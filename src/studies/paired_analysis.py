"""Paired analysis engine for the study templates.

Input: a per-unit table (CSV) with one row per unit × condition × dimension::

    unit_id,condition,dimension,f_treatment,f_reference
    t001,calibrated,utterance_length,2.10,2.30
    ...

(``d`` may be given directly instead of the two f columns.)

Config: ``config/analysis.json``::

    {
      "mode": "equivalence" | "comparison",
      "alpha": 0.05,
      "bootstrap_reps": 5000,
      "seed": 0,
      "primary_condition": "calibrated",
      "conditions": ["calibrated", "uncalibrated"],
      "dimensions": [
        {"name": "utterance_length", "units": "log ratio", "delta": 0.093},        # equivalence
        {"name": "brier", "units": "Brier points", "expected_sign": -1}            # comparison
      ]
    }

Output: a results dict that is deterministic in the input and config (no
timestamps — those go in the run log), validated against the template's
``config/results-schema.json`` before it is written.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy import stats

from studies import equivalence as eq

INPUT_DEFAULT = "output/per-unit.csv"


def load_per_unit(path: str | Path) -> dict[tuple[str, str], dict[str, float]]:
    """{(condition, dimension): {unit_id: d}}"""
    table: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        needed = {"unit_id", "condition", "dimension"}
        if not reader.fieldnames or not needed.issubset(reader.fieldnames):
            raise ValueError(f"per-unit table needs columns {sorted(needed)} plus d or f_treatment/f_reference")
        for row in reader:
            key = (row["condition"].strip(), row["dimension"].strip())
            if row.get("d") not in (None, ""):
                d = float(row["d"])
            else:
                ft, fr = row.get("f_treatment", ""), row.get("f_reference", "")
                if ft == "" or fr == "":
                    continue  # missing on this dimension for this unit
                d = float(ft) - float(fr)
            table[key][row["unit_id"].strip()] = d
    return table


def load_values(path: str | Path) -> dict[tuple[str, str], dict[str, tuple[float, float]]]:
    """{(condition, dimension): {unit_id: (f_treatment, f_reference)}} when both f columns exist."""
    out: dict[tuple[str, str], dict[str, tuple[float, float]]] = defaultdict(dict)
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "f_treatment" not in reader.fieldnames:
            return out
        for row in reader:
            ft, fr = row.get("f_treatment", ""), row.get("f_reference", "")
            if ft == "" or fr == "":
                continue
            out[(row["condition"].strip(), row["dimension"].strip())][row["unit_id"].strip()] = (float(ft), float(fr))
    return out


def _sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _round(x, nd=6):
    if x is None:
        return None
    if isinstance(x, (list, tuple)):
        return [_round(v, nd) for v in x]
    if isinstance(x, float):
        if math.isnan(x):
            return None
        return round(x, nd)
    return x


def analyze(study_id: str, partition: str, config: dict, input_path: str | Path, commit_tag: str) -> dict:
    mode = config.get("mode", "equivalence")
    alpha = float(config.get("alpha", 0.05))
    reps = int(config.get("bootstrap_reps", 5000))
    seed = int(config.get("seed", 0))
    conditions = list(config["conditions"])
    primary = config.get("primary_condition", conditions[0])
    table = load_per_unit(input_path)
    values = load_values(input_path)

    dims_out: dict[str, dict] = {}
    gap_pvals: list[float | None] = []
    gap_dims: list[str] = []
    for spec in config["dimensions"]:
        name = spec["name"]
        entry: dict = {"units": spec.get("units"), "conditions": {}}
        if mode == "equivalence":
            delta = float(spec["delta"])
            entry["delta"] = delta
        else:
            entry["expected_sign"] = int(spec.get("expected_sign", 0))
        for cond in conditions:
            d_by_unit = table.get((cond, name), {})
            units = sorted(d_by_unit)
            d = [d_by_unit[u] for u in units]
            if len(d) < 2:
                entry["conditions"][cond] = {"n": len(d), "missing": True}
                continue
            res: dict = {"n": len(d), "missing": False}
            if mode == "equivalence":
                v = eq.tost_verdict(d, delta, alpha)
                res.update({k: v[k] for k in ("mean_d", "sd_d", "ci_equivalence", "ci_zero", "verdict", "d_over_delta")})
            else:
                a = np.asarray(d)
                t = stats.ttest_1samp(a, 0.0)
                se = a.std(ddof=1) / math.sqrt(len(a))
                tcrit = stats.t.ppf(1 - alpha / 2, len(a) - 1)
                res.update({
                    "mean_d": float(a.mean()),
                    "sd_d": float(a.std(ddof=1)),
                    "ci_95": [float(a.mean() - tcrit * se), float(a.mean() + tcrit * se)],
                    "p_value": float(t.pvalue),
                })
                sign = entry["expected_sign"]
                ci = res["ci_95"]
                res["supported"] = bool((sign > 0 and ci[0] > 0) or (sign < 0 and ci[1] < 0) or (sign == 0 and (ci[0] > 0 or ci[1] < 0)))
            res["bootstrap_ci_90"] = eq.bootstrap_ci(d, 0.90, reps, seed)
            pairs = values.get((cond, name), {})
            if pairs:
                common = [u for u in units if u in pairs]
                s = eq.spread_stats([pairs[u][0] for u in common], [pairs[u][1] for u in common])
                res["spread"] = {"sd_ratio": s["sd_ratio"], "r": s["r"], "n": s["n"]}
            entry["conditions"][cond] = {k: _round(v) for k, v in res.items()}
        # gap test between the primary condition and each other condition (equivalence mode)
        if mode == "equivalence" and len(conditions) >= 2:
            others = [c for c in conditions if c != primary]
            comp = others[0]
            p_units = table.get((primary, name), {})
            c_units = table.get((comp, name), {})
            common = sorted(set(p_units) & set(c_units))
            if len(common) >= 5:
                g = eq.paired_gap_test([p_units[u] for u in common], [c_units[u] for u in common])
                entry["gap_test"] = {"comparison": comp, "n": g["n"], "p_value": _round(g["p_value"]), "median_reduction": _round(g["median_reduction"])}
                gap_pvals.append(g["p_value"])
            else:
                entry["gap_test"] = {"comparison": comp, "n": len(common), "p_value": None, "median_reduction": None}
                gap_pvals.append(None)
            gap_dims.append(name)
        dims_out[name] = entry

    if gap_dims:
        adjusted = eq.holm(gap_pvals)
        for name, p_adj in zip(gap_dims, adjusted):
            dims_out[name]["gap_test"]["p_holm"] = _round(p_adj)

    summary: dict = {"primary_condition": primary, "n_dimensions": len(dims_out)}
    if mode == "equivalence":
        prim = [dims_out[n]["conditions"].get(primary, {}) for n in dims_out]
        summary["n_equivalent"] = sum(1 for r in prim if r.get("verdict") in ("equivalent", "trivially_different"))
        summary["all_equivalent"] = bool(prim) and summary["n_equivalent"] == len(prim)
        summary["gap_rejections"] = sum(
            1 for n in dims_out if (dims_out[n].get("gap_test") or {}).get("p_holm") is not None and dims_out[n]["gap_test"]["p_holm"] < alpha
        )
    else:
        prim = [dims_out[n]["conditions"].get(primary, {}) for n in dims_out]
        summary["n_supported"] = sum(1 for r in prim if r.get("supported"))

    return {
        "study_id": study_id,
        "partition": partition,
        "template": f"paired-{mode}",
        "commit_tag": commit_tag,
        "input_sha256": _sha256(input_path),
        "analysis": {"mode": mode, "alpha": alpha, "bootstrap_reps": reps, "seed": seed, "conditions": conditions},
        "dimensions": dims_out,
        "summary": summary,
    }


def run(study_root: str | Path, partition: str, input_path: str | Path | None = None, commit_tag: str = "unknown") -> tuple[dict, list[str]]:
    """Analyze and validate against the study's schema. Returns (results, schema_errors)."""
    from studies import guards

    root = Path(study_root)
    config = json.loads((root / "config" / "analysis.json").read_text(encoding="utf-8"))
    inp = Path(input_path) if input_path else root / INPUT_DEFAULT
    results = analyze(root.name, partition, config, inp, commit_tag)
    schema = root / "config" / "results-schema.json"
    errors = guards.validate_results(results, schema) if schema.exists() else ["no schema"]
    return results, errors


def write_results(study_root: str | Path, partition: str, results: dict) -> Path:
    root = Path(study_root)
    out = root / "output" / ("confirmatory-results.json" if partition == "confirmatory" else f"{partition}-results.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out
