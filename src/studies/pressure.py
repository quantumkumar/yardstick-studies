"""Pressure-test checks as code.

The program's pressure-test is a checklist run by a reviewer after the
confirmatory run. The parts an artifact can prove are here:

* :func:`leakage_audit` — the fence log shows no allowed confirmatory read
  before the logged unlock.
* :func:`determinism` — two results files (a run and a clean rerun) are
  byte-identical.
* :func:`reconciliation` — every count that appears in more than one stage
  agrees with itself.
* :func:`membership_audit` — every id in a sample belongs to the partition
  it claims.
* :func:`effect_plausibility` — results too clean get flagged like results
  too messy: zero spread, tiny n, or effects far outside the bound.

:func:`run_pressure` bundles them into one report.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable, Mapping


def _sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def leakage_audit(fence_state: Mapping) -> dict:
    """Allowed confirmatory reads that precede the unlock (or any, if never unlocked)."""
    unlocked_at = fence_state.get("unlocked_at")
    early = []
    for e in fence_state.get("access_log", []):
        if e.get("partition") != "confirmatory" or not e.get("allowed"):
            continue
        if str(e.get("reason", "")).startswith("UNLOCK"):
            continue
        if not unlocked_at or e.get("timestamp", "") < unlocked_at:
            early.append(e)
    return {
        "check": "leakage",
        "ok": not early,
        "unlocked_at": unlocked_at,
        "early_confirmatory_reads": early,
        "confirmatory_reads_denied": sum(
            1 for e in fence_state.get("access_log", []) if e.get("partition") == "confirmatory" and not e.get("allowed")
        ),
    }


def determinism(results_a: str | Path, results_b: str | Path) -> dict:
    ha, hb = _sha256(results_a), _sha256(results_b)
    return {"check": "determinism", "ok": ha == hb, "sha256_a": ha, "sha256_b": hb, "identical": ha == hb}


def reconciliation(counts: Mapping[str, Mapping[str, int]]) -> dict:
    """``{"quantity": {"stage": value, ...}}`` — flag quantities whose stages disagree."""
    mismatches = {}
    for q, stages in counts.items():
        vals = {k: v for k, v in stages.items() if v is not None}
        if len(set(vals.values())) > 1:
            mismatches[q] = vals
    return {"check": "reconciliation", "ok": not mismatches, "mismatches": mismatches, "quantities": len(counts)}


def membership_audit(partition_of, ids: Iterable[str], partition: str) -> dict:
    wrong = {}
    for rid in ids:
        p = partition_of(rid)
        if p != partition:
            wrong[str(rid)] = p
    return {"check": "membership", "ok": not wrong, "partition": partition, "wrong": wrong}


def effect_plausibility(
    dimensions: Mapping[str, Mapping],
    max_d_over_delta: float = 10.0,
    min_n: int = 10,
) -> dict:
    """Flag per-dimension results that deserve a second look.

    ``dimensions`` maps name → stats with any of ``n``, ``sd_d``, ``mean_d``,
    ``delta``, ``verdict`` (the shape :func:`studies.equivalence.tost_verdict`
    returns).
    """
    flags: dict[str, list[str]] = {}
    for name, s in dimensions.items():
        f = []
        n = s.get("n")
        sd = s.get("sd_d")
        d = s.get("mean_d")
        delta = s.get("delta")
        if n is not None and n < min_n:
            f.append(f"n={n} < {min_n}")
        if sd is not None and sd == 0:
            f.append("sd_d is exactly 0 (no variation across units)")
        if d is not None and delta:
            ratio = abs(d) / delta
            if ratio > max_d_over_delta:
                f.append(f"|d|/delta={ratio:.1f} > {max_d_over_delta}")
            if d == 0 and s.get("verdict") == "equivalent":
                f.append("mean_d exactly 0 with an equivalent verdict")
        if f:
            flags[name] = f
    return {"check": "plausibility", "ok": not flags, "flags": flags, "dimensions": len(dimensions)}


def run_pressure(
    study_root: str | Path,
    results_path: str | Path | None = None,
    rerun_results_path: str | Path | None = None,
    counts_path: str | Path | None = None,
    dimensions: Mapping[str, Mapping] | None = None,
) -> dict:
    root = Path(study_root)
    report: dict = {"study_id": root.name, "checks": []}
    state_path = root / "fence" / "state.json"
    if state_path.exists():
        report["checks"].append(leakage_audit(json.loads(state_path.read_text())))
    else:
        report["checks"].append({"check": "leakage", "ok": False, "detail": "no fence state"})
    if results_path and rerun_results_path:
        report["checks"].append(determinism(results_path, rerun_results_path))
    if counts_path:
        report["checks"].append(reconciliation(json.loads(Path(counts_path).read_text())))
    if dimensions:
        report["checks"].append(effect_plausibility(dimensions))
    report["ok"] = all(c.get("ok") for c in report["checks"])
    return report
