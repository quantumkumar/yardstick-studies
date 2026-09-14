"""Tests for studies.llm — the LLM-study kit."""

from datetime import date

import pytest

from studies import llm


def test_template_hash_ignores_text_but_not_structure_or_parameters():
    base = {"model": "m", "max_tokens": 150, "temperature": 1.0,
            "system": [{"type": "text", "text": "You are a student."}],
            "messages": [{"role": "user", "content": "Teacher turn one"}]}
    same_structure = {**base, "system": [{"type": "text", "text": "You are a different student."}],
                      "messages": [{"role": "user", "content": "Teacher turn two"}]}
    assert llm.request_template_hash(base) == llm.request_template_hash(same_structure)
    assert llm.request_template_hash(base) != llm.request_template_hash({**base, "max_tokens": 200})
    more_turns = {**base, "messages": base["messages"] + [{"role": "assistant", "content": "x"}]}
    assert llm.request_template_hash(base) != llm.request_template_hash(more_turns)
    assert llm.system_prompt_hash("a") != llm.system_prompt_hash("b")


def test_model_pin_check():
    pin = llm.ModelPin("m", {"temperature": 1.0}, retirement_not_before="2027-02-17")
    assert pin.check(on=date(2026, 10, 26))["ok"]
    assert not pin.check(on=date(2027, 3, 1))["ok"]
    assert not llm.ModelPin("m", {}, retirement_not_before=None).check()["ok"]
    assert "not an ISO date" in llm.ModelPin("m", {}, retirement_not_before="Not sooner than February 17, 2027").check()["detail"]
    cfg = {"model": "m", "api_parameters": {"t": 1}, "model_retirement": {"retirement_date": "2027-02-17"}}
    assert llm.ModelPin.from_generation_config(cfg).retirement_not_before == "2027-02-17"


def test_generator_drift():
    a = {"generator": "anthropic-api", "model": "m1", "parameters": {"t": 1.0}, "request_template_hash": "h", "system_prompt_hash": "s"}
    assert llm.generator_drift(a, dict(a))["ok"]
    d = llm.generator_drift(a, {**a, "generator": "claude-cli", "model": "unknown"})
    assert not d["ok"] and set(d["differences"]) == {"generator", "model"}


def test_archive_records_hashes_and_verifies(tmp_path):
    arc = llm.GenerationArchive(tmp_path / "archive.jsonl", tmp_path / "archive-text.jsonl")
    arc.append("t1", "calibrated", 0, "I think it's twelve.", "m", "h", usage={"input_tokens": 100, "output_tokens": 8})
    arc.append("t1", "calibrated", 1, "Yes.", "m", "h", usage={"input_tokens": 120, "output_tokens": 2})
    man = arc.manifest()
    assert man["records"] == 2 and man["units"] == 1 and man["models"] == ["m"] and man["usage"]["input_tokens"] == 220
    assert arc.verify()["ok"]
    # tamper with the archived text
    text_path = tmp_path / "archive-text.jsonl"
    text_path.write_text(text_path.read_text().replace("twelve", "eleven"))
    assert not arc.verify()["ok"]


def test_spend_ledger_prices_and_enforces_cap(tmp_path):
    rates = {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75}
    ledger = llm.SpendLedger(tmp_path / "ledger.jsonl", rates, cap_usd=0.01)
    usd = ledger.record({"input_tokens": 1000, "output_tokens": 100, "cache_read_input_tokens": 5000})
    assert abs(usd - (1000 * 3 + 100 * 15 + 5000 * 0.3) / 1e6) < 1e-9
    with pytest.raises(llm.SpendCapExceeded):
        ledger.record({"input_tokens": 3_000_000})
    reloaded = llm.SpendLedger(tmp_path / "ledger.jsonl", rates, cap_usd=0.01)
    assert reloaded.calls == 1 and reloaded.summary()["within_cap"]
