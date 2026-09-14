"""Study templates — the two study shapes this program has run, as reusable files.

* ``paired-equivalence`` (SDA-002's shape): per unit, a treatment value and a
  reference value on several dimensions; the claim is that the mean
  difference is inside a pre-registered bound (TOST), optionally with a
  second condition to test whether an intervention narrowed the gap.
* ``paired-comparison`` (SDA-001's shape): per unit, two models or rules
  scored on the same held-out data; the claim is that one is better by a
  registered direction (paired difference with 95% and bootstrap intervals).

A template adds to the scaffold: ``config/analysis.json``, a strict
``config/results-schema.json`` naming every registered quantity, a
registration draft with the analysis plan written and the PI rulings left as
slots, and ``analysis.py`` — a thin runner over :mod:`studies.paired_analysis`
so the study's analysis is library code.
"""

from __future__ import annotations

import json

TEMPLATE_NAMES = ("paired-equivalence", "paired-comparison")

_ANALYSIS_PY = '''"""{study_id} — registered analysis (template: {template}).

    python analysis.py --partition exploratory|confirmatory [--input output/per-unit.csv] [--commit-tag TAG]

Reads the per-unit table, runs studies.paired_analysis with config/analysis.json,
validates the result against config/results-schema.json, and writes the results
file only if it validates. The same command, on the same input, yields the same
bytes; that is the determinism proof.
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# The engine normally lives at <repo>/packages/engine; YARDSTICK_ENGINE overrides.
for _candidate in (os.environ.get("YARDSTICK_ENGINE"), str(ROOT.parents[1] / "packages" / "engine")):
    if _candidate and Path(_candidate).exists() and _candidate not in sys.path:
        sys.path.insert(0, _candidate)

from studies import paired_analysis  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--partition", required=True, choices=["exploratory", "confirmatory"])
    ap.add_argument("--input", default=None)
    ap.add_argument("--commit-tag", default="unknown")
    ap.add_argument("--out", default=None, help="write here instead of the study's results file (replays)")
    args = ap.parse_args(argv)
    results, errors = paired_analysis.run(ROOT, args.partition, args.input, args.commit_tag)
    if errors:
        print("results do not match config/results-schema.json:", file=sys.stderr)
        for e in errors[:10]:
            print("  -", e, file=sys.stderr)
        return 1
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(results, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
    else:
        out = paired_analysis.write_results(ROOT, args.partition, results)
    print(f"wrote {{out}}")
    print(json.dumps(results["summary"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

_CONDITION_EQ = {
    "type": "object",
    "required": ["n", "missing"],
    "additionalProperties": False,
    "properties": {
        "n": {"type": "integer", "minimum": 0},
        "missing": {"type": "boolean"},
        "mean_d": {"type": "number"},
        "sd_d": {"type": "number"},
        "ci_equivalence": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
        "ci_zero": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
        "verdict": {"enum": ["equivalent", "trivially_different", "different", "inconclusive"]},
        "d_over_delta": {"type": "number"},
        "bootstrap_ci_90": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
        "spread": {
            "type": "object",
            "required": ["sd_ratio", "r", "n"],
            "additionalProperties": False,
            "properties": {"sd_ratio": {"type": ["number", "null"]}, "r": {"type": ["number", "null"]}, "n": {"type": "integer"}},
        },
    },
}

_CONDITION_CMP = {
    "type": "object",
    "required": ["n", "missing"],
    "additionalProperties": False,
    "properties": {
        "n": {"type": "integer", "minimum": 0},
        "missing": {"type": "boolean"},
        "mean_d": {"type": "number"},
        "sd_d": {"type": "number"},
        "ci_95": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
        "p_value": {"type": "number"},
        "supported": {"type": "boolean"},
        "bootstrap_ci_90": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
        "spread": {
            "type": "object",
            "required": ["sd_ratio", "r", "n"],
            "additionalProperties": False,
            "properties": {"sd_ratio": {"type": ["number", "null"]}, "r": {"type": ["number", "null"]}, "n": {"type": "integer"}},
        },
    },
}

_GAP = {
    "type": "object",
    "required": ["comparison", "n", "p_value", "median_reduction"],
    "additionalProperties": False,
    "properties": {
        "comparison": {"type": "string"},
        "n": {"type": "integer"},
        "p_value": {"type": ["number", "null"]},
        "p_holm": {"type": ["number", "null"]},
        "median_reduction": {"type": ["number", "null"]},
    },
}


def _schema(study_id: str, mode: str) -> dict:
    eqm = mode == "equivalence"
    dimension = {
        "type": "object",
        "required": ["units", "conditions"] + (["delta"] if eqm else ["expected_sign"]),
        "additionalProperties": False,
        "properties": {
            "units": {"type": ["string", "null"]},
            **({"delta": {"type": "number", "exclusiveMinimum": 0}} if eqm else {"expected_sign": {"enum": [-1, 0, 1]}}),
            "conditions": {"type": "object", "additionalProperties": _CONDITION_EQ if eqm else _CONDITION_CMP},
            **({"gap_test": _GAP} if eqm else {}),
        },
    }
    summary_props = {"primary_condition": {"type": "string"}, "n_dimensions": {"type": "integer"}}
    summary_req = ["primary_condition", "n_dimensions"]
    if eqm:
        summary_props.update({"n_equivalent": {"type": "integer"}, "all_equivalent": {"type": "boolean"}, "gap_rejections": {"type": "integer"}})
        summary_req += ["n_equivalent", "all_equivalent", "gap_rejections"]
    else:
        summary_props.update({"n_supported": {"type": "integer"}})
        summary_req += ["n_supported"]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": f"{study_id} Results (paired-{mode})",
        "description": "Frozen at registration. Every registered quantity is named here before any number exists. Deterministic in the input: no timestamps.",
        "type": "object",
        "required": ["study_id", "partition", "template", "commit_tag", "input_sha256", "analysis", "dimensions", "summary"],
        "additionalProperties": False,
        "properties": {
            "study_id": {"type": "string", "const": study_id},
            "partition": {"enum": ["exploratory", "confirmatory"]},
            "template": {"const": f"paired-{mode}"},
            "commit_tag": {"type": "string"},
            "input_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "analysis": {
                "type": "object",
                "required": ["mode", "alpha", "bootstrap_reps", "seed", "conditions"],
                "additionalProperties": False,
                "properties": {
                    "mode": {"const": mode},
                    "alpha": {"type": "number"},
                    "bootstrap_reps": {"type": "integer"},
                    "seed": {"type": "integer"},
                    "conditions": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                },
            },
            "dimensions": {"type": "object", "additionalProperties": dimension, "minProperties": 1},
            "summary": {"type": "object", "required": summary_req, "additionalProperties": False, "properties": summary_props},
        },
    }


_ANALYSIS_EQ = {
    "_doc": "Registration parameters. delta per dimension is a PI ruling (e.g. k x sd_ref from the real-vs-real reference spread).",
    "mode": "equivalence",
    "alpha": 0.05,
    "bootstrap_reps": 5000,
    "seed": 0,
    "primary_condition": "treatment",
    "conditions": ["treatment"],
    "dimensions": [{"name": "example_dimension", "units": "[OPEN]", "direction": "[OPEN — e.g. higher = longer utterances]", "kind": "[OPEN — direct | classifier]", "delta": 0.1}],
}

_ANALYSIS_CMP = {
    "_doc": "Registration parameters. expected_sign is the registered direction of d = model_A - model_B (-1: A lower is better, +1: A higher).",
    "mode": "comparison",
    "alpha": 0.05,
    "bootstrap_reps": 5000,
    "seed": 0,
    "primary_condition": "model_a_vs_b",
    "conditions": ["model_a_vs_b"],
    "dimensions": [{"name": "example_metric", "units": "[OPEN]", "direction": "[OPEN — e.g. lower = better calibrated]", "kind": "[OPEN — direct | classifier]", "expected_sign": -1}],
}

_DRAFT_EQ = """# {study_id} — OSF Registration text, v0.1 — draft (template: paired-equivalence)

Cover note (not part of the registration). Every number cited from `registration/facts.md`, which is generated and checked in CI. Slots marked **[PI ruling]** are decided at G2; slots marked **[OPEN]** are filled from exploratory work.

## 1. Study information

Title: {title} [PI to confirm]

Author: [OPEN]

Research question. [OPEN — one paragraph: what is being reproduced, by what, and what "close enough" means]

Hypotheses:

- **H1 (equivalence).** On each registered dimension, the mean within-unit difference between the primary condition and the reference lies inside ±δ.
- **H2 (intervention effect, if a second condition is registered).** Per unit, |d| is smaller under the primary condition than under the comparison condition on at least [OPEN] of the registered dimensions.
- **S1 (secondary, descriptive).** Spread fidelity: SD ratio and pairing correlation between treatment and reference values across units.

## 2. Data description

Dataset, citation, terms, file hashes (facts.md → Config files). Unit of analysis: [OPEN].

## 3. Variables

Dimensions by code version (facts.md → Measures). Registered family **[PI ruling]**: [OPEN]. Floor rule: any dimension whose treatment and reference medians are both below [OPEN] on confirmatory data is reported as descriptive and dropped from the family; applied once, before any test, and logged.

## 4. Knowledge of the data

4.1 Partition (facts.md → Fence). 4.2 What has been seen: [OPEN]. 4.3 What the exploratory pass changed, in order: [OPEN]. 4.4 Exploratory results that informed the bounds: [OPEN].

## 5. Analysis plan

5.1 Pairing. Every unit yields one d = f_treatment − f_reference per dimension per condition; tests are on the mean of d across units.

5.2 Bounds **[PI ruling]**. δ per dimension = k × sd_ref, where sd_ref is the real-vs-real reference spread for that dimension ([OPEN — how sd_ref is computed]); k = [PI ruling]. Values are in `config/analysis.json` and reproduced in facts.md.

5.3 Test and verdict (`studies.equivalence.tost_verdict`). 90% t-interval on the mean d (TOST at α = 0.05) and a 95% interval for zero. Verdicts: *equivalent* (90% CI inside (−δ, δ), 95% CI includes 0); *trivially different* (inside, excludes 0); *different* (not inside, excludes 0); *inconclusive* (not inside, includes 0). H1 holds for a dimension when the verdict is equivalent or trivially different; the summary claim "equivalent on all registered dimensions" is an intersection and needs no multiplicity correction. A percentile-bootstrap 90% interval (B = 5,000, seed committed) is reported alongside.

5.4 H2 (`studies.equivalence.paired_gap_test`). One-sided paired Wilcoxon on |d|, Holm-adjusted across the family; supported if at least [OPEN] dimensions reject.

5.5 S1 (`studies.equivalence.spread_stats`). SD(f_treatment)/SD(f_reference) and r(f_treatment, f_reference) per dimension and condition; descriptive.

5.6 Exclusions, fixed before the draw: [OPEN]. Per-dimension missingness is reported as counts; a unit missing on one dimension stays in the others.

## 6. Sample size **[PI ruling]**

N units, both conditions on the same units. N = max over the family of `studies.equivalence.sample_size(sd_d, δ, θ, power)` with sd_d and residual shift θ from the exploratory pass (θ = 0 only if the exploratory pass supports it), 90% power, plus [OPEN]% for missingness. Draw: seeded random sample from the confirmatory partition; seed committed at freeze: `________`.

## 7. Deviations and amendments

`deviations-log.md` is part of this registration.

## 8. Confirmatory run and artifacts

`python analysis.py --partition confirmatory --commit-tag <tag>` from the tagged commit, on the archived per-unit table; the results file is written only if it validates against `config/results-schema.json`; a rerun on a clean checkout is byte-identical (`yardstick verify --compare`). Evidence package per G4.
"""

_DRAFT_CMP = """# {study_id} — OSF Registration text, v0.1 — draft (template: paired-comparison)

Cover note (not part of the registration). Every number cited from `registration/facts.md`, which is generated and checked in CI. Slots marked **[PI ruling]** are decided at G2; slots marked **[OPEN]** are filled from exploratory work.

## 1. Study information

Title: {title} [PI to confirm]

Author: [OPEN]

Research question. [OPEN — one paragraph: which two models or rules, scored on what, and which direction of difference is the claim]

Hypotheses:

- **H1.** On [OPEN metric], the student-level (unit-level) mean of paired differences d = metric(A) − metric(B) is [OPEN: negative / positive]; the 95% interval excludes zero in that direction.
- **H2 … [OPEN].**

## 2. Data description

Dataset, citation, terms, file hashes (facts.md → Config files). Unit of analysis: [OPEN]. Held-out evaluation: [OPEN — how the held-out set is defined].

## 3. Variables

Metrics and models by code version (facts.md → Measures). Registered metrics **[PI ruling]**: [OPEN]. Expected sign per metric is fixed in `config/analysis.json`.

## 4. Knowledge of the data

4.1 Partition (facts.md → Fence). 4.2 What has been seen: [OPEN]. 4.3 What the exploratory pass changed: [OPEN]. 4.4 Exploratory effect sizes that informed the sample size: [OPEN].

## 5. Analysis plan

5.1 Pairing. Every unit yields one d per metric; tests are on the mean of d across units.

5.2 Test. Paired t-test on d against zero with a 95% t-interval, and a percentile-bootstrap 90% interval (B = 5,000, seed committed). A hypothesis is supported when the 95% interval excludes zero in the registered direction. Multiplicity across metrics: [PI ruling — Holm across the family, or intersection claim].

5.3 Secondary (`studies.equivalence.spread_stats`): SD ratio and correlation of the two models' unit-level metrics; descriptive.

5.4 Exclusions, fixed before the draw: [OPEN].

## 6. Sample size **[PI ruling]**

N units from the confirmatory partition; N from the exploratory sd_d and the smallest effect of interest at 90% power (paired t), plus [OPEN]% for missingness. Draw seed committed at freeze: `________`.

## 7. Deviations and amendments

`deviations-log.md` is part of this registration.

## 8. Confirmatory run and artifacts

`python analysis.py --partition confirmatory --commit-tag <tag>` from the tagged commit; the results file is written only if it validates against `config/results-schema.json`; byte-identical on a clean rerun (`yardstick verify --compare`). Evidence package per G4.
"""

_FIGURES_EQ = """# {study_id} — figure list (template: paired-equivalence)

Every figure renders from the results file.

| # | Figure | Source fields |
|---|---|---|
| 1 | Per-dimension mean d with 90% and 95% intervals against ±δ, per condition | dimensions.*.conditions.*.{{mean_d, ci_equivalence, ci_zero}}, dimensions.*.delta |
| 2 | Verdict grid (dimension × condition) | dimensions.*.conditions.*.verdict |
| 3 | Spread: SD ratio and pairing r per dimension | dimensions.*.conditions.*.spread |
| 4 | Gap reduction per dimension with Holm-adjusted p | dimensions.*.gap_test |
"""

_FIGURES_CMP = """# {study_id} — figure list (template: paired-comparison)

Every figure renders from the results file.

| # | Figure | Source fields |
|---|---|---|
| 1 | Per-metric mean d with 95% and bootstrap intervals against zero | dimensions.*.conditions.*.{{mean_d, ci_95, bootstrap_ci_90}} |
| 2 | Support grid (metric × registered direction) | dimensions.*.conditions.*.supported, dimensions.*.expected_sign |
| 3 | Unit-level scatter of model A vs model B | per-unit table |
"""


def template_files(name: str, study_id: str, title: str) -> dict[str, str]:
    """Study-relative path → content for a template."""
    if name not in TEMPLATE_NAMES:
        raise ValueError(f"unknown template {name!r}; choose from {TEMPLATE_NAMES}")
    mode = "equivalence" if name == "paired-equivalence" else "comparison"
    analysis = _ANALYSIS_EQ if mode == "equivalence" else _ANALYSIS_CMP
    draft = _DRAFT_EQ if mode == "equivalence" else _DRAFT_CMP
    figures = _FIGURES_EQ if mode == "equivalence" else _FIGURES_CMP
    return {
        "config/analysis.json": json.dumps(analysis, indent=2) + "\n",
        "config/results-schema.json": json.dumps(_schema(study_id, mode), indent=2) + "\n",
        "registration/osf-registration-draft.md": draft.format(study_id=study_id, title=title),
        "registration/figure-list.md": figures.format(study_id=study_id),
        "analysis.py": _ANALYSIS_PY.format(study_id=study_id, template=name),
    }
