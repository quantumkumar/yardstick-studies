"""Generic research data fence infrastructure.

Prevents access to confirmatory data partitions before a study's
pre-registration is frozen.  Study-agnostic — no dataset-specific
logic belongs here.

Storage layout (relative to YARDSTICK_STUDIES_ROOT or repo-level `studies/`):
    {study_id}/fence/state.json
    {study_id}/fence/exploratory-manifest.json
    {study_id}/fence/confirmatory-manifest.json
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class FenceManifest:
    study_id: str
    partition_name: str  # "exploratory" or "confirmatory"
    row_count: int
    manifest_hash: str  # SHA-256 of the partition's sorted row identifiers
    created_at: str  # ISO 8601
    seed: int


@dataclass
class FenceState:
    study_id: str
    confirmatory_locked: bool
    locked_at: str
    unlocked_at: Optional[str] = None
    unlock_reason: Optional[str] = None
    access_log: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------


def _studies_root() -> Path:
    """Return the studies root directory, respecting env override for tests."""
    override = os.environ.get("YARDSTICK_STUDIES_ROOT")
    if override:
        return Path(override)
    # Default: repo-level studies/ directory (two levels up from this file)
    return Path(__file__).resolve().parent.parent.parent.parent / "studies"


def _fence_dir(study_id: str) -> Path:
    d = _studies_root() / study_id / "fence"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_manifest(manifest: FenceManifest) -> None:
    path = _fence_dir(manifest.study_id) / f"{manifest.partition_name}-manifest.json"
    path.write_text(json.dumps(asdict(manifest), indent=2))


def _read_manifest(study_id: str, partition: str) -> FenceManifest:
    path = _fence_dir(study_id) / f"{partition}-manifest.json"
    data = json.loads(path.read_text())
    return FenceManifest(**data)


def _write_state(state: FenceState) -> None:
    path = _fence_dir(state.study_id) / "state.json"
    path.write_text(json.dumps(asdict(state), indent=2))


def _read_state(study_id: str) -> FenceState:
    path = _fence_dir(study_id) / "state.json"
    data = json.loads(path.read_text())
    return FenceState(**data)


# ---------------------------------------------------------------------------
# Deterministic split logic
# ---------------------------------------------------------------------------


def _hash_id(row_id: str, seed: int) -> str:
    """SHA-256 of id+seed for deterministic partition assignment."""
    return hashlib.sha256(f"{row_id}:{seed}".encode()).hexdigest()


def _partition_hash(row_ids: list[str]) -> str:
    """SHA-256 digest of sorted row IDs — the manifest hash."""
    joined = "\n".join(sorted(row_ids))
    return hashlib.sha256(joined.encode()).hexdigest()


def _split_ids(
    row_ids: list[str],
    seed: int,
    exploratory_fraction: float,
    exclude_ids: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Deterministically split row_ids into (exploratory, confirmatory).

    Each ID is hashed with the seed; the first `exploratory_fraction` of
    the hash space goes to exploratory.  Excluded IDs are dropped first.
    """
    excluded_set = set(exclude_ids) if exclude_ids else set()
    active_ids = [rid for rid in row_ids if rid not in excluded_set]

    # Sort by hash for deterministic ordering
    scored = [(int(_hash_id(rid, seed), 16), rid) for rid in active_ids]
    scored.sort()

    cutoff = int(len(scored) * exploratory_fraction)
    exploratory = [rid for _, rid in scored[:cutoff]]
    confirmatory = [rid for _, rid in scored[cutoff:]]
    return exploratory, confirmatory


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_split(
    study_id: str,
    row_ids: list[str],
    seed: int,
    exploratory_fraction: float = 0.5,
    exclude_ids: list[str] | None = None,
) -> tuple[FenceManifest, FenceManifest]:
    """Split row IDs into exploratory and confirmatory partitions.

    Returns (exploratory_manifest, confirmatory_manifest).
    Deterministic: same seed + same IDs = same split.
    The confirmatory partition starts LOCKED.
    """
    if not 0.0 < exploratory_fraction < 1.0:
        raise ValueError("exploratory_fraction must be between 0 and 1 exclusive")

    exploratory_ids, confirmatory_ids = _split_ids(
        row_ids, seed, exploratory_fraction, exclude_ids
    )

    now = datetime.now(timezone.utc).isoformat()

    exp_manifest = FenceManifest(
        study_id=study_id,
        partition_name="exploratory",
        row_count=len(exploratory_ids),
        manifest_hash=_partition_hash(exploratory_ids),
        created_at=now,
        seed=seed,
    )
    conf_manifest = FenceManifest(
        study_id=study_id,
        partition_name="confirmatory",
        row_count=len(confirmatory_ids),
        manifest_hash=_partition_hash(confirmatory_ids),
        created_at=now,
        seed=seed,
    )

    _write_manifest(exp_manifest)
    _write_manifest(conf_manifest)

    state = FenceState(
        study_id=study_id,
        confirmatory_locked=True,
        locked_at=now,
    )
    _write_state(state)

    return exp_manifest, conf_manifest


def is_confirmatory_locked(study_id: str) -> bool:
    """Check if confirmatory partition is still locked."""
    state = _read_state(study_id)
    return state.confirmatory_locked


def request_access(study_id: str, partition: str, reason: str) -> bool:
    """Request access to a partition. Logs the attempt.

    Returns True if access granted (exploratory always granted,
    confirmatory only if unlocked).
    """
    if partition not in ("exploratory", "confirmatory"):
        raise ValueError(f"Unknown partition: {partition!r}")

    state = _read_state(study_id)
    allowed = partition == "exploratory" or not state.confirmatory_locked

    state.access_log.append(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "partition": partition,
            "reason": reason,
            "allowed": allowed,
        }
    )
    _write_state(state)
    return allowed


def unlock_confirmatory(study_id: str, reason: str) -> None:
    """Lift the fence after registration freeze. Logged."""
    if not reason or not reason.strip():
        raise ValueError("unlock_confirmatory requires a non-empty reason string")

    state = _read_state(study_id)
    if not state.confirmatory_locked:
        raise RuntimeError(f"Study {study_id} confirmatory is already unlocked")

    now = datetime.now(timezone.utc).isoformat()
    state.confirmatory_locked = False
    state.unlocked_at = now
    state.unlock_reason = reason

    state.access_log.append(
        {
            "timestamp": now,
            "partition": "confirmatory",
            "reason": f"UNLOCK: {reason}",
            "allowed": True,
        }
    )
    _write_state(state)


def get_access_log(study_id: str) -> list[dict]:
    """Return the full access log for audit."""
    state = _read_state(study_id)
    return state.access_log


def verify_split(study_id: str, row_ids: list[str], seed: int) -> dict:
    """Verify that a split is reproducible.

    Recomputes the split from row_ids + seed and compares manifest hashes
    against stored manifests. Returns {valid: bool, checks: {...}}.
    """
    exp_manifest = _read_manifest(study_id, "exploratory")
    conf_manifest = _read_manifest(study_id, "confirmatory")

    # Recompute using the same fraction
    total = exp_manifest.row_count + conf_manifest.row_count
    if total == 0:
        return {"valid": False, "checks": {"error": "empty partition"}}

    fraction = exp_manifest.row_count / total
    exploratory_ids, confirmatory_ids = _split_ids(row_ids, seed, fraction)

    recomputed_exp_hash = _partition_hash(exploratory_ids)
    recomputed_conf_hash = _partition_hash(confirmatory_ids)

    exp_match = recomputed_exp_hash == exp_manifest.manifest_hash
    conf_match = recomputed_conf_hash == conf_manifest.manifest_hash

    return {
        "valid": exp_match and conf_match,
        "checks": {
            "exploratory_hash_match": exp_match,
            "confirmatory_hash_match": conf_match,
            "stored_exploratory_hash": exp_manifest.manifest_hash,
            "recomputed_exploratory_hash": recomputed_exp_hash,
            "stored_confirmatory_hash": conf_manifest.manifest_hash,
            "recomputed_confirmatory_hash": recomputed_conf_hash,
            "seed": seed,
            "row_count": len(row_ids),
        },
    }
