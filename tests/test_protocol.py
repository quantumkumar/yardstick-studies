"""Tests for protocol freeze — generic study protocol hashing and verification.

These tests are domain-agnostic: protocol content is an arbitrary dict.
"""
import json
import hashlib

import pytest

from studies.protocol import freeze_protocol, verify_protocol, FrozenProtocol


class TestFreezeProtocol:
    def test_freeze_produces_valid_hashes(self):
        content = {"design": "RCT", "population": "adults", "sample_size": 200}
        fp = freeze_protocol("Test Study", "v0.1", content)

        assert fp.protocol_id  # non-empty UUID
        assert fp.title == "Test Study"
        assert fp.version == "v0.1"
        assert fp.content == content
        assert len(fp.content_hash) == 64  # SHA-256 hex
        assert len(fp.seal_hash) == 64
        assert fp.frozen_at  # ISO timestamp present

    def test_freeze_deterministic(self):
        """Same content always produces the same content_hash."""
        content = {"x": 1, "y": [2, 3]}
        fp1 = freeze_protocol("S", "v1", content)
        fp2 = freeze_protocol("S", "v1", content)
        assert fp1.content_hash == fp2.content_hash

    def test_freeze_different_content_different_hash(self):
        fp1 = freeze_protocol("S", "v1", {"a": 1})
        fp2 = freeze_protocol("S", "v1", {"a": 2})
        assert fp1.content_hash != fp2.content_hash

    def test_freeze_empty_content_allowed(self):
        fp = freeze_protocol("Empty", "v0", {})
        assert fp.content == {}
        assert len(fp.content_hash) == 64

    def test_freeze_nested_content(self):
        content = {
            "hypotheses": [
                {"id": "H1", "text": "Treatment > control"},
                {"id": "H2", "text": "Dose-response linear"},
            ],
            "analysis_plan": {
                "primary": "t-test",
                "secondary": {"method": "ANCOVA", "covariates": ["age", "sex"]},
            },
        }
        fp = freeze_protocol("Nested", "v2", content)
        assert fp.content == content
        result = verify_protocol(fp)
        assert result["valid"] is True

    def test_freeze_protocol_hash_is_sha256_of_canonical_json(self):
        content = {"b": 2, "a": 1}
        fp = freeze_protocol("S", "v1", content)
        expected = hashlib.sha256(
            json.dumps(content, sort_keys=True, default=str).encode()
        ).hexdigest()
        assert fp.content_hash == expected

    def test_freeze_seal_hash_binds_content_to_timestamp(self):
        content = {"x": 42}
        fp = freeze_protocol("S", "v1", content)
        expected_seal = hashlib.sha256(
            f"{fp.content_hash}|{fp.frozen_at}".encode()
        ).hexdigest()
        assert fp.seal_hash == expected_seal


class TestVerifyProtocol:
    def test_verify_valid_protocol_passes(self):
        fp = freeze_protocol("Valid", "v1", {"k": "v"})
        result = verify_protocol(fp)
        assert result["valid"] is True
        assert result["checks"]["content_hash"] is True
        assert result["checks"]["seal_hash"] is True

    def test_verify_tampered_content_fails(self):
        fp = freeze_protocol("Test", "v1", {"original": True})
        # Tamper with content
        fp.content["original"] = False
        result = verify_protocol(fp)
        assert result["valid"] is False
        assert result["checks"]["content_hash"] is False

    def test_verify_tampered_timestamp_fails(self):
        fp = freeze_protocol("Test", "v1", {"data": 1})
        fp.frozen_at = "2000-01-01T00:00:00+00:00"
        result = verify_protocol(fp)
        assert result["valid"] is False
        assert result["checks"]["seal_hash"] is False

    def test_verify_tampered_seal_hash_fails(self):
        fp = freeze_protocol("Test", "v1", {"data": 1})
        fp.seal_hash = "0" * 64
        result = verify_protocol(fp)
        assert result["valid"] is False
        assert result["checks"]["seal_hash"] is False

    def test_verify_returns_all_check_keys(self):
        fp = freeze_protocol("Test", "v1", {"a": 1})
        result = verify_protocol(fp)
        assert "valid" in result
        assert "checks" in result
        assert "content_hash" in result["checks"]
        assert "seal_hash" in result["checks"]
