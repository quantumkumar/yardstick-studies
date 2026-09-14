# yardstick-studies

The study kernel behind YardStick's pre-registered studies, as a standalone package.
It enforces the checkable half of a verifiable study:

- **fenced data** — a seeded split, every read logged, confirmatory reads refused until the registration is frozen (`studies.access.FencedDataset`);
- **guards** — CI fails on scripts that bypass the fence, on restricted content tracked in git, and on results files that break their frozen schema;
- **generated registration facts** — hashes, versions, manifests, and tables compiled from the repository, checked for staleness;
- **a gate board** — G0–G4 as checks with hints, printed by the CLI or served by an API;
- **templates** — paired-equivalence and paired-comparison designs as library code with strict results schemas;
- **the LLM-study kit** — generation archive with content hashes, request-template hashing, pinned-model retirement check, generator-drift check, spend ledger;
- **replay attestations** — who replayed what, on which ref, and whether the bytes matched.

Start with `docs/first-study.md`. The standard the kernel enforces is written in `docs/standard.md`.

```bash
pip install -e .[dev]
yardstick init sda-003 --title "..." --llm --template paired-equivalence
python -m pytest -q
```

Exported from the YardStick repository by `scripts/export_kernel.py`; the engine and the studies themselves are not part of this package.
