"""OSF export — structured registration manifests from frozen protocols.

Produces a registration document suitable for OSF or any pre-registration
platform. Generic: works with any study content dict.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from studies.protocol import FrozenProtocol


# Standard OSF pre-registration section keys.
_STANDARD_SECTIONS = (
    "design",
    "population",
    "sample_size",
    "hypotheses",
    "primary_outcome",
    "secondary_outcomes",
    "analysis_plan",
    "stopping_rules",
    "exclusion_criteria",
)


@dataclass
class RegistrationManifest:
    """Structured registration document linked to a frozen protocol."""
    manifest_id: str           # UUID
    protocol_id: str           # Links to FrozenProtocol
    protocol_hash: str         # From the frozen protocol
    frozen_at: str             # When the protocol was frozen
    title: str                 # Study title
    version: str               # Protocol version
    sections: dict             # Structured sections extracted from content
    platform: str              # "yardstick"
    platform_version: str      # "1.0.0"
    export_format: str         # "osf-prereg-v1"
    exported_at: str           # When this manifest was generated
    manifest_hash: str         # SHA-256 of all fields above


def _compute_manifest_hash(manifest: RegistrationManifest) -> str:
    """SHA-256 of all manifest fields except manifest_hash itself."""
    payload = {
        "manifest_id": manifest.manifest_id,
        "protocol_id": manifest.protocol_id,
        "protocol_hash": manifest.protocol_hash,
        "frozen_at": manifest.frozen_at,
        "title": manifest.title,
        "version": manifest.version,
        "sections": manifest.sections,
        "platform": manifest.platform,
        "platform_version": manifest.platform_version,
        "export_format": manifest.export_format,
        "exported_at": manifest.exported_at,
    }
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _extract_sections(content: dict) -> dict:
    """Extract standard pre-registration sections from protocol content.

    Only includes keys that are present in content; never invents data.
    """
    sections = {}
    for key in _STANDARD_SECTIONS:
        if key in content:
            sections[key] = content[key]
    return sections


def export_registration(protocol: FrozenProtocol) -> RegistrationManifest:
    """Generate an OSF-compatible registration manifest from a frozen protocol."""
    manifest = RegistrationManifest(
        manifest_id=str(uuid.uuid4()),
        protocol_id=protocol.protocol_id,
        protocol_hash=protocol.content_hash,
        frozen_at=protocol.frozen_at,
        title=protocol.title,
        version=protocol.version,
        sections=_extract_sections(protocol.content),
        platform="yardstick",
        platform_version="1.0.0",
        export_format="osf-prereg-v1",
        exported_at=datetime.now(timezone.utc).isoformat(),
        manifest_hash="",  # placeholder, computed below
    )
    manifest.manifest_hash = _compute_manifest_hash(manifest)
    return manifest


def to_markdown(manifest: RegistrationManifest) -> str:
    """Render the manifest as a human-readable Markdown document."""
    lines = [
        f"# {manifest.title}",
        "",
        f"**Version:** {manifest.version}",
        f"**Frozen at:** {manifest.frozen_at}",
        f"**Protocol hash:** `{manifest.protocol_hash}`",
        f"**Manifest hash:** `{manifest.manifest_hash}`",
        f"**Platform:** {manifest.platform} v{manifest.platform_version}",
        f"**Export format:** {manifest.export_format}",
        "",
        "---",
        "",
    ]

    for key, value in manifest.sections.items():
        heading = key.replace("_", " ").title()
        lines.append(f"## {heading}")
        lines.append("")
        if isinstance(value, list):
            for item in value:
                lines.append(f"- {item}")
        elif isinstance(value, dict):
            lines.append(json.dumps(value, indent=2, default=str))
        else:
            lines.append(str(value))
        lines.append("")

    return "\n".join(lines)


def verify_manifest(manifest: RegistrationManifest) -> dict:
    """Verify manifest hash integrity and link to protocol.

    Returns {valid: bool, checks: {manifest_hash: bool, protocol_link: bool}}.
    """
    expected_hash = _compute_manifest_hash(manifest)
    hash_ok = manifest.manifest_hash == expected_hash

    # Protocol link check: protocol_hash and protocol_id are non-empty
    link_ok = bool(manifest.protocol_id) and bool(manifest.protocol_hash)

    return {
        "valid": hash_ok and link_ok,
        "checks": {
            "manifest_hash": hash_ok,
            "protocol_link": link_ok,
        },
    }
