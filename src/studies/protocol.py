"""Protocol freeze — cryptographic pre-registration for any study.

Freezes an arbitrary protocol document (dict) with a timestamp, producing
an immutable record that proves the protocol existed before data collection.
Generic: works with any study domain.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class FrozenProtocol:
    """Immutable record of a frozen study protocol."""
    protocol_id: str          # UUID
    protocol_hash: str        # SHA-256 of canonical JSON (legacy alias for content_hash)
    frozen_at: str            # ISO 8601 UTC timestamp
    title: str                # Study title
    version: str              # e.g. "v0.1"
    content: dict             # The full protocol document (arbitrary structure)
    content_hash: str         # SHA-256 of content JSON (sorted keys)
    seal_hash: str            # SHA-256 of (content_hash + "|" + frozen_at)


def _content_hash(content: dict) -> str:
    """SHA-256 of canonical JSON representation of content."""
    canonical = json.dumps(content, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _seal_hash(content_hash: str, frozen_at: str) -> str:
    """SHA-256 binding content hash to timestamp."""
    return hashlib.sha256(f"{content_hash}|{frozen_at}".encode()).hexdigest()


def freeze_protocol(title: str, version: str, content: dict) -> FrozenProtocol:
    """Freeze a protocol. Content can be any dict."""
    frozen_at = datetime.now(timezone.utc).isoformat()
    ch = _content_hash(content)
    sh = _seal_hash(ch, frozen_at)

    return FrozenProtocol(
        protocol_id=str(uuid.uuid4()),
        protocol_hash=ch,
        frozen_at=frozen_at,
        title=title,
        version=version,
        content=content,
        content_hash=ch,
        seal_hash=sh,
    )


def verify_protocol(protocol: FrozenProtocol) -> dict:
    """Verify a frozen protocol's hash integrity.

    Recomputes all hashes and checks they match the stored values.
    Returns {valid: bool, checks: {content_hash: bool, seal_hash: bool}}.
    """
    expected_content_hash = _content_hash(protocol.content)
    content_ok = protocol.content_hash == expected_content_hash

    expected_seal = _seal_hash(protocol.content_hash, protocol.frozen_at)
    seal_ok = protocol.seal_hash == expected_seal

    return {
        "valid": content_ok and seal_ok,
        "checks": {
            "content_hash": content_ok,
            "seal_hash": seal_ok,
        },
    }
