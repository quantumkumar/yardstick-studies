"""Templates: init from a template, analyze a synthetic per-unit table, results validate, reruns are byte-identical."""

import csv
import json
import subprocess
import shutil
from pathlib import Path

import numpy as np
import pytest

from studies import cli, gates, paired_analysis, templates


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "studies").mkdir(parents=True)
    monkeypatch.setenv("YARDSTICK_STUDIES_ROOT", str(repo / "studies"))
    return repo


def _per_unit_csv(path: Path, conditions, dims, n=60, seed=0, shift=None):
    rng = np.random.default_rng(seed)
    shift = shift or {}
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["unit_id", "condition", "dimension", "f_treatment", "f_reference"])
        w.writeheader()
        for i in range(n):
            for cond in conditions:
                for dim in dims:
                    ref = rng.normal(1.0, 0.3)
                    d = rng.normal(shift.get((cond, dim), 0.0), 0.05)
                    w.writerow({"unit_id": f"u{i:03d}", "condition": cond, "dimension": dim, "f_treatment": f"{ref + d:.6f}", "f_reference": f"{ref:.6f}"})


def _config(root: Path, cfg: dict):
    (root / "config" / "analysis.json").write_text(json.dumps(cfg, indent=2))


def test_template_files_are_complete_and_schema_strict():
    files = templates.template_files("paired-equivalence", "s1", "T")
    assert set(files) == {"config/analysis.json", "config/results-schema.json", "registration/osf-registration-draft.md", "registration/figure-list.md", "analysis.py"}
    schema = json.loads(files["config/results-schema.json"])
    assert schema["additionalProperties"] is False and schema["properties"]["study_id"]["const"] == "s1"
    with pytest.raises(ValueError):
        templates.template_files("nope", "s1", "T")


def test_equivalence_template_end_to_end(workspace):
    assert cli.main(["init", "eq-001", "--title", "Equivalence", "--template", "paired-equivalence"]) == 0
    root = workspace / "studies" / "eq-001"
    assert (root / "analysis.py").exists() and (root / "config" / "analysis.json").exists()
    _config(root, {
        "mode": "equivalence", "alpha": 0.05, "bootstrap_reps": 500, "seed": 1,
        "primary_condition": "calibrated", "conditions": ["calibrated", "uncalibrated"],
        "dimensions": [{"name": "length", "units": "log ratio", "delta": 0.05}, {"name": "richness", "units": "MATTR", "delta": 0.05}],
    })
    _per_unit_csv(root / "output" / "per-unit.csv", ["calibrated", "uncalibrated"], ["length", "richness"],
                  shift={("uncalibrated", "length"): 0.4, ("uncalibrated", "richness"): 0.3})
    assert cli.main(["analyze", "eq-001", "--partition", "exploratory", "--commit-tag", "t1"]) == 0
    results = json.loads((root / "output" / "exploratory-results.json").read_text())
    assert results["template"] == "paired-equivalence"
    cal = results["dimensions"]["length"]["conditions"]["calibrated"]
    unc = results["dimensions"]["length"]["conditions"]["uncalibrated"]
    assert cal["verdict"] in ("equivalent", "trivially_different") and unc["verdict"] == "different"
    assert results["summary"]["all_equivalent"] is True and results["summary"]["gap_rejections"] == 2
    assert results["dimensions"]["length"]["gap_test"]["p_holm"] < 0.05
    assert "spread" in cal and cal["spread"]["r"] > 0.9

    # confirmatory analysis is refused while the fence is locked; allowed after a forced unlock
    raw = root / "data" / "rows.csv"
    with open(raw, "w") as f:
        f.write("OBSID\n" + "\n".join(f"r{i}" for i in range(20)) + "\n")
    assert cli.main(["split", "eq-001", "--csv", str(raw), "--id-column", "OBSID", "--seed", "3"]) == 0
    assert cli.main(["analyze", "eq-001", "--partition", "confirmatory"]) == 1
    assert cli.main(["unlock", "eq-001", "--reason", "test", "--force"]) == 0
    assert cli.main(["analyze", "eq-001", "--partition", "confirmatory", "--commit-tag", "t1"]) == 0
    first = (root / "output" / "confirmatory-results.json").read_bytes()
    assert cli.main(["analyze", "eq-001", "--partition", "confirmatory", "--commit-tag", "t1"]) == 0
    assert (root / "output" / "confirmatory-results.json").read_bytes() == first  # deterministic
    assert cli.main(["verify", "eq-001"]) == 0

    # the standalone runner gives the same bytes
    import os, studies
    env = {**os.environ, "YARDSTICK_ENGINE": str(Path(studies.__file__).resolve().parents[1])}
    res = subprocess.run(["python3", "analysis.py", "--partition", "confirmatory", "--commit-tag", "t1"], cwd=root, capture_output=True, text=True, env=env)
    assert res.returncode == 0, res.stderr
    assert (root / "output" / "confirmatory-results.json").read_bytes() == first


def test_comparison_template_end_to_end(workspace):
    assert cli.main(["init", "cmp-001", "--title", "Comparison", "--template", "paired-comparison"]) == 0
    root = workspace / "studies" / "cmp-001"
    _config(root, {
        "mode": "comparison", "alpha": 0.05, "bootstrap_reps": 500, "seed": 1,
        "primary_condition": "face_vs_tempered", "conditions": ["face_vs_tempered"],
        "dimensions": [{"name": "brier", "units": "Brier", "expected_sign": 1}, {"name": "accuracy", "units": "proportion", "expected_sign": -1}],
    })
    _per_unit_csv(root / "output" / "per-unit.csv", ["face_vs_tempered"], ["brier", "accuracy"],
                  shift={("face_vs_tempered", "brier"): 0.1, ("face_vs_tempered", "accuracy"): 0.1})
    assert cli.main(["analyze", "cmp-001", "--partition", "exploratory"]) == 0
    results = json.loads((root / "output" / "exploratory-results.json").read_text())
    b = results["dimensions"]["brier"]["conditions"]["face_vs_tempered"]
    a = results["dimensions"]["accuracy"]["conditions"]["face_vs_tempered"]
    assert b["supported"] is True and a["supported"] is False  # accuracy went up, sign says it should go down
    assert results["summary"]["n_supported"] == 1


def test_analysis_refuses_results_that_break_the_schema(workspace, tmp_path):
    assert cli.main(["init", "eq-002", "--title", "x", "--template", "paired-equivalence"]) == 0
    root = workspace / "studies" / "eq-002"
    _config(root, {"mode": "equivalence", "conditions": ["t"], "primary_condition": "t",
                   "dimensions": [{"name": "d1", "units": "u", "delta": 0.1}]})
    _per_unit_csv(root / "output" / "per-unit.csv", ["t"], ["d1"])
    # tighten the schema so the produced results no longer fit
    schema_path = root / "config" / "results-schema.json"
    schema = json.loads(schema_path.read_text())
    schema["properties"]["template"] = {"const": "something-else"}
    schema_path.write_text(json.dumps(schema))
    assert cli.main(["analyze", "eq-002", "--partition", "exploratory"]) == 1
    assert not (root / "output" / "exploratory-results.json").exists()
