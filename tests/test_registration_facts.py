"""Tests for studies.registration — generated registration facts."""

import json
from pathlib import Path

from studies import registration


def _mini_study(tmp_path):
    root = tmp_path / "mini"
    (root / "fence").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "measures").mkdir()
    (root / "registration").mkdir()
    (root / "output").mkdir()
    (root / "fence" / "exploratory-manifest.json").write_text(
        json.dumps({"study_id": "mini", "partition_name": "exploratory", "row_count": 3,
                    "manifest_hash": "a" * 64, "created_at": "2026-01-01T00:00:00+00:00", "seed": 1})
    )
    (root / "fence" / "confirmatory-manifest.json").write_text(
        json.dumps({"study_id": "mini", "partition_name": "confirmatory", "row_count": 3,
                    "manifest_hash": "b" * 64, "created_at": "2026-01-01T00:00:00+00:00", "seed": 1})
    )
    (root / "fence" / "state.json").write_text(
        json.dumps({"study_id": "mini", "confirmatory_locked": True, "locked_at": "t", "unlocked_at": None,
                    "unlock_reason": None, "access_log": [{"partition": "confirmatory", "allowed": False}]})
    )
    (root / "config" / "generation.json").write_text(json.dumps({"model": "m-1", "api_parameters": {"t": 1}}))
    (root / "config" / "results-schema.json").write_text(json.dumps({"title": "Mini", "required": ["study_id"]}))
    (root / "measures" / "base.py").write_text('class Base:\n    version: str = "0.0.1"\n')
    (root / "measures" / "m1.py").write_text('class M1:\n    name = "m1"\n    version = "1.2.0"\n')
    (root / "output" / "ref.json").write_text(json.dumps({"measures": {"m1": {"sd_ref": 0.5, "n": 10}}}))
    (root / "registration" / "facts-sources.json").write_text(
        json.dumps({"tables": [{"file": "output/ref.json", "title": "Ref", "path": "measures", "columns": ["n", "sd_ref"]}]})
    )
    return root


def test_facts_cover_every_source_and_are_deterministic(tmp_path):
    root = _mini_study(tmp_path)
    text = registration.build(root)
    assert registration.build(root, write=False) == text
    assert "`" + "a" * 64 + "`" in text and "`" + "b" * 64 + "`" in text
    assert "Fence created" in text and "denied" not in text
    assert "- model: m-1" in text
    assert "| m1 | 1.2.0 | m1.py |" in text and "base.py" not in text
    assert "results-schema.json" in text and "generation.json" in text
    assert "## Ref" in text and "| m1 | 10 | 0.5 |" in text


def test_check_detects_staleness(tmp_path):
    root = _mini_study(tmp_path)
    assert registration.check(root)[0] is False  # nothing committed yet
    registration.build(root)
    assert registration.check(root)[0] is True
    (root / "config" / "generation.json").write_text(json.dumps({"model": "m-2"}))
    ok, msg = registration.check(root)
    assert ok is False and "stale" in msg


def test_cli(tmp_path, capsys):
    root = _mini_study(tmp_path)
    assert registration.main([str(root)]) == 0
    assert registration.main([str(root), "--check"]) == 0
