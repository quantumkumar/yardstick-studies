"""Tests for studies.gates and studies.pressure."""

import json

from studies import gates, pressure


def test_open_placeholders():
    text = "k **[PI ruling]** and seed ________ and [OPEN -- x] and [after pilot]"
    holes = gates.open_placeholders(text)
    assert "[PI ruling]" in holes and "[OPEN" in holes and "[after pilot]" in holes and "________" in holes
    assert gates.open_placeholders("all decided") == []


def test_status_on_empty_study_points_at_g0(tmp_path):
    (tmp_path / "studies" / "empty").mkdir(parents=True)
    status = gates.study_status(tmp_path, tmp_path / "studies" / "empty")
    assert status["current"] == "G0"
    g0 = status["gates"][0]
    assert not g0["ok"] and all(c["hint"] for c in g0["checks"] if not c["ok"])
    assert "G0" in gates.render_status(status)


def test_leakage_audit():
    state = {
        "unlocked_at": "2026-10-20T00:00:00+00:00",
        "access_log": [
            {"timestamp": "2026-09-01T00:00:00+00:00", "partition": "confirmatory", "allowed": False, "reason": "negative test"},
            {"timestamp": "2026-09-15T00:00:00+00:00", "partition": "confirmatory", "allowed": True, "reason": "oops"},
            {"timestamp": "2026-10-20T00:00:00+00:00", "partition": "confirmatory", "allowed": True, "reason": "UNLOCK: frozen"},
            {"timestamp": "2026-10-21T00:00:00+00:00", "partition": "confirmatory", "allowed": True, "reason": "confirmatory run"},
        ],
    }
    r = pressure.leakage_audit(state)
    assert not r["ok"] and [e["reason"] for e in r["early_confirmatory_reads"]] == ["oops"]
    assert r["confirmatory_reads_denied"] == 1
    state["access_log"].pop(1)
    assert pressure.leakage_audit(state)["ok"]


def test_reconciliation_and_membership_and_determinism(tmp_path):
    r = pressure.reconciliation({"rows": {"clean": 100, "score": 100}, "students": {"clean": 12, "report": 11}})
    assert not r["ok"] and list(r["mismatches"]) == ["students"]
    m = pressure.membership_audit(lambda i: "exploratory" if i.startswith("e") else "confirmatory", ["e1", "e2", "c9"], "exploratory")
    assert not m["ok"] and m["wrong"] == {"c9": "confirmatory"}
    a = tmp_path / "a.json"; b = tmp_path / "b.json"; c = tmp_path / "c.json"
    a.write_text('{"x": 1}'); b.write_text('{"x": 1}'); c.write_text('{"x": 2}')
    assert pressure.determinism(a, b)["identical"] and not pressure.determinism(a, c)["identical"]


def test_effect_plausibility_flags_too_clean_and_too_wild():
    dims = {
        "fine": {"n": 100, "sd_d": 0.2, "mean_d": 0.01, "delta": 0.1, "verdict": "equivalent"},
        "tiny": {"n": 4, "sd_d": 0.2, "mean_d": 0.01, "delta": 0.1},
        "frozen": {"n": 100, "sd_d": 0.0, "mean_d": 0.0, "delta": 0.1, "verdict": "equivalent"},
        "wild": {"n": 100, "sd_d": 0.2, "mean_d": 5.0, "delta": 0.1},
    }
    r = pressure.effect_plausibility(dims)
    assert set(r["flags"]) == {"tiny", "frozen", "wild"}
