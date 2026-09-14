"""Tests for studies.access — the fenced data-access layer."""

import csv
import json
from pathlib import Path

import pytest

from studies import fence
from studies.access import (
    FencedDataset,
    FenceError,
    FenceLockedError,
    read_csv_partition,
    unique_ids_from_csv,
)

STUDY = "access-test"
SEED = 7


@pytest.fixture(autouse=True)
def studies_root(tmp_path, monkeypatch):
    monkeypatch.setenv("YARDSTICK_STUDIES_ROOT", str(tmp_path))
    yield tmp_path


@pytest.fixture
def ids():
    return [f"r{i:03d}" for i in range(60)]


@pytest.fixture
def split(ids):
    return fence.create_split(STUDY, ids, seed=SEED, exploratory_fraction=0.5, exclude_ids=["r000"])


def test_construction_verifies_against_manifests(split, ids):
    ds = FencedDataset(STUDY, ids, seed=SEED, exclude_ids=["r000"])
    parts = ds.membership(ids)
    assert parts["r000"] == "excluded"
    assert sorted(set(parts.values())) == ["confirmatory", "excluded", "exploratory"]
    assert sum(v == "exploratory" for v in parts.values()) == split[0].row_count


def test_wrong_seed_is_a_fence_error(split, ids):
    with pytest.raises(FenceError):
        FencedDataset(STUDY, ids, seed=SEED + 1, exclude_ids=["r000"])


def test_missing_exclusion_is_a_fence_error(split, ids):
    with pytest.raises(FenceError):
        FencedDataset(STUDY, ids, seed=SEED)


def test_exploratory_read_is_logged_and_allowed(split, ids):
    ds = FencedDataset(STUDY, ids, seed=SEED, exclude_ids=["r000"])
    got = ds.ids("exploratory", "unit test read")
    assert len(got) == split[0].row_count
    log = fence.get_access_log(STUDY)
    assert log[-1]["partition"] == "exploratory" and log[-1]["allowed"] is True


def test_confirmatory_read_raises_while_locked_and_is_logged(split, ids):
    ds = FencedDataset(STUDY, ids, seed=SEED, exclude_ids=["r000"])
    with pytest.raises(FenceLockedError):
        ds.ids("confirmatory", "unit test: should be refused")
    log = fence.get_access_log(STUDY)
    assert log[-1]["partition"] == "confirmatory" and log[-1]["allowed"] is False


def test_confirmatory_read_works_after_logged_unlock(split, ids):
    ds = FencedDataset(STUDY, ids, seed=SEED, exclude_ids=["r000"])
    fence.unlock_confirmatory(STUDY, "registration frozen; test")
    got = ds.ids("confirmatory", "confirmatory run")
    assert len(got) == split[1].row_count


def test_reason_is_required(split, ids):
    ds = FencedDataset(STUDY, ids, seed=SEED, exclude_ids=["r000"])
    with pytest.raises(ValueError):
        ds.ids("exploratory", "")


def test_filter_rows_never_yields_other_partition_or_excluded(split, ids):
    ds = FencedDataset(STUDY, ids, seed=SEED, exclude_ids=["r000"])
    rows = [{"id": i, "payload": f"p-{i}"} for i in ids]
    got = list(ds.filter_rows(rows, key=lambda r: r["id"], partition="exploratory", reason="t"))
    assert got and all(ds.partition_of(r["id"]) == "exploratory" for r in got)
    assert all(r["id"] != "r000" for r in got)


def test_assert_partition_catches_a_mixed_sample(split, ids):
    ds = FencedDataset(STUDY, ids, seed=SEED, exclude_ids=["r000"])
    expl = ds.ids("exploratory", "t")
    ds.assert_partition(expl[:5], "exploratory", "t")
    conf_id = next(i for i in ids if ds.partition_of(i) == "confirmatory")
    with pytest.raises(FenceError):
        ds.assert_partition(expl[:5] + [conf_id], "exploratory", "t")


def test_csv_helpers(split, ids, tmp_path):
    csv_path = tmp_path / "rows.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["OBSID", "text"])
        w.writeheader()
        for i in ids:
            w.writerow({"OBSID": i, "text": f"text {i}"})
            w.writerow({"OBSID": i, "text": f"more {i}"})
    assert unique_ids_from_csv(csv_path, "OBSID") == sorted(ids)
    ds = FencedDataset(STUDY, unique_ids_from_csv(csv_path, "OBSID"), seed=SEED, exclude_ids=["r000"])
    rows = list(read_csv_partition(ds, csv_path, "OBSID", "exploratory", "t"))
    assert rows and all(ds.partition_of(r["OBSID"]) == "exploratory" for r in rows)
    assert len(rows) == 2 * split[0].row_count
