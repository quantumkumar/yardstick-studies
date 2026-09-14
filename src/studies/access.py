"""Fenced data access — the only sanctioned way to read rows of a fenced dataset.

`fence.py` records the split (manifest hashes, lock state, access log) but
cannot stop a script from opening the raw file directly. This module closes
that gap for code that goes through it, and `guards.py` catches code that
does not.

Guarantees for callers of :class:`FencedDataset`:

* The split is re-derived from the row ids and seed on construction and
  checked against the stored manifests; a mismatch raises :class:`FenceError`
  before any row is served.
* Every read of a partition goes through :func:`fence.request_access` and is
  therefore logged in ``fence/state.json``.
* A confirmatory read while the fence is locked raises
  :class:`FenceLockedError`. It is never a silent ``False``.
* Rows outside the requested partition are never yielded, and excluded ids
  are never yielded from either partition.

Study-agnostic: the dataset is any iterable of rows plus a function that
returns the row's id.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Callable, Iterable, Iterator, TypeVar

from studies import fence

T = TypeVar("T")

PARTITIONS = ("exploratory", "confirmatory")


class FenceError(RuntimeError):
    """The stored fence does not match the split recomputed from the data."""


class FenceLockedError(PermissionError):
    """A confirmatory read was requested while the fence is locked."""


class FencedDataset:
    """Partition-aware view over a set of row ids.

    Args:
        study_id: Study whose fence (``studies/<id>/fence/``) governs access.
        row_ids: Every row id in the dataset, before exclusions.
        seed: The split seed recorded in the manifests.
        exploratory_fraction: Fraction used when the split was created.
        exclude_ids: Ids removed before splitting (e.g. known-bad transcripts).
        verify: Re-derive the split and compare with the stored manifests.
    """

    def __init__(
        self,
        study_id: str,
        row_ids: Iterable[str],
        seed: int,
        exploratory_fraction: float = 0.5,
        exclude_ids: Iterable[str] | None = None,
        verify: bool = True,
    ) -> None:
        self.study_id = study_id
        self.seed = seed
        self._excluded = set(exclude_ids or ())
        all_ids = [str(r) for r in row_ids]
        exploratory, confirmatory = fence._split_ids(
            all_ids, seed, exploratory_fraction, list(self._excluded)
        )
        self._members: dict[str, str] = {}
        for rid in exploratory:
            self._members[rid] = "exploratory"
        for rid in confirmatory:
            self._members[rid] = "confirmatory"
        self._partition_ids = {
            "exploratory": exploratory,
            "confirmatory": confirmatory,
        }
        if verify:
            self._verify_against_manifests()

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def _verify_against_manifests(self) -> None:
        for name in PARTITIONS:
            manifest = fence._read_manifest(self.study_id, name)
            ids = self._partition_ids[name]
            recomputed = fence._partition_hash(ids)
            if recomputed != manifest.manifest_hash:
                raise FenceError(
                    f"{self.study_id}: recomputed {name} manifest hash "
                    f"{recomputed[:12]}… does not match stored "
                    f"{manifest.manifest_hash[:12]}…; the split inputs "
                    "(row ids, seed, fraction, exclusions) differ from the "
                    "ones the fence was created with."
                )
            if len(ids) != manifest.row_count:
                raise FenceError(
                    f"{self.study_id}: {name} row count {len(ids)} != "
                    f"manifest {manifest.row_count}"
                )

    # ------------------------------------------------------------------
    # Membership (no logging: ids only, no row content)
    # ------------------------------------------------------------------

    def partition_of(self, row_id: str) -> str:
        """Return 'exploratory', 'confirmatory', or 'excluded'."""
        rid = str(row_id)
        if rid in self._excluded:
            return "excluded"
        try:
            return self._members[rid]
        except KeyError:
            raise KeyError(f"{rid!r} is not a row id of this dataset") from None

    def membership(self, row_ids: Iterable[str]) -> dict[str, str]:
        """Partition of each id — the check to run on any hand-picked sample."""
        return {str(r): self.partition_of(r) for r in row_ids}

    # ------------------------------------------------------------------
    # Reads (logged; raise while locked)
    # ------------------------------------------------------------------

    def _gate(self, partition: str, reason: str) -> None:
        if partition not in PARTITIONS:
            raise ValueError(f"Unknown partition: {partition!r}")
        if not reason or not reason.strip():
            raise ValueError("a non-empty reason is required for every read")
        allowed = fence.request_access(self.study_id, partition, reason)
        if not allowed:
            raise FenceLockedError(
                f"{self.study_id}: confirmatory partition is locked; "
                f"read refused and logged (reason given: {reason!r})"
            )

    def ids(self, partition: str, reason: str) -> list[str]:
        """Row ids of a partition. Logged; raises if locked."""
        self._gate(partition, reason)
        return list(self._partition_ids[partition])

    def filter_rows(
        self,
        rows: Iterable[T],
        key: Callable[[T], str],
        partition: str,
        reason: str,
    ) -> Iterator[T]:
        """Yield only the rows whose id is in ``partition``. Logged; raises if locked.

        Rows in the other partition and excluded rows are skipped, never
        materialized by the caller.
        """
        self._gate(partition, reason)
        for row in rows:
            if self._members.get(str(key(row))) == partition:
                yield row

    def assert_partition(self, row_ids: Iterable[str], partition: str, reason: str) -> None:
        """Raise unless every id is in ``partition``.

        Use this before generating, scoring, or otherwise touching a sample
        that was chosen by hand or by another script.
        """
        self._gate(partition, reason)
        wrong = {
            str(r): self.partition_of(r)
            for r in row_ids
            if self.partition_of(r) != partition
        }
        if wrong:
            raise FenceError(
                f"{self.study_id}: {len(wrong)} id(s) are not in {partition}: "
                f"{dict(list(wrong.items())[:10])}"
            )


# ----------------------------------------------------------------------
# CSV convenience
# ----------------------------------------------------------------------


def read_csv_partition(
    dataset: FencedDataset,
    csv_path: str | Path,
    id_column: str,
    partition: str,
    reason: str,
    encoding: str = "utf-8",
) -> Iterator[dict[str, str]]:
    """Stream the rows of one partition from a CSV, through the fence.

    The whole file is scanned (there is no other way to know each row's
    partition) but rows outside ``partition`` are dropped before they reach
    the caller.
    """
    with open(csv_path, newline="", encoding=encoding) as f:
        reader = csv.DictReader(f)
        yield from dataset.filter_rows(
            reader, key=lambda row: row.get(id_column, "").strip(), partition=partition, reason=reason
        )


def unique_ids_from_csv(csv_path: str | Path, id_column: str, encoding: str = "utf-8") -> list[str]:
    """All distinct ids in a CSV — the ``row_ids`` input for :class:`FencedDataset`.

    Reads ids only; no other column leaves this function.
    """
    seen: set[str] = set()
    with open(csv_path, newline="", encoding=encoding) as f:
        for row in csv.DictReader(f):
            rid = row.get(id_column, "").strip()
            if rid:
                seen.add(rid)
    return sorted(seen)
