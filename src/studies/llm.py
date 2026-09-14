"""LLM-study kit — what a study must record when a language model sits in the pipeline.

Generation cannot be reproduced, only audited. So the kit makes four things
checkable after the fact:

* :class:`GenerationArchive` — every generated turn appended with a content
  hash, the request-template hash, the model id, and the token usage;
  ``manifest()`` summarizes it and ``verify()`` re-hashes every record.
* :func:`request_template_hash` — SHA-256 of the canonical request with the
  variable content removed, so "same prompt structure" is a hash equality.
* :class:`ModelPin` — the registered generator: model id, parameters, and
  the retirement date from the provider's deprecation page; ``check()``
  says whether the pin is safe on a given date.
* :func:`generator_drift` — the exploratory pass and the confirmatory run
  must use the same generator, template, and parameters; any difference is
  listed. This is the check SDA-002 needed.
* :class:`SpendLedger` — token usage per call priced at registered rates,
  refusing to exceed a registered cap.

Everything writes plain JSON so the gate board and the pressure-test can
read it without importing the provider SDK.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

# Request fields that hold variable content rather than structure.
CONTENT_FIELDS = ("messages", "system", "system_text", "prompt", "content", "input", "text")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# ----------------------------------------------------------------------
# Request template
# ----------------------------------------------------------------------


def request_template_hash(request: Mapping, structure_of_content: bool = True) -> str:
    """Hash of the request with content stripped.

    Keeps model, parameters, tool definitions, and — when
    ``structure_of_content`` is true — the *shape* of the content fields
    (roles and the number of blocks) but not their text. Two runs with the
    same instructions packaged the same way get the same hash; changing
    max_tokens, the role structure, or the system prompt changes it.
    """
    skeleton: dict = {}
    for k, v in request.items():
        if k in CONTENT_FIELDS:
            if not structure_of_content:
                continue
            skeleton[k] = _shape(v)
        else:
            skeleton[k] = v
    return _sha256_text(_canonical(skeleton))


def _shape(v):
    if isinstance(v, list):
        return [_shape(x) for x in v]
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            if k in ("text", "content") and isinstance(x, str):
                out[k] = "<text>"
            elif k == "content":
                out[k] = _shape(x)
            else:
                out[k] = x
        return out
    if isinstance(v, str):
        return "<text>"
    return v


def system_prompt_hash(system_text: str) -> str:
    """The instructions themselves, hashed — registered alongside the template hash."""
    return _sha256_text(system_text)


# ----------------------------------------------------------------------
# Model pin
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class ModelPin:
    model: str
    parameters: dict
    retirement_not_before: str | None = None  # ISO date from the provider's deprecation page
    fallback: str | None = None

    def check(self, on: date | None = None) -> dict:
        today = on or datetime.now(timezone.utc).date()
        if not self.retirement_not_before:
            return {"ok": False, "detail": "no retirement date recorded; check the provider's deprecation page and pin it"}
        try:
            retire = date.fromisoformat(str(self.retirement_not_before)[:10])
        except ValueError:
            return {"ok": False, "detail": f"retirement date {self.retirement_not_before!r} is not an ISO date (YYYY-MM-DD)"}
        days = (retire - today).days
        return {
            "ok": days > 0,
            "days_until_retirement": days,
            "detail": f"{self.model} retires not before {self.retirement_not_before} ({days} days)",
        }

    @classmethod
    def from_generation_config(cls, cfg: Mapping) -> "ModelPin":
        return cls(
            model=cfg["model"],
            parameters=dict(cfg.get("api_parameters", {})),
            retirement_not_before=(cfg.get("model_retirement") or {}).get("retirement_date") if isinstance(cfg.get("model_retirement"), dict) else cfg.get("model_retirement"),
            fallback=cfg.get("fallback_model"),
        )


# ----------------------------------------------------------------------
# Generator drift
# ----------------------------------------------------------------------


def generator_drift(exploratory: Mapping, confirmatory: Mapping) -> dict:
    """Differences between two generator records ({model, parameters, request_template_hash, system_prompt_hash, generator}).

    Empty ``differences`` means the confirmatory run uses the generator the
    exploratory pass characterized. Anything else is a registration matter.
    """
    keys = ("generator", "model", "parameters", "request_template_hash", "system_prompt_hash")
    diffs = {}
    for k in keys:
        a, b = exploratory.get(k), confirmatory.get(k)
        if a != b:
            diffs[k] = {"exploratory": a, "confirmatory": b}
    return {"ok": not diffs, "differences": diffs}


# ----------------------------------------------------------------------
# Generation archive
# ----------------------------------------------------------------------


@dataclass
class ArchiveRecord:
    unit_id: str
    condition: str
    turn_index: int
    content_hash: str
    model: str
    request_template_hash: str
    usage: dict = field(default_factory=dict)
    request_id: str | None = None
    created_at: str = ""


class GenerationArchive:
    """Append-only JSONL archive of generated turns, with hashes but (optionally) without text.

    The public package carries the archive *without* text (pointers and
    hashes); the private working copy carries text alongside, in a separate
    file, so the analysis can run from it.
    """

    def __init__(self, path: str | Path, text_path: str | Path | None = None) -> None:
        self.path = Path(path)
        self.text_path = Path(text_path) if text_path else None

    def append(
        self,
        unit_id: str,
        condition: str,
        turn_index: int,
        text: str,
        model: str,
        request_template_hash: str,
        usage: Mapping | None = None,
        request_id: str | None = None,
    ) -> ArchiveRecord:
        rec = ArchiveRecord(
            unit_id=str(unit_id),
            condition=condition,
            turn_index=int(turn_index),
            content_hash=_sha256_text(text),
            model=model,
            request_template_hash=request_template_hash,
            usage=dict(usage or {}),
            request_id=request_id,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(_canonical(asdict(rec)) + "\n")
        if self.text_path:
            self.text_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.text_path, "a", encoding="utf-8") as f:
                f.write(_canonical({"unit_id": rec.unit_id, "condition": condition, "turn_index": rec.turn_index, "content_hash": rec.content_hash, "text": text}) + "\n")
        return rec

    def records(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(l) for l in self.path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def manifest(self) -> dict:
        recs = self.records()
        models = sorted({r["model"] for r in recs})
        templates = sorted({r["request_template_hash"] for r in recs})
        usage_keys = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
        usage = {k: sum(int(r.get("usage", {}).get(k, 0) or 0) for r in recs) for k in usage_keys}
        return {
            "records": len(recs),
            "units": len({r["unit_id"] for r in recs}),
            "conditions": sorted({r["condition"] for r in recs}),
            "models": models,
            "request_template_hashes": templates,
            "usage": usage,
            "archive_sha256": hashlib.sha256(self.path.read_bytes()).hexdigest() if self.path.exists() else None,
        }

    def verify(self) -> dict:
        """Re-hash every archived text against its record. Needs the text file."""
        if not self.text_path or not self.text_path.exists():
            return {"ok": False, "detail": "no text file to verify against"}
        by_key = {(r["unit_id"], r["condition"], r["turn_index"]): r["content_hash"] for r in self.records()}
        bad, seen = [], 0
        for line in self.text_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            t = json.loads(line)
            key = (t["unit_id"], t["condition"], t["turn_index"])
            seen += 1
            if by_key.get(key) != _sha256_text(t["text"]):
                bad.append(key)
        return {"ok": not bad and seen == len(by_key), "checked": seen, "records": len(by_key), "mismatched": bad[:10]}


# ----------------------------------------------------------------------
# Spend ledger
# ----------------------------------------------------------------------


class SpendCapExceeded(RuntimeError):
    pass


class SpendLedger:
    """Prices token usage at registered per-million rates and enforces a cap.

    ``rates`` keys: input, output, cache_read, cache_write (USD per million
    tokens). ``cap_usd`` is the registered cap; ``record`` raises before
    the cap is crossed so the runner stops with a logged reason.
    """

    def __init__(self, path: str | Path, rates: Mapping[str, float], cap_usd: float | None) -> None:
        self.path = Path(path)
        self.rates = dict(rates)
        self.cap_usd = cap_usd
        self.total_usd = 0.0
        self.calls = 0
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rec = json.loads(line)
                    self.total_usd += float(rec.get("usd", 0.0))
                    self.calls += 1

    def price(self, usage: Mapping) -> float:
        m = 1_000_000
        u = {k: int(usage.get(k, 0) or 0) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")}
        return (
            u["input_tokens"] * self.rates.get("input", 0.0)
            + u["output_tokens"] * self.rates.get("output", 0.0)
            + u["cache_read_input_tokens"] * self.rates.get("cache_read", 0.0)
            + u["cache_creation_input_tokens"] * self.rates.get("cache_write", 0.0)
        ) / m

    def record(self, usage: Mapping, note: str = "") -> float:
        usd = self.price(usage)
        if self.cap_usd is not None and self.total_usd + usd > self.cap_usd:
            raise SpendCapExceeded(f"spend cap {self.cap_usd:.2f} USD would be exceeded: {self.total_usd:.2f} + {usd:.4f}")
        self.total_usd += usd
        self.calls += 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(_canonical({"usd": round(usd, 6), "usage": dict(usage), "note": note, "at": datetime.now(timezone.utc).isoformat()}) + "\n")
        return usd

    def summary(self) -> dict:
        return {"calls": self.calls, "total_usd": round(self.total_usd, 4), "cap_usd": self.cap_usd, "within_cap": self.cap_usd is None or self.total_usd <= self.cap_usd}


# ----------------------------------------------------------------------
# Study-level record helpers (what the gate board reads)
# ----------------------------------------------------------------------


def generator_record(cfg: Mapping, system_text: str | None, request: Mapping | None, generator: str) -> dict:
    """The record a run writes so drift can be checked later."""
    return {
        "generator": generator,
        "model": cfg.get("model"),
        "parameters": dict(cfg.get("api_parameters", {})),
        "request_template_hash": request_template_hash(request) if request else cfg.get("request_template_hash"),
        "system_prompt_hash": system_prompt_hash(system_text) if system_text else None,
    }


def write_json(path: str | Path, data: Mapping) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
