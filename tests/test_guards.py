"""Tests for studies.guards — direct-read, restricted-content, and schema checks."""

import json
import subprocess
from pathlib import Path

import pytest

from studies import guards


def _study(tmp_path, protected=("rows.csv",), allowed=(), globs=("data/*",)):
    root = tmp_path / "study-x"
    (root / "fence").mkdir(parents=True)
    (root / "fence" / "protected-files.json").write_text(
        json.dumps(
            {
                "protected_filenames": list(protected),
                "allowed_readers": list(allowed),
                "restricted_content_globs": list(globs),
            }
        )
    )
    (root / "scripts").mkdir()
    return root


def test_direct_read_detected_and_comments_ignored(tmp_path):
    root = _study(tmp_path)
    (root / "scripts" / "bad.py").write_text(
        'import csv\n# rows.csv is documented here\npath = DATA / "rows.csv"\n'
    )
    hits = guards.find_direct_reads(root, guards.load_protection(root))
    assert [(h.path, h.line, h.filename) for h in hits] == [("scripts/bad.py", 3, "rows.csv")]


def test_allowed_reader_is_skipped(tmp_path):
    root = _study(tmp_path, allowed=("data_access.py",))
    (root / "data_access.py").write_text('RAW = "rows.csv"\n')
    assert guards.find_direct_reads(root, guards.load_protection(root)) == []


def test_baseline_ratchets(tmp_path):
    root = _study(tmp_path)
    (root / "scripts" / "legacy.py").write_text('a = "rows.csv"\nb = "rows.csv"\n')
    hits = guards.find_direct_reads(root, guards.load_protection(root))
    assert len(hits) == 2
    assert guards.new_direct_reads(hits, {"direct_reads": {"scripts/legacy.py": 2}}) == []
    assert len(guards.new_direct_reads(hits, {"direct_reads": {"scripts/legacy.py": 1}})) == 1
    assert len(guards.new_direct_reads(hits, {"direct_reads": {}})) == 2


def test_restricted_globs_and_baseline():
    prot = {"restricted_content_globs": ["data/*.csv", "data/raw/*"]}
    tracked = ["data/rows.csv", "data/raw/a.json", "scripts/x.py", "data/README.md"]
    found = guards.find_tracked_restricted(tracked, prot)
    assert found == ["data/raw/a.json", "data/rows.csv"]
    assert guards.new_tracked_restricted(found, {"tracked_restricted": ["data/raw/"]}) == ["data/rows.csv"]


def test_git_tracked_files_in_a_real_repo(tmp_path):
    if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
        pytest.skip("git not available")
    repo = tmp_path / "repo"
    root = repo / "studies" / "s1"
    (root / "data").mkdir(parents=True)
    (root / "data" / "rows.csv").write_text("id\n1\n")
    (root / "keep.txt").write_text("x")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    tracked = guards.git_tracked_files(repo, root)
    assert sorted(tracked) == ["data/rows.csv", "keep.txt"]
    # absolute study path gives the same study-relative answer
    assert sorted(guards.git_tracked_files(repo, root.resolve())) == ["data/rows.csv", "keep.txt"]


def test_git_tracked_files_outside_a_repo_returns_none(tmp_path):
    (tmp_path / "s").mkdir()
    assert guards.git_tracked_files(tmp_path, tmp_path / "s") is None


def test_validate_results():
    pytest.importorskip("jsonschema")
    schema = {
        "type": "object",
        "required": ["study_id", "n"],
        "additionalProperties": False,
        "properties": {"study_id": {"const": "s1"}, "n": {"type": "integer"}},
    }
    assert guards.validate_results({"study_id": "s1", "n": 3}, schema) == []
    msgs = guards.validate_results({"study_id": "s2", "extra": 1}, schema)
    assert any("study_id" in m for m in msgs) and any("extra" in m or "required" in m for m in msgs)


def test_run_guards_skips_study_without_protection(tmp_path):
    (tmp_path / "plain").mkdir()
    rep = guards.run_guards(tmp_path, tmp_path / "plain")
    assert rep["ok"] and "skipped" in rep
