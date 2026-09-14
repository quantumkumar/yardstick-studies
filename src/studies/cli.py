"""yardstick — one command for the study lifecycle.

    yardstick init <id> --title "..." [--llm] [--template paired-equivalence|paired-comparison]   G0
    yardstick analyze <id> --partition exploratory|confirmatory [--input f] [--commit-tag t]    template analysis
    yardstick split <id> --csv <file> --id-column <col> --seed <n> [--fraction 0.5] [--exclude-file f]
                                                            G0  fenced split + negative test
    yardstick facts <id>                                    G1  regenerate registration/facts.md
    yardstick check <id>                                    G1  guards + facts check
    yardstick status <id> [--json]                          all gates, with hints
    yardstick freeze <id> --version v1.0                    G2  freeze the registration text
    yardstick unlock <id> --reason "..."                    G3  lift the fence (logged)
    yardstick verify <id> [--results f] [--compare f2]      G3  schema validation + determinism
    yardstick package <id>                                  G4  evidence manifest (pointer-only)
    yardstick replay <id> --results <file>                  G4  compare a replay to the frozen results
    yardstick attest <id> --results <file> [--runner-kind ci|independent|author]   G4  replay attestation
    yardstick pressure <id> [--results f --rerun f2] [--counts f]   pressure-test report

Every verb prints what it did and, on failure, what to do. ``--studies-root``
(or ``YARDSTICK_STUDIES_ROOT``) points at a studies directory other than the
repository's.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
from datetime import datetime, timezone
from dataclasses import asdict
from pathlib import Path

from studies import fence, gates, guards, paired_analysis, pressure, registration, scaffold, templates
from studies.osf_export import export_registration, to_markdown
from studies.protocol import freeze_protocol


class CliError(SystemExit):
    def __init__(self, message: str, code: int = 1):
        print(f"error: {message}", file=sys.stderr)
        super().__init__(code)


# ----------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------


def studies_root() -> Path:
    return fence._studies_root()


def repo_root() -> Path:
    return studies_root().parent


def study_root(study_id: str, must_exist: bool = True) -> Path:
    root = studies_root() / study_id
    if must_exist and not root.exists():
        raise CliError(f"no study at {root}; run: yardstick init {study_id} --title ...")
    return root


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# ----------------------------------------------------------------------
# Verbs
# ----------------------------------------------------------------------


def cmd_init(args) -> int:
    root = scaffold.create_study(studies_root(), args.study_id, args.title, llm=args.llm, template=args.template)
    print(f"created {root}" + (f" (template: {args.template})" if args.template else ""))
    print(f"next: yardstick split {args.study_id} --csv <raw file> --id-column <col> --seed <seed>")
    return 0


def cmd_split(args) -> int:
    root = study_root(args.study_id)
    exclude = []
    if args.exclude_file:
        exclude = [l.strip() for l in Path(args.exclude_file).read_text().splitlines() if l.strip()]
    if args.csv:
        from studies.access import unique_ids_from_csv

        ids = unique_ids_from_csv(args.csv, args.id_column)
        raw_name = Path(args.csv).name
    elif args.ids_file:
        ids = [l.strip() for l in Path(args.ids_file).read_text().splitlines() if l.strip()]
        raw_name = Path(args.ids_file).name
    else:
        raise CliError("give --csv <file> --id-column <col> or --ids-file <file>")
    if len(ids) < 4:
        raise CliError(f"only {len(ids)} ids; refusing to split")

    exp, conf = fence.create_split(args.study_id, ids, seed=args.seed, exploratory_fraction=args.fraction, exclude_ids=exclude)

    # Negative test: a confirmatory read must be refused and logged.
    allowed = fence.request_access(args.study_id, "confirmatory", "negative test: confirmatory read must be refused")
    if allowed:
        raise CliError("fence negative test FAILED: confirmatory read was allowed right after the split")
    fence.request_access(args.study_id, "exploratory", "negative test: exploratory read must be allowed")

    # Declare the raw file as protected.
    prot_path = root / guards.PROTECTION_FILE
    prot = guards.load_protection(root) or {"protected_filenames": [], "allowed_readers": [], "restricted_content_globs": []}
    if raw_name not in prot["protected_filenames"]:
        prot["protected_filenames"].append(raw_name)
    _write_json(prot_path, prot)

    print(f"split {args.study_id}: exploratory {exp.row_count} rows ({exp.manifest_hash[:12]}…), "
          f"confirmatory {conf.row_count} rows ({conf.manifest_hash[:12]}…), seed {args.seed}, "
          f"{len(exclude)} excluded")
    print("negative test: confirmatory read refused and logged")
    print(f"declared {raw_name} in {prot_path.relative_to(root)}")
    registration.build(root)
    print("next: read data only through studies.access.FencedDataset; then yardstick check")
    return 0


def cmd_analyze(args) -> int:
    root = study_root(args.study_id)
    if not (root / "config" / "analysis.json").exists():
        raise CliError("no config/analysis.json; this study was not created from a template (or add the file by hand)")
    if args.partition == "confirmatory" and fence.is_confirmatory_locked(args.study_id):
        raise CliError("the fence is locked; a confirmatory analysis before unlock is refused")
    results, errors = paired_analysis.run(root, args.partition, args.input, args.commit_tag)
    if errors:
        raise CliError("results do not match config/results-schema.json: " + "; ".join(errors[:3]))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {out}")
    else:
        out = paired_analysis.write_results(root, args.partition, results)
        print(f"wrote {out.relative_to(root)}")
    print(json.dumps(results["summary"], indent=2))
    if args.partition == "confirmatory":
        print(f"next: rerun on a clean checkout and: yardstick verify {args.study_id} --compare <that file>")
    return 0


def cmd_facts(args) -> int:
    root = study_root(args.study_id)
    registration.build(root)
    print(f"wrote {root / registration.FACTS_FILE}")
    return 0


def cmd_check(args) -> int:
    root = study_root(args.study_id)
    report = guards.run_guards(repo_root(), root)
    ok_facts, msg = registration.check(root)
    print(json.dumps({"guards": report, "facts": msg}, indent=2))
    if not report.get("ok"):
        print("guards: FAIL — new fence violations (see direct_reads_new / tracked_restricted_new)")
    if not ok_facts:
        print(f"facts: FAIL — run: yardstick facts {args.study_id}")
    return 0 if report.get("ok") and ok_facts else 1


def cmd_status(args) -> int:
    root = study_root(args.study_id)
    status = gates.study_status(repo_root(), root)
    if args.json:
        print(json.dumps(status, indent=2))
    else:
        print(gates.render_status(status))
    return 0


def cmd_freeze(args) -> int:
    root = study_root(args.study_id)
    draft = gates.registration_draft_path(root)
    if draft is None:
        raise CliError("no registration draft found under registration/")
    text = draft.read_text(encoding="utf-8")
    holes = gates.open_placeholders(text)
    if holes and not args.allow_open:
        raise CliError(f"registration text still has open slots: {', '.join(holes)} (fill them, or --allow-open for a rehearsal freeze)")
    ok_facts, msg = registration.check(root)
    if not ok_facts:
        raise CliError(f"{msg}; run: yardstick facts {args.study_id}")
    report = guards.run_guards(repo_root(), root)
    if not report.get("ok"):
        raise CliError("guards report new violations; run: yardstick check")
    if not fence.is_confirmatory_locked(args.study_id):
        raise CliError("the fence is already unlocked; a freeze after unlock is not a pre-registration")
    status = gates.study_status(repo_root(), root)
    blocking = [c for g in status["gates"] if g["gate"] in ("G1", "G2")
                for c in g["checks"] if not c["ok"] and c["id"] in ("units-direction", "measure-validation", "pilot-every-arm", "model-pin", "exploratory-generator")]
    if blocking and not args.allow_open:
        raise CliError("freeze refused; unmet before registration: " + "; ".join(f"{c['label']} ({c['detail']})" for c in blocking)
                       + ". Fix them, or --allow-open for a rehearsal freeze.")

    schema = root / "config" / "results-schema.json"
    content = {
        "study_id": args.study_id,
        "registration_file": str(draft.relative_to(root)),
        "registration_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "registration_markdown": text,
        "facts_sha256": _sha256_file(root / registration.FACTS_FILE),
        "results_schema_sha256": _sha256_file(schema) if schema.exists() else None,
        "fence_manifests": {
            p: asdict(fence._read_manifest(args.study_id, p)) for p in ("exploratory", "confirmatory")
        },
        "rehearsal": bool(holes),
    }
    title = args.title or f"{args.study_id} registration"
    frozen = freeze_protocol(title=title, version=args.version, content=content)
    _write_json(root / gates.FROZEN_FILE, asdict(frozen))
    manifest = export_registration(frozen)
    (root / "registration" / "osf-export.md").write_text(to_markdown(manifest), encoding="utf-8")
    print(f"frozen {args.study_id} {args.version}: content hash {frozen.content_hash[:16]}… at {frozen.frozen_at}")
    print(f"wrote {gates.FROZEN_FILE} and registration/osf-export.md")
    if holes:
        print("NOTE: rehearsal freeze — open slots remain; this is not a filing copy")
    print(f"next: tag the repository, file on OSF, then: yardstick unlock {args.study_id} --reason \"registration frozen: <tag>\"")
    return 0


def cmd_unlock(args) -> int:
    root = study_root(args.study_id)
    if gates.load_frozen(root) is None and not args.force:
        raise CliError("no frozen protocol; freeze before unlocking (or --force to record a deviation deliberately)")
    fence.unlock_confirmatory(args.study_id, args.reason)
    print(f"unlocked {args.study_id}: {args.reason}")
    print("next: run the confirmatory script from the tag, then: yardstick verify")
    return 0


def cmd_verify(args) -> int:
    root = study_root(args.study_id)
    results = Path(args.results) if args.results else root / gates.RESULTS_FILE
    schema = root / "config" / "results-schema.json"
    if not results.exists():
        raise CliError(f"no results file at {results}")
    if not schema.exists():
        raise CliError(f"no schema at {schema}")
    errors = guards.validate_results(results, schema)
    record = {
        "results_file": str(results),
        "results_sha256": _sha256_file(results),
        "schema_sha256": _sha256_file(schema),
        "schema_valid": not errors,
        "schema_errors": errors[:20],
        "determinism": None,
    }
    if args.compare:
        record["determinism"] = pressure.determinism(results, args.compare)
    _write_json(root / gates.VERIFICATION_FILE, record)
    print(f"schema: {'valid' if not errors else 'INVALID — ' + '; '.join(errors[:3])}")
    if record["determinism"]:
        print(f"determinism: {'identical' if record['determinism']['identical'] else 'DIFFERENT'} ({record['determinism']['sha256_a'][:12]}… vs {record['determinism']['sha256_b'][:12]}…)")
    else:
        print("determinism: not checked — rerun on a clean checkout and pass --compare <that file>")
    print(f"wrote {gates.VERIFICATION_FILE}")
    return 0 if not errors and (record["determinism"] is None or record["determinism"]["identical"]) else 1


def _tracked_or_all(root: Path) -> list[str]:
    tracked = guards.git_tracked_files(repo_root(), root)
    if tracked is not None:
        return tracked
    out = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and p.name != ".DS_Store":
            out.append(p.relative_to(root).as_posix())
    return out


def cmd_package(args) -> int:
    root = study_root(args.study_id)
    protection = guards.load_protection(root) or {}
    files = _tracked_or_all(root)
    restricted = guards.find_tracked_restricted(files, protection)
    if restricted:
        raise CliError(f"{len(restricted)} restricted file(s) would enter the package (e.g. {restricted[0]}); "
                       "the evidence package is pointer-only. Remove them from the tree and history first.")
    manifest = {
        "study_id": args.study_id,
        "files": {rel: _sha256_file(root / rel) for rel in files if (root / rel).is_file()},
        "results_sha256": _sha256_file(root / gates.RESULTS_FILE) if (root / gates.RESULTS_FILE).exists() else None,
        "protocol_content_hash": (gates.load_frozen(root).content_hash if gates.load_frozen(root) else None),
        "commit": _git_commit(),
    }
    _write_json(root / gates.EVIDENCE_FILE, manifest)
    print(f"evidence manifest: {len(manifest['files'])} files, results {str(manifest['results_sha256'])[:12]}…, commit {manifest['commit']}")
    print(f"wrote {gates.EVIDENCE_FILE}")
    return 0


def _git_commit() -> str | None:
    try:
        return subprocess.run(["git", "-C", str(repo_root()), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None


def cmd_replay(args) -> int:
    root = study_root(args.study_id)
    frozen_results = root / gates.RESULTS_FILE
    if not frozen_results.exists():
        raise CliError(f"no frozen results at {frozen_results}")
    rep = pressure.determinism(frozen_results, args.results)
    _write_json(root / "output" / "replay-report.json", {"replayed_file": str(args.results), **rep})
    print("replay: IDENTICAL — the empty diff is the certificate" if rep["identical"] else
          f"replay: DIFFERENT — {rep['sha256_a'][:12]}… (frozen) vs {rep['sha256_b'][:12]}… (replay)")
    return 0 if rep["identical"] else 1


def cmd_attest(args) -> int:
    """Record a replay attestation: who ran it, on what, and whether the bytes matched."""
    root = study_root(args.study_id)
    frozen_results = root / gates.RESULTS_FILE
    if not frozen_results.exists():
        raise CliError(f"no frozen results at {frozen_results}")
    rep = pressure.determinism(frozen_results, args.results)
    now = datetime.now(timezone.utc)
    runner = {
        "kind": args.runner_kind,
        "name": args.runner_name or os.environ.get("GITHUB_ACTOR") or os.environ.get("USER") or "unknown",
        "host": os.environ.get("GITHUB_REPOSITORY") or socket.gethostname(),
        "ci_run": os.environ.get("GITHUB_RUN_ID"),
        "workflow": os.environ.get("GITHUB_WORKFLOW"),
    }
    att = {
        "study_id": args.study_id,
        "attested_at": now.isoformat(),
        "runner": runner,
        "commit": _git_commit(),
        "ref": args.ref or os.environ.get("GITHUB_REF_NAME"),
        "frozen_results_sha256": rep["sha256_a"],
        "replay_results_sha256": rep["sha256_b"],
        "identical": rep["identical"],
        "note": args.note or "",
    }
    att["attestation_sha256"] = hashlib.sha256(json.dumps(att, sort_keys=True).encode("utf-8")).hexdigest()
    name = f"{now.strftime('%Y%m%dT%H%M%SZ')}-{rep['sha256_b'][:8]}-{args.runner_kind}.json"
    _write_json(root / gates.ATTESTATIONS_DIR / name, att)
    print(("IDENTICAL" if rep["identical"] else "DIFFERENT") + f" — attestation written: {gates.ATTESTATIONS_DIR}/{name} (runner: {runner['kind']}, {runner['name']})")
    return 0 if rep["identical"] else 1


def cmd_pressure(args) -> int:
    root = study_root(args.study_id)
    dims = None
    if args.dimensions:
        dims = json.loads(Path(args.dimensions).read_text())
    report = pressure.run_pressure(root, args.results, args.rerun, args.counts, dims)
    _write_json(root / "output" / "pressure-report.json", report)
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


# ----------------------------------------------------------------------
# Parser
# ----------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="yardstick", description="Study lifecycle, gate by gate.")
    ap.add_argument("--studies-root", help="studies directory (default: the repository's studies/)")
    sub = ap.add_subparsers(dest="verb", required=True)

    p = sub.add_parser("init", help="G0: scaffold a study"); p.add_argument("study_id"); p.add_argument("--title", required=True); p.add_argument("--llm", action="store_true"); p.add_argument("--template", choices=templates.TEMPLATE_NAMES); p.set_defaults(fn=cmd_init)
    p = sub.add_parser("analyze", help="run the template analysis on the per-unit table"); p.add_argument("study_id"); p.add_argument("--partition", required=True, choices=["exploratory", "confirmatory"]); p.add_argument("--input"); p.add_argument("--commit-tag", default="unknown"); p.add_argument("--out", help="write here instead of the study's results file (replays)"); p.set_defaults(fn=cmd_analyze)
    p = sub.add_parser("attest", help="G4: record a replay attestation (who, what, identical or not)"); p.add_argument("study_id"); p.add_argument("--results", required=True); p.add_argument("--runner-kind", choices=["author", "independent", "ci"], default="independent"); p.add_argument("--runner-name"); p.add_argument("--ref"); p.add_argument("--note"); p.set_defaults(fn=cmd_attest)
    p = sub.add_parser("split", help="G0: fenced split + negative test"); p.add_argument("study_id"); p.add_argument("--csv"); p.add_argument("--id-column"); p.add_argument("--ids-file"); p.add_argument("--seed", type=int, required=True); p.add_argument("--fraction", type=float, default=0.5); p.add_argument("--exclude-file"); p.set_defaults(fn=cmd_split)
    p = sub.add_parser("facts", help="G1: regenerate registration/facts.md"); p.add_argument("study_id"); p.set_defaults(fn=cmd_facts)
    p = sub.add_parser("check", help="G1: guards + facts check"); p.add_argument("study_id"); p.set_defaults(fn=cmd_check)
    p = sub.add_parser("status", help="gate board"); p.add_argument("study_id"); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_status)
    p = sub.add_parser("freeze", help="G2: freeze the registration text"); p.add_argument("study_id"); p.add_argument("--version", required=True); p.add_argument("--title"); p.add_argument("--allow-open", action="store_true", help="rehearsal freeze with open slots"); p.set_defaults(fn=cmd_freeze)
    p = sub.add_parser("unlock", help="G3: lift the fence (logged)"); p.add_argument("study_id"); p.add_argument("--reason", required=True); p.add_argument("--force", action="store_true"); p.set_defaults(fn=cmd_unlock)
    p = sub.add_parser("verify", help="G3: validate results; determinism with --compare"); p.add_argument("study_id"); p.add_argument("--results"); p.add_argument("--compare"); p.set_defaults(fn=cmd_verify)
    p = sub.add_parser("package", help="G4: evidence manifest (pointer-only)"); p.add_argument("study_id"); p.set_defaults(fn=cmd_package)
    p = sub.add_parser("replay", help="G4: compare a replay to the frozen results"); p.add_argument("study_id"); p.add_argument("--results", required=True); p.set_defaults(fn=cmd_replay)
    p = sub.add_parser("pressure", help="pressure-test report"); p.add_argument("study_id"); p.add_argument("--results"); p.add_argument("--rerun"); p.add_argument("--counts"); p.add_argument("--dimensions"); p.set_defaults(fn=cmd_pressure)
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.studies_root:
        os.environ["YARDSTICK_STUDIES_ROOT"] = str(Path(args.studies_root).resolve())
    try:
        return int(args.fn(args) or 0)
    except CliError as e:
        return int(e.code)


if __name__ == "__main__":
    sys.exit(main())
