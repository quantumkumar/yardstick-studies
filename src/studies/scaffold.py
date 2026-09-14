"""Scaffold a new study directory with the verifiability kernel in place.

    python scripts/new_study.py sda-003 --title "..." [--llm]

Creates ``studies/<id>/`` with: a README carrying the G0–G7 gate checklist;
``fence/protected-files.json`` and ``guard-baseline.json`` (guards on from
day one); ``config/results-schema.json`` (frozen at registration);
``config/generation.json`` when ``--llm`` is given; a registration draft
skeleton with the PI-ruling slots; ``registration/facts-sources.json``;
``deviations-log.md``; ``REPLAY.md``; ``data/`` ignored by git by default;
and a generated ``registration/facts.md``.

Nothing here is dataset-specific. The split (``studies.fence.create_split``)
is the study's first act after acquisition, and every read after that goes
through ``studies.access.FencedDataset``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from studies import registration, templates


README = """# {study_id} — {title}

Created with `scripts/new_study.py`. Program rules: `studies/SDA-execution-plan-DoD-gates.md`.

## Gates

- [ ] **G0 Kickoff** — data acquired, terms filed (`acquisition/`); split created with `studies.fence.create_split` and negative test passing; `fence/protected-files.json` lists the raw files; pipeline skeleton proven on the exploratory partition.
- [ ] **G1 Exploratory complete** — measures/models as versioned code with tests; exploratory analysis done; power inputs computed; `registration/facts.md` regenerated.
- [ ] **G2 Registration freeze** — `registration/osf-registration-draft.md` finalized with every PI ruling filled; `config/results-schema.json` frozen; repo tagged; lockfile committed; fence audit shows zero confirmatory reads; facts check green.
- [ ] **G3 Confirmatory run** — single scripted execution from the tag; fence unlocked by a logged action; results written only after `studies.guards.validate_results` passes; determinism proof.
- [ ] **G4 Evidence package** — public artifacts, pointers and hashes only for restricted data; `REPLAY.md` verified on a clean machine.
- [ ] **G5 Results handoff and pressure-test** — RDP with run logs, spend, deviations log.
- [ ] **G6–G7 Manuscript and preprint.**

## Rules that the tools enforce

- Raw data is read only through `studies.access.FencedDataset`; `tests/studies/test_study_guards.py` fails on any script that names a protected file directly.
- Files matching `fence/protected-files.json → restricted_content_globs` must never be tracked; `data/` is ignored by default.
- `registration/facts.md` is generated; CI fails if it is stale.
- The results file must validate against `config/results-schema.json`.
"""

PROTECTED_FILES = {
    "_doc": "Raw data files under the fence. Scripts may not name them directly; only allowed_readers may.",
    "protected_filenames": [],
    "allowed_readers": ["data_access.py"],
    "restricted_content_globs": ["data/**", "data/*"],
}

GUARD_BASELINE = {"direct_reads": {}, "tracked_restricted": []}

DATA_ACCESS = '''"""{study_id} — the one module that opens raw data.

Every other script imports from here and receives rows already filtered to a
partition by `studies.access.FencedDataset`, which logs the read and refuses
confirmatory rows while the fence is locked.
"""

from __future__ import annotations

from pathlib import Path

from studies.access import FencedDataset, read_csv_partition, unique_ids_from_csv

STUDY_ID = "{study_id}"
DATA_DIR = Path(__file__).resolve().parent / "data"
SEED = None  # [OPEN] committed at G0, must equal the seed in fence/*-manifest.json
ID_COLUMN = None  # [OPEN] the row-identifier column of the raw file
RAW_FILE = None  # [OPEN] e.g. DATA_DIR / "rows.csv"; add its name to fence/protected-files.json


def dataset() -> FencedDataset:
    ids = unique_ids_from_csv(RAW_FILE, ID_COLUMN)
    return FencedDataset(STUDY_ID, ids, seed=SEED)


def rows(partition: str, reason: str):
    """Rows of one partition, through the fence. Always give a real reason."""
    return read_csv_partition(dataset(), RAW_FILE, ID_COLUMN, partition, reason)
'''

RESULTS_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "{study_id} Confirmatory Results",
    "description": "Frozen at registration. Every registered quantity is named here before any number exists.",
    "type": "object",
    "required": ["study_id", "partition", "commit_tag", "run_timestamp"],
    "additionalProperties": False,
    "properties": {
        "study_id": {"type": "string", "const": "{study_id}"},
        "partition": {"type": "string", "enum": ["exploratory", "confirmatory"]},
        "commit_tag": {"type": "string"},
        "run_timestamp": {"type": "string", "format": "date-time"},
    },
}

GENERATION = {
    "_status": "OPEN — every field below is a registration parameter",
    "generator": "anthropic-api",
    "model": "[OPEN — pinned model id]",
    "model_retirement": "[OPEN — date from the deprecations page]",
    "api_parameters": {"max_tokens": None, "temperature": None},
    "conditions": [],
    "seed": "[OPEN — committed before confirmatory]",
    "confirmatory_volume": "[OPEN — registration parameter]",
    "request_template_hash": "[OPEN — sha256 of the canonical request minus content]",
    "cost_cap_usd": None,
    "replayability_model": "Archived generations with content hashes; analysis reproduces from the archive.",
}

REGISTRATION_DRAFT = """# {study_id} — OSF Registration text, v0.1 — draft

Cover note (not part of the registration). Drafted from the repository; every number cited from `registration/facts.md`, which is generated and checked in CI. Slots marked **[PI ruling]** are decided at G2.

## 1. Study information

Title: {title} [PI to confirm]

Author:

Research question.

Hypotheses.

## 2. Data description

Dataset, citation, terms, file hashes (see facts.md → Config files).

## 3. Variables

Measures by code version (see facts.md → Measures).

## 4. Knowledge of the data

4.1 Partition (see facts.md → Fence).
4.2 What has been seen.
4.3 What the exploratory pass changed, in order.
4.4 Exploratory results that informed the predictions.

## 5. Analysis plan

Primary test, bounds or effect sizes **[PI ruling]**, multiplicity rule, exclusions, sensitivities.

## 6. Sample size **[PI ruling]**

## 7. Deviations and amendments

`deviations-log.md` is part of this registration.

## 8. Confirmatory run and artifacts

Single scripted execution from the tag; results validated against `config/results-schema.json`; determinism proof; evidence package per G4.
"""

FACTS_SOURCES = {"tables": []}

FIGURE_LIST = "# {study_id} — figure list\n\nProposed at G1, ratified at G2. Every figure renders from the results file.\n\n| # | Figure | Source fields |\n|---|---|---|\n"

DEVIATIONS_LOG = """# {study_id} Deviations Log

| # | Date | Description | Registered Item Affected | Amendment Filed? |
|---|------|-------------|-------------------------|-----------------|
| (none) | -- | -- | -- | -- |

An empty log is a valid entry. An absent log is not.
"""

REPLAY = """# {study_id} — REPLAY

From a clean clone to the confirmatory results file, byte-identical.

1. Obtain the data through the official channel (never from this repository).
2. Verify file hashes against `registration/facts.md`.
3. Check out the registration tag.
4. Run the analysis stage from the archived generations / frozen inputs.
5. `diff` your results file against `output/confirmatory-results.json` — expected output: empty.
"""

DATA_GITIGNORE = "# Raw and derived data are never tracked. Pointers and hashes live in registration/facts.md.\n*\n!.gitignore\n"
OUTPUT_GITIGNORE = "# Only the frozen results file, run logs, and small reports are committed. Archives of generated text are not.\n*.jsonl\narchive/\n"


def _write(path: Path, content: str, overwrite: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError(path)
    path.write_text(content, encoding="utf-8")


def create_study(
    studies_root: str | Path, study_id: str, title: str, llm: bool = False, template: str | None = None
) -> Path:
    root = Path(studies_root) / study_id
    if root.exists():
        raise FileExistsError(f"{root} already exists")
    if template is not None and template not in templates.TEMPLATE_NAMES:
        raise ValueError(f"unknown template {template!r}; choose from {templates.TEMPLATE_NAMES}")
    sub = {"study_id": study_id, "title": title}

    _write(root / "README.md", README.format(**sub))
    _write(root / "fence" / "protected-files.json", json.dumps(PROTECTED_FILES, indent=2) + "\n")
    _write(root / "fence" / "guard-baseline.json", json.dumps(GUARD_BASELINE, indent=2) + "\n")
    _write(root / "data_access.py", DATA_ACCESS.format(**sub))
    schema = json.loads(json.dumps(RESULTS_SCHEMA).replace("{study_id}", study_id))
    _write(root / "config" / "results-schema.json", json.dumps(schema, indent=2) + "\n")
    if llm:
        _write(root / "config" / "generation.json", json.dumps(GENERATION, indent=2) + "\n")
    _write(root / "registration" / "osf-registration-draft.md", REGISTRATION_DRAFT.format(**sub))
    _write(root / "registration" / "facts-sources.json", json.dumps(FACTS_SOURCES, indent=2) + "\n")
    _write(root / "registration" / "figure-list.md", FIGURE_LIST.format(**sub))
    _write(root / "deviations-log.md", DEVIATIONS_LOG.format(**sub))
    _write(root / "REPLAY.md", REPLAY.format(**sub))
    _write(root / "data" / ".gitignore", DATA_GITIGNORE)
    _write(root / "output" / ".gitignore", OUTPUT_GITIGNORE)
    _write(root / "acquisition" / "access-terms.md", f"# {study_id} — data access terms\n\nDataset, license, access method, terms verbatim, file hashes.\n")
    (root / "scripts").mkdir(exist_ok=True)

    if template:
        for rel, content in templates.template_files(template, study_id, title).items():
            _write(root / rel, content, overwrite=True)
        sources = {"tables": [{"file": "config/analysis.json", "title": "Registered dimensions (config/analysis.json)", "path": "dimensions"}]}
        _write(root / "registration" / "facts-sources.json", json.dumps(sources, indent=2) + "\n", overwrite=True)
        with open(root / "README.md", "a", encoding="utf-8") as f:
            f.write(f"\n## Template: {template}\n\nAnalysis is library code: `python analysis.py --partition exploratory` reads "
                    "`output/per-unit.csv` (unit_id, condition, dimension, f_treatment, f_reference) with `config/analysis.json` "
                    "and writes a results file that must validate against `config/results-schema.json`.\n")

    registration.build(root)
    return root


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Create a new study directory.")
    ap.add_argument("study_id")
    ap.add_argument("--title", required=True)
    ap.add_argument("--llm", action="store_true", help="include config/generation.json for LLM-generated data")
    ap.add_argument("--template", choices=templates.TEMPLATE_NAMES, default=None, help="study shape: registration text, schema, analysis runner")
    ap.add_argument("--studies-root", default=None, help="defaults to the repo's studies/ directory")
    args = ap.parse_args(argv)
    studies_root = Path(args.studies_root) if args.studies_root else Path(__file__).resolve().parents[3] / "studies"
    root = create_study(studies_root, args.study_id, args.title, llm=args.llm, template=args.template)
    print(f"created {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
