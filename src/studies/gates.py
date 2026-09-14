"""Gate status computed from a study's artifacts.

The program's gates (G0 kickoff … G4 evidence package) are checklists in a
markdown plan. This module turns the mechanically checkable parts of each
gate into checks that run against the study directory, so the same board can
be printed by the CLI, served by the API, and shown in the app. Human
rulings (PI verdicts, manuscript) stay human; what is checked here is only
what an artifact can prove.

Each check: ``{"id", "label", "ok", "detail", "hint"}``. A gate is ok when
every check is ok. ``current`` is the first gate that is not ok.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path

from studies import guards, registration
from studies.protocol import FrozenProtocol, verify_protocol

try:  # optional: only LLM studies use it
    from studies import llm as _llm
except Exception:  # pragma: no cover
    _llm = None

PLACEHOLDER_RE = re.compile(r"\[OPEN|\[PI ruling\]|\[after pilot\]|\[PI to confirm\]|_{6,}")

DRAFT_FILES = (
    "registration/osf-registration-v2-draft.md",
    "registration/osf-registration-draft.md",
)
FROZEN_FILE = "registration/protocol-frozen.json"
RESULTS_FILE = "output/confirmatory-results.json"
RUN_LOG_FILE = "output/confirmatory-run-log.json"
VERIFICATION_FILE = "output/verification.json"
EVIDENCE_FILE = "output/evidence-manifest.json"
VALIDATION_FILE = "config/measure-validation.json"
EXPLORATORY_GENERATOR = "output/exploratory-generator.json"
CONFIRMATORY_GENERATOR = "output/confirmatory-generator.json"
SPEND_LEDGER = "output/spend-ledger.jsonl"
ATTESTATIONS_DIR = "output/attestations"
PILOT_FILES = ("output/pilot-results.json", "output/exploratory-results.json")
MIN_PILOT_N = 10


def _generation_config(root: Path) -> dict | None:
    cfg = _load(root / "config" / "generation.json")
    return cfg if isinstance(cfg, dict) and cfg.get("model") else None


def _analysis_config(root: Path) -> dict | None:
    cfg = _load(root / "config" / "analysis.json")
    return cfg if isinstance(cfg, dict) else None


def declared_conditions(root: Path) -> list[str]:
    a = _analysis_config(root)
    if a and a.get("conditions"):
        return [str(c) for c in a["conditions"]]
    g = _generation_config(root)
    if g and g.get("conditions"):
        return [c if isinstance(c, str) else str(c.get("name") or c.get("id") or c) for c in g["conditions"]]
    return []


def measure_names(root: Path) -> list[str]:
    a = _analysis_config(root)
    if a and a.get("dimensions"):
        return [str(d.get("name")) for d in a["dimensions"] if d.get("name")]
    return [m["name"] for m in registration.collect_facts(root)["measures"] if m.get("name")]


def measure_declarations(root: Path) -> dict[str, dict]:
    """Per measure: units/direction/kind/validation, merged from analysis.json and measure-validation.json."""
    out: dict[str, dict] = {}
    a = _analysis_config(root)
    if a:
        for d in a.get("dimensions", []):
            if d.get("name"):
                out[str(d["name"])] = {k: d.get(k) for k in ("units", "direction", "kind")}
    v = _load(root / VALIDATION_FILE)
    if isinstance(v, dict):
        for name, entry in (v.get("measures") or {}).items():
            out.setdefault(str(name), {}).update({k: entry[k] for k in ("units", "direction", "kind", "precision", "recall", "against", "n") if entry.get(k) is not None})
    return out


def validation_floors(root: Path) -> dict:
    v = _load(root / VALIDATION_FILE)
    floors = (v or {}).get("floors") if isinstance(v, dict) else None
    return floors if isinstance(floors, dict) else {"precision": 0.8, "recall": 0.8}


def pilot_coverage(root: Path) -> dict[str, int]:
    """Smallest per-condition n across dimensions in the pilot/exploratory results, per condition."""
    for rel in PILOT_FILES:
        res = _load(root / rel)
        if isinstance(res, dict) and isinstance(res.get("dimensions"), dict):
            cover: dict[str, int] = {}
            for dim in res["dimensions"].values():
                for cond, r in (dim.get("conditions") or {}).items():
                    n = int(r.get("n", 0) or 0)
                    cover[cond] = min(cover.get(cond, n), n)
            return cover
    return {}


def attestations(root: Path) -> list[dict]:
    d = root / ATTESTATIONS_DIR
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        a = _load(p)
        if isinstance(a, dict):
            out.append(a)
    return out


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def registration_draft_path(study_root: Path) -> Path | None:
    for rel in DRAFT_FILES:
        p = study_root / rel
        if p.exists():
            return p
    return None


def open_placeholders(text: str) -> list[str]:
    return sorted({m.group(0) for m in PLACEHOLDER_RE.finditer(text)})


def load_frozen(study_root: Path) -> FrozenProtocol | None:
    data = _load(study_root / FROZEN_FILE)
    if not isinstance(data, dict):
        return None
    try:
        return FrozenProtocol(**data)
    except TypeError:
        return None


def _check(cid: str, label: str, ok: bool, detail: str = "", hint: str = "") -> dict:
    return {"id": cid, "label": label, "ok": bool(ok), "detail": detail, "hint": hint}


def study_status(repo_root: str | Path, study_root: str | Path) -> dict:
    repo_root = Path(repo_root)
    root = Path(study_root)
    sid = root.name
    fence_state = _load(root / "fence" / "state.json")
    log = fence_state.get("access_log", []) if isinstance(fence_state, dict) else []
    guard_report = guards.run_guards(repo_root, root)
    protection = guards.load_protection(root)
    gates: list[dict] = []

    # ------------------------------------------------------------- G0
    g0 = []
    terms = root / "acquisition" / "access-terms.md"
    receipt = root / "acquisition" / "receipt.json"  # pre-kernel studies recorded acquisition here
    terms_ok = (terms.exists() and terms.stat().st_size > 200) or receipt.exists()
    g0.append(_check("terms", "Data access terms filed", terms_ok,
                     "acquisition/access-terms.md" if terms.exists() else ("acquisition/receipt.json (pre-kernel)" if receipt.exists() else "missing"),
                     "write acquisition/access-terms.md: dataset, license, terms verbatim, file hashes"))
    have_manifests = all((root / "fence" / f"{p}-manifest.json").exists() for p in ("exploratory", "confirmatory"))
    legacy_split = (root / "fence" / "split-report.json").exists()
    g0.append(_check("split", "Fenced split created", have_manifests or legacy_split,
                     "manifests present" if have_manifests else ("fence/split-report.json (pre-kernel split)" if legacy_split else "no fence manifests"),
                     f"yardstick split {sid} --csv <raw file> --id-column <col> --seed <seed>"))
    negative = any(e.get("partition") == "confirmatory" and not e.get("allowed") for e in log)
    g0.append(_check("negative-test", "Fence negative test recorded", negative,
                     "a refused confirmatory read is in the access log" if negative else "no refused confirmatory read logged",
                     "run the fence negative test (yardstick split does this)"))
    g0.append(_check("protected", "Raw files declared in fence/protected-files.json",
                     bool(protection and protection.get("protected_filenames")),
                     ", ".join(protection.get("protected_filenames", [])) if protection else "missing",
                     "list every raw data file under protected_filenames"))
    readers = bool(protection and protection.get("allowed_readers"))
    g0.append(_check("data-access", "Single data-access module declared (allowed_readers)", readers,
                     ", ".join(protection.get("allowed_readers", [])) if protection else "missing",
                     "route all raw reads through one module using studies.access.FencedDataset and list it in allowed_readers"))
    gates.append({"gate": "G0", "title": "Kickoff", "checks": g0})

    # ------------------------------------------------------------- G1
    g1 = []
    g1.append(_check("guards", "No new fence-guard violations", guard_report.get("ok", False),
                     f"{len(guard_report.get('direct_reads_new', []))} new direct reads, "
                     f"{len(guard_report.get('tracked_restricted_new', []))} new restricted files",
                     f"yardstick check {sid}"))
    facts_ok, facts_msg = registration.check(root)
    g1.append(_check("facts", "registration/facts.md current", facts_ok, facts_msg, f"yardstick facts {sid}"))
    g1.append(_check("schema", "config/results-schema.json present", (root / "config" / "results-schema.json").exists(),
                     "", "name every registered quantity in the schema before any number exists"))
    g1.append(_check("figures", "registration/figure-list.md present", (root / "registration" / "figure-list.md").exists(),
                     "", "list the figures that will render from the results file"))
    g1.append(_check("deviations", "deviations-log.md present", (root / "deviations-log.md").exists(),
                     "", "an empty log is a valid entry; an absent log is not"))
    names = measure_names(root)
    decls = measure_declarations(root)
    def _declared(v) -> bool:
        return bool(v) and "[OPEN" not in str(v)
    undeclared = [n for n in names if not (_declared(decls.get(n, {}).get("units")) and _declared(decls.get(n, {}).get("direction")))]
    g1.append(_check("units-direction", "Units and direction declared for every measure", bool(names) and not undeclared,
                     ("missing: " + ", ".join(undeclared[:6])) if undeclared else f"{len(names)} measure(s)",
                     "declare units and direction (e.g. 'higher = more questions') per dimension in config/analysis.json or config/measure-validation.json"))
    floors = validation_floors(root)
    unvalidated = []
    for n in names:
        d = decls.get(n, {})
        if d.get("kind") == "direct":
            continue  # a directly computed quantity (a count, a mean length) is not a classifier
        p, r = d.get("precision"), d.get("recall")
        if p is None or r is None or p < floors.get("precision", 0.8) or r < floors.get("recall", 0.8):
            unvalidated.append(f"{n}" + (f" (p={p}, r={r})" if p is not None or r is not None else ""))
    g1.append(_check("measure-validation", f"Classifier-type measures validated against a reference at or above the registered floor (precision ≥ {floors.get('precision', 0.8)}, recall ≥ {floors.get('recall', 0.8)})",
                     bool(names) and not unvalidated,
                     ("below floor or unvalidated: " + ", ".join(unvalidated[:6])) if unvalidated else "all validated or direct",
                     "record precision/recall against a human or ground-truth reference per measure in config/measure-validation.json, or mark direct quantities kind='direct'"))
    gen = _generation_config(root)
    if gen:
        exp_gen = _load(root / EXPLORATORY_GENERATOR)
        g1.append(_check("exploratory-generator", "Exploratory generator recorded (model, parameters, template and prompt hashes)",
                         isinstance(exp_gen, dict) and bool(exp_gen.get("model")),
                         f"{exp_gen.get('generator')}/{exp_gen.get('model')}" if isinstance(exp_gen, dict) else "no output/exploratory-generator.json",
                         "write output/exploratory-generator.json from studies.llm.generator_record at the end of the exploratory pass"))
    gates.append({"gate": "G1", "title": "Exploratory complete", "checks": g1})

    # ------------------------------------------------------------- G2
    g2 = []
    draft = registration_draft_path(root)
    placeholders = open_placeholders(draft.read_text(encoding="utf-8")) if draft else ["no draft"]
    g2.append(_check("draft", "Registration text has no open slots", bool(draft) and not placeholders,
                     ", ".join(placeholders) if placeholders else str(draft.relative_to(root)),
                     "fill every [PI ruling] / [OPEN] / [after pilot] slot in the registration draft"))
    frozen = load_frozen(root)
    frozen_ok = frozen is not None and verify_protocol(frozen)["valid"]
    g2.append(_check("frozen", "Protocol frozen and hash-verified", frozen_ok,
                     f"{frozen.version} at {frozen.frozen_at}" if frozen else "not frozen",
                     f"yardstick freeze {sid} --version v1.0"))
    locked_at_freeze = True
    if frozen and isinstance(fence_state, dict) and fence_state.get("unlocked_at"):
        locked_at_freeze = fence_state["unlocked_at"] > frozen.frozen_at
    g2.append(_check("locked-at-freeze", "Fence was locked when the protocol froze", bool(frozen) and locked_at_freeze,
                     "" if locked_at_freeze else "unlocked before freeze", "never unlock before freezing"))
    pre_unlock_reads = [
        e for e in log
        if e.get("partition") == "confirmatory" and e.get("allowed") and not str(e.get("reason", "")).startswith("UNLOCK")
        and (not isinstance(fence_state, dict) or not fence_state.get("unlocked_at") or e.get("timestamp", "") < fence_state["unlocked_at"])
    ]
    g2.append(_check("no-early-reads", "Zero confirmatory reads before unlock", not pre_unlock_reads,
                     f"{len(pre_unlock_reads)} allowed confirmatory read(s) before unlock",
                     "file a deviations-log entry and investigate the read path"))
    conds = declared_conditions(root)
    if conds:
        cover = pilot_coverage(root)
        missing = [c for c in conds if cover.get(c, 0) < MIN_PILOT_N]
        g2.append(_check("pilot-every-arm", f"Pilot or exploratory data on every registered condition (n ≥ {MIN_PILOT_N})", not missing,
                         ("missing: " + ", ".join(missing)) if missing else ", ".join(f"{c}: n={cover[c]}" for c in conds),
                         "run the pilot on every condition and write output/pilot-results.json (or exploratory-results.json) before freezing"))
    if gen and _llm is not None:
        pin = _llm.ModelPin.from_generation_config(gen)
        chk = pin.check()
        g2.append(_check("model-pin", "Pinned model has a recorded retirement date in the future", chk["ok"], chk["detail"],
                         "record model_retirement.retirement_date (ISO) from the provider's deprecation page in config/generation.json"))
    gates.append({"gate": "G2", "title": "Registration freeze", "checks": g2})

    # ------------------------------------------------------------- G3
    g3 = []
    unlocked = isinstance(fence_state, dict) and not fence_state.get("confirmatory_locked") and fence_state.get("unlock_reason")
    g3.append(_check("unlocked", "Fence unlocked by a logged action", bool(unlocked),
                     fence_state.get("unlock_reason", "") if isinstance(fence_state, dict) else "",
                     f"yardstick unlock {sid} --reason \"registration frozen: <tag>\""))
    results = root / RESULTS_FILE
    schema = root / "config" / "results-schema.json"
    if results.exists() and schema.exists():
        try:
            errors = guards.validate_results(results, schema)
        except RuntimeError as e:
            errors = [str(e)]
        g3.append(_check("results", "Results file validates against the frozen schema", not errors,
                         "; ".join(errors[:3]) if errors else str(results.relative_to(root)),
                         f"yardstick verify {sid}"))
    else:
        g3.append(_check("results", "Results file validates against the frozen schema", False,
                         "no results file" if not results.exists() else "no schema", "run the confirmatory script from the tag"))
    run_log = _load(root / RUN_LOG_FILE)
    real_run = isinstance(run_log, dict) and not run_log.get("dry_run") and run_log.get("partition", "confirmatory") == "confirmatory"
    g3.append(_check("run-log", "Run log from a real confirmatory run", bool(real_run),
                     ("dry run" if isinstance(run_log, dict) and run_log.get("dry_run") else
                      f"partition={run_log.get('partition')}" if isinstance(run_log, dict) else "no run log"),
                     "the runner writes output/confirmatory-run-log.json with dry_run=false and partition=confirmatory"))
    if gen and _llm is not None:
        exp_gen, conf_gen = _load(root / EXPLORATORY_GENERATOR), _load(root / CONFIRMATORY_GENERATOR)
        if isinstance(exp_gen, dict) and isinstance(conf_gen, dict):
            drift = _llm.generator_drift(exp_gen, conf_gen)
            g3.append(_check("generator-drift", "Confirmatory generator identical to the exploratory generator", drift["ok"],
                             ("differs in " + ", ".join(drift["differences"])) if not drift["ok"] else "no drift",
                             "re-run the exploratory/pilot pass on the registered generator, or amend the registration"))
        else:
            g3.append(_check("generator-drift", "Confirmatory generator identical to the exploratory generator", False,
                             "generator records missing", "write output/exploratory-generator.json and output/confirmatory-generator.json from studies.llm.generator_record"))
        cap = gen.get("cost_cap_usd")
        ledger = root / SPEND_LEDGER
        if ledger.exists():
            total = 0.0
            for line in ledger.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    total += float(json.loads(line).get("usd", 0.0))
            within = cap is None or total <= float(cap)
            g3.append(_check("spend-cap", "Spend within the registered cap", within, f"{total:.2f} USD of cap {cap}",
                             "the runner must stop at the cap with a logged reason"))
        else:
            g3.append(_check("spend-cap", "Spend within the registered cap", False, "no spend ledger",
                             "price every call with studies.llm.SpendLedger into output/spend-ledger.jsonl"))
    ver = _load(root / VERIFICATION_FILE)
    det_ok = isinstance(ver, dict) and ver.get("schema_valid") and (ver.get("determinism") or {}).get("identical") is True
    g3.append(_check("determinism", "Determinism proof recorded", bool(det_ok),
                     "" if det_ok else "no verification with an identical rerun",
                     f"yardstick verify {sid} --compare <results from a clean rerun>"))
    gates.append({"gate": "G3", "title": "Confirmatory run", "checks": g3})

    # ------------------------------------------------------------- G4
    g4 = []
    g4.append(_check("replay", "REPLAY.md present", (root / "REPLAY.md").exists(), "", "write the clone-to-identical-numbers instructions"))
    restricted_total = guard_report.get("tracked_restricted_total", 0)
    g4.append(_check("pointer-only", "No restricted content tracked (baseline does not count here)", restricted_total == 0,
                     f"{restricted_total} restricted file(s) tracked", "purge restricted files from history; keep pointers and hashes"))
    ev = _load(root / EVIDENCE_FILE)
    g4.append(_check("evidence", "Evidence manifest built", isinstance(ev, dict) and bool(ev.get("files")),
                     f"{len(ev.get('files', {}))} files" if isinstance(ev, dict) else "", f"yardstick package {sid}"))
    atts = attestations(root)
    independent = [a for a in atts if a.get("identical") and a.get("runner", {}).get("kind") != "author"]
    g4.append(_check("independent-replay", "At least one independent replay attestation (identical)", bool(independent),
                     f"{len(independent)} independent of {len(atts)} attestation(s)",
                     "have a stranger or the replay workflow run: yardstick attest <id> --results <their file>"))
    gates.append({"gate": "G4", "title": "Evidence package", "checks": g4})

    for g in gates:
        g["ok"] = all(c["ok"] for c in g["checks"])
    current = next((g["gate"] for g in gates if not g["ok"]), "done")
    return {"study_id": sid, "current": current, "gates": gates}


def render_status(status: dict) -> str:
    lines = [f"{status['study_id']} — current gate: {status['current']}"]
    for g in status["gates"]:
        mark = "✓" if g["ok"] else "✗"
        lines.append(f"\n{mark} {g['gate']} {g['title']}")
        for c in g["checks"]:
            m = "✓" if c["ok"] else "✗"
            detail = f" — {c['detail']}" if c["detail"] else ""
            lines.append(f"   {m} {c['label']}{detail}")
            if not c["ok"] and c["hint"]:
                lines.append(f"       → {c['hint']}")
    return "\n".join(lines)
