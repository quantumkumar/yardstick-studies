"""Tests for OSF export — generating registration manifests from frozen protocols.

Domain-agnostic: works with any study content dict.
"""
import pytest

from studies.protocol import freeze_protocol
from studies.osf_export import (
    export_registration,
    to_markdown,
    verify_manifest,
    RegistrationManifest,
)


class TestExportRegistration:
    def _make_protocol(self, **extra):
        content = {
            "design": "randomized controlled trial",
            "population": "university students aged 18-25",
            "sample_size": 120,
            "hypotheses": ["Treatment group shows higher gain scores"],
            "primary_outcome": "post-test score",
            "secondary_outcomes": ["engagement minutes", "satisfaction rating"],
            "analysis_plan": "Two-sample t-test with alpha=0.05",
            "stopping_rules": "Futility at interim if p > 0.5",
            "exclusion_criteria": ["incomplete consent", "prior exposure"],
        }
        content.update(extra)
        return freeze_protocol("RCT Study", "v1.0", content)

    def test_export_links_to_protocol(self):
        fp = self._make_protocol()
        manifest = export_registration(fp)
        assert manifest.protocol_id == fp.protocol_id
        assert manifest.protocol_hash == fp.content_hash
        assert manifest.frozen_at == fp.frozen_at

    def test_export_extracts_standard_sections(self):
        fp = self._make_protocol()
        manifest = export_registration(fp)
        assert "design" in manifest.sections
        assert "population" in manifest.sections
        assert "sample_size" in manifest.sections
        assert "hypotheses" in manifest.sections
        assert "primary_outcome" in manifest.sections
        assert "analysis_plan" in manifest.sections

    def test_export_omits_missing_sections(self):
        fp = freeze_protocol("Minimal", "v0.1", {"design": "observational"})
        manifest = export_registration(fp)
        assert "design" in manifest.sections
        assert "hypotheses" not in manifest.sections
        assert "stopping_rules" not in manifest.sections

    def test_export_manifest_hash_valid(self):
        fp = self._make_protocol()
        manifest = export_registration(fp)
        assert len(manifest.manifest_hash) == 64
        result = verify_manifest(manifest)
        assert result["valid"] is True

    def test_export_platform_metadata(self):
        fp = self._make_protocol()
        manifest = export_registration(fp)
        assert manifest.platform == "yardstick"
        assert manifest.platform_version == "1.0.0"
        assert manifest.export_format == "osf-prereg-v1"

    def test_export_arbitrary_content_works(self):
        """Content with non-standard keys still exports fine."""
        fp = freeze_protocol("Custom", "v1", {
            "custom_field": "anything",
            "nested": {"deep": True},
            "design": "quasi-experimental",
        })
        manifest = export_registration(fp)
        assert "design" in manifest.sections
        # Non-standard keys are not in sections
        assert "custom_field" not in manifest.sections


class TestToMarkdown:
    def _make_manifest(self):
        fp = freeze_protocol("Markdown Test", "v2.0", {
            "design": "pre-post with control",
            "hypotheses": ["H1: gain > 0", "H2: effect size > 0.3"],
            "sample_size": 50,
        })
        return export_registration(fp)

    def test_to_markdown_includes_title(self):
        manifest = self._make_manifest()
        md = to_markdown(manifest)
        assert "Markdown Test" in md

    def test_to_markdown_includes_sections(self):
        manifest = self._make_manifest()
        md = to_markdown(manifest)
        assert "## Design" in md or "## design" in md.lower()
        assert "pre-post with control" in md

    def test_to_markdown_includes_hashes(self):
        manifest = self._make_manifest()
        md = to_markdown(manifest)
        assert manifest.protocol_hash in md
        assert manifest.manifest_hash in md


class TestVerifyManifest:
    def test_verify_manifest_passes(self):
        fp = freeze_protocol("V", "v1", {"design": "RCT"})
        manifest = export_registration(fp)
        result = verify_manifest(manifest)
        assert result["valid"] is True
        assert result["checks"]["manifest_hash"] is True
        assert result["checks"]["protocol_link"] is True

    def test_verify_tampered_manifest_fails(self):
        fp = freeze_protocol("V", "v1", {"design": "RCT"})
        manifest = export_registration(fp)
        manifest.title = "TAMPERED"
        result = verify_manifest(manifest)
        assert result["valid"] is False
        assert result["checks"]["manifest_hash"] is False
