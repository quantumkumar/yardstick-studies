"""Tests for studies.scaffold — new-study skeleton."""

import json

import pytest

from studies import guards, registration, scaffold


def test_scaffold_creates_kernel_files_and_builds_facts(tmp_path):
    root = scaffold.create_study(tmp_path, "sda-999", "A test study", llm=True)
    for rel in (
        "README.md",
        "data_access.py",
        "fence/protected-files.json",
        "fence/guard-baseline.json",
        "config/results-schema.json",
        "config/generation.json",
        "registration/osf-registration-draft.md",
        "registration/facts-sources.json",
        "registration/facts.md",
        "registration/figure-list.md",
        "deviations-log.md",
        "REPLAY.md",
        "data/.gitignore",
        "output/.gitignore",
        "acquisition/access-terms.md",
    ):
        assert (root / rel).exists(), rel
    schema = json.loads((root / "config" / "results-schema.json").read_text())
    assert schema["properties"]["study_id"]["const"] == "sda-999"
    assert registration.check(root)[0] is True
    report = guards.run_guards(tmp_path, root)
    assert report["ok"] and report["direct_reads_total"] == 0


def test_scaffold_refuses_to_overwrite(tmp_path):
    scaffold.create_study(tmp_path, "sda-998", "x")
    with pytest.raises(FileExistsError):
        scaffold.create_study(tmp_path, "sda-998", "x")


def test_scaffold_without_llm_has_no_generation_config(tmp_path):
    root = scaffold.create_study(tmp_path, "sda-997", "x")
    assert not (root / "config" / "generation.json").exists()
