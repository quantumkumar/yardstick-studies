# Changelog

## 0.1.0 — 2026-09-14

First public release, exported from the YardStick repository where the kernel was developed against two pre-registered studies (SDA-001, SDA-002).

- Fenced data access (`studies.access`), CI guards (`studies.guards`), generated registration facts (`studies.registration`), gate board (`studies.gates`), pressure-test checks (`studies.pressure`).
- Lifecycle CLI (`yardstick init | split | facts | check | status | freeze | unlock | analyze | verify | package | replay | attest | pressure`).
- Templates: paired-equivalence, paired-comparison (`studies.templates`, `studies.paired_analysis`, `studies.equivalence`).
- LLM-study kit (`studies.llm`): generation archive, request-template hash, model pin, generator drift, spend ledger.
- Replay attestations and the `replay-attest` workflow.
- The standard (`docs/standard.md`) and the first-study walkthrough (`docs/first-study.md`), executed by `tests/test_walkthrough.py`.
