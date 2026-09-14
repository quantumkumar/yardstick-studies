"""Tests for the generic research data fence infrastructure.

TDD: these tests are written BEFORE the implementation.
"""

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from studies.fence import (
    FenceManifest,
    FenceState,
    create_split,
    get_access_log,
    is_confirmatory_locked,
    request_access,
    unlock_confirmatory,
    verify_split,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

STUDY_ID = "test-study-001"


@pytest.fixture(autouse=True)
def clean_study_dir(tmp_path, monkeypatch):
    """Redirect fence storage to a temp directory for each test."""
    monkeypatch.setenv("YARDSTICK_STUDIES_ROOT", str(tmp_path))
    yield
    # cleanup handled by tmp_path


def _sample_ids(n: int = 100) -> list[str]:
    return [f"row-{i:04d}" for i in range(n)]


# ---------------------------------------------------------------------------
# Split determinism
# ---------------------------------------------------------------------------


class TestCreateSplit:
    def test_create_split_deterministic(self):
        """Same seed + same IDs must produce identical partitions."""
        ids = _sample_ids(200)
        m1_exp, m1_conf = create_split(STUDY_ID + "-a", ids, seed=42)
        m2_exp, m2_conf = create_split(STUDY_ID + "-b", ids, seed=42)
        assert m1_exp.manifest_hash == m2_exp.manifest_hash
        assert m1_conf.manifest_hash == m2_conf.manifest_hash
        assert m1_exp.row_count == m2_exp.row_count

    def test_create_split_different_seed_different_partition(self):
        """Different seeds must produce different partitions."""
        ids = _sample_ids(200)
        m1_exp, _ = create_split(STUDY_ID + "-c", ids, seed=42)
        m2_exp, _ = create_split(STUDY_ID + "-d", ids, seed=99)
        assert m1_exp.manifest_hash != m2_exp.manifest_hash

    def test_create_split_excludes_ids(self):
        """Excluded IDs must not appear in either partition."""
        ids = _sample_ids(100)
        exclude = ids[:10]
        exp_m, conf_m = create_split(
            STUDY_ID + "-e", ids, seed=42, exclude_ids=exclude
        )
        assert exp_m.row_count + conf_m.row_count == 90

    def test_create_split_fractions_correct(self):
        """Exploratory fraction controls the split ratio."""
        ids = _sample_ids(1000)
        exp_m, conf_m = create_split(
            STUDY_ID + "-f", ids, seed=42, exploratory_fraction=0.3
        )
        # With 1000 IDs and 0.3, expect ~300 exploratory, ~700 confirmatory
        assert 250 <= exp_m.row_count <= 350
        assert 650 <= conf_m.row_count <= 750
        assert exp_m.row_count + conf_m.row_count == 1000


# ---------------------------------------------------------------------------
# Fence state
# ---------------------------------------------------------------------------


class TestFenceState:
    def test_confirmatory_locked_by_default(self):
        """After create_split, confirmatory must be locked."""
        ids = _sample_ids(50)
        create_split(STUDY_ID, ids, seed=1)
        assert is_confirmatory_locked(STUDY_ID) is True

    def test_request_access_exploratory_always_granted(self):
        """Exploratory access is always granted regardless of lock state."""
        ids = _sample_ids(50)
        create_split(STUDY_ID, ids, seed=1)
        assert request_access(STUDY_ID, "exploratory", "initial EDA") is True

    def test_request_access_confirmatory_denied_when_locked(self):
        """Confirmatory access must be denied when fence is up."""
        ids = _sample_ids(50)
        create_split(STUDY_ID, ids, seed=1)
        assert request_access(STUDY_ID, "confirmatory", "peeking") is False

    def test_request_access_confirmatory_granted_when_unlocked(self):
        """Confirmatory access must be granted after unlock."""
        ids = _sample_ids(50)
        create_split(STUDY_ID, ids, seed=1)
        unlock_confirmatory(STUDY_ID, "Registration frozen at OSF")
        assert request_access(STUDY_ID, "confirmatory", "final analysis") is True

    def test_unlock_requires_reason(self):
        """Unlocking without a reason must raise an error."""
        ids = _sample_ids(50)
        create_split(STUDY_ID, ids, seed=1)
        with pytest.raises((ValueError, TypeError)):
            unlock_confirmatory(STUDY_ID, "")


# ---------------------------------------------------------------------------
# Access log
# ---------------------------------------------------------------------------


class TestAccessLog:
    def test_access_log_records_all_attempts(self):
        """Every access attempt must be recorded in the log."""
        ids = _sample_ids(50)
        create_split(STUDY_ID, ids, seed=1)

        request_access(STUDY_ID, "exploratory", "reason-1")
        request_access(STUDY_ID, "confirmatory", "reason-2")
        request_access(STUDY_ID, "exploratory", "reason-3")

        log = get_access_log(STUDY_ID)
        assert len(log) == 3
        assert log[0]["partition"] == "exploratory"
        assert log[0]["allowed"] is True
        assert log[1]["partition"] == "confirmatory"
        assert log[1]["allowed"] is False
        assert log[2]["partition"] == "exploratory"
        assert log[2]["allowed"] is True
        # Each entry has a timestamp
        for entry in log:
            assert "timestamp" in entry


# ---------------------------------------------------------------------------
# Verify split
# ---------------------------------------------------------------------------


class TestVerifySplit:
    def test_verify_split_passes(self):
        """verify_split with correct seed must return valid=True."""
        ids = _sample_ids(100)
        create_split(STUDY_ID, ids, seed=77)
        result = verify_split(STUDY_ID, ids, seed=77)
        assert result["valid"] is True

    def test_verify_split_fails_on_wrong_seed(self):
        """verify_split with wrong seed must return valid=False."""
        ids = _sample_ids(100)
        create_split(STUDY_ID, ids, seed=77)
        result = verify_split(STUDY_ID, ids, seed=78)
        assert result["valid"] is False
