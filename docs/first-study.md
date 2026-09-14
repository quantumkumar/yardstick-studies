# Your first study in 30 minutes

This walks a new user from nothing to a packaged, replayable study on synthetic data, using one command. Every step below is also executed by `tests/studies/test_walkthrough.py`, so if the tests are green, this document is true.

The command is `scripts/yardstick` (or `python -m studies.cli` with `packages/engine` on `PYTHONPATH`). Every verb prints what it did and, when something is missing, what to do next. `yardstick status <id>` shows the gate board at any time.

## The idea in one paragraph

A registered study makes claims a stranger can verify from the artifacts alone. Four things make that possible: the data is **fenced** (exploratory and confirmatory halves, with every read logged and confirmatory reads refused until the registration is frozen); the registration's numbers are **generated from the code**, not typed; the results file is checked against a **schema frozen before any number exists**; and a **replay** of the analysis from the public package produces a byte-identical results file. The gates G0–G4 are the order these happen in. The tool checks what artifacts can prove; the PI still rules on what they mean.

## G0 — Kickoff

```bash
scripts/yardstick init sda-003 --title "My first study" --llm --template paired-equivalence
```

`--template` picks a study shape and adds its analysis as library code: `paired-equivalence` (per unit, a treatment value and a reference value on several dimensions; the claim is that the mean difference sits inside a registered bound) or `paired-comparison` (two models or rules scored on the same held-out units; the claim is a registered direction of difference). A template contributes `config/analysis.json`, a strict `config/results-schema.json`, a registration draft with the analysis plan already written, a figure list, and `analysis.py`. Skip `--template` for a bare scaffold.

You get `studies/sda-003/` with: `README.md` (the gate checklist), `fence/` (guard config), `config/results-schema.json`, `config/generation.json` (only with `--llm`), `registration/` (draft skeleton, facts sources), `deviations-log.md`, `REPLAY.md`, `acquisition/access-terms.md`, and `data/` ignored by git so raw data can never be committed by accident.

Fill in `acquisition/access-terms.md` — dataset, license, the terms verbatim, file hashes. Put the raw file under `data/` and split it:

```bash
scripts/yardstick split sda-003 --csv studies/sda-003/data/rows.csv --id-column OBSID --seed 20261001
```

The split is deterministic (SHA-256 of `id:seed`), writes both manifests, runs the negative test (a confirmatory read is attempted, refused, and logged), declares the raw file as protected, and regenerates the facts file.

From here on, code reads the data only through the fence:

```python
from studies.access import FencedDataset, read_csv_partition, unique_ids_from_csv
ids = unique_ids_from_csv(RAW, "OBSID")
ds = FencedDataset("sda-003", ids, seed=20261001)          # verifies the split against the manifests
for row in read_csv_partition(ds, RAW, "OBSID", "exploratory", reason="measure development"):
    ...
```

A confirmatory read raises `FenceLockedError` and is logged; a hand-picked sample can be checked with `ds.assert_partition(ids, "exploratory", reason)`.

The scaffold's `data_access.py` is the one file allowed to name the raw file. Any other script that does gets caught:

```bash
scripts/yardstick check sda-003      # guards: direct reads, tracked restricted files; facts current?
```

## G1 — Exploratory complete

Write your measures or models as versioned code (each measure class with `name` and `version`), run the exploratory analysis, name every registered quantity in `config/results-schema.json`, and list the figures in `registration/figure-list.md`.

Three things the board now insists on before G1 is green:

- **Units and direction for every measure**, in `config/analysis.json` (template studies) or `config/measure-validation.json`. "Higher = more questions" is a declaration the board checks against; a folded or inverted score read by its sign is the mistake this prevents.
- **Validation for every classifier-type measure** — a cue rule, a lexicon, a judge — against a human or ground-truth reference, with precision and recall at or above the floor in `config/measure-validation.json`. Direct quantities (a count, a mean length) are marked `"kind": "direct"` and skip this.
- **For LLM studies, the exploratory generator recorded**: `output/exploratory-generator.json` from `studies.llm.generator_record` (model, parameters, template hash, prompt hash). The confirmatory run is checked against it.

Then:

```bash
scripts/yardstick facts sda-003      # regenerates registration/facts.md from the repo
scripts/yardstick status sda-003
```

`facts.md` carries the manifests, config hashes, model and parameters, measure versions by code, and any output tables you list in `registration/facts-sources.json`. Your registration text cites it instead of restating numbers. CI fails if it goes stale.

## G2 — Registration freeze

Fill every `[PI ruling]`, `[OPEN]`, `[after pilot]`, and `________` slot in `registration/osf-registration-draft.md`. Then:

```bash
scripts/yardstick freeze sda-003 --version v1.0
```

The freeze refuses if slots are open (use `--allow-open` only for a rehearsal), if the facts are stale, if the guards report new violations, if the fence is already unlocked, if any measure is undeclared or an unvalidated classifier, if any registered condition has no pilot or exploratory data (n ≥ 10), or — for LLM studies — if the pinned model has no recorded retirement date in the future. It writes `registration/protocol-frozen.json` (content hash, seal hash, the registration text, the facts and schema hashes, both manifests) and `registration/osf-export.md` to paste into OSF. Tag the repository and file the registration.

## G3 — Confirmatory run

```bash
scripts/yardstick unlock sda-003 --reason "registration frozen: sda-003-v1.0"
scripts/yardstick analyze sda-003 --partition confirmatory --commit-tag sda-003-v1.0
```

For LLM studies the run also writes `output/confirmatory-generator.json` (same call as the exploratory record; any drift is a red line) and prices every call into `output/spend-ledger.jsonl` with `studies.llm.SpendLedger`, which stops at the registered cap.

`analyze` (template studies) reads `output/per-unit.csv` — one row per unit × condition × dimension with `f_treatment` and `f_reference` — runs the registered analysis, and writes the results file only if it validates against the frozen schema. It refuses a confirmatory run while the fence is locked. The same input and tag give the same bytes.

Unlocking is refused before a freeze. Run your confirmatory script from the tag; it should write `output/confirmatory-results.json` and `output/confirmatory-run-log.json`. Then rerun the analysis stage on a clean checkout and compare:

```bash
scripts/yardstick verify sda-003 --compare /path/to/clean-rerun-results.json
```

`verify` validates the results file against the frozen schema and records the determinism check in `output/verification.json`. A results file with an unregistered field fails.

## G4 — Evidence package

```bash
scripts/yardstick package sda-003    # refuses if any restricted file would enter the package
scripts/yardstick replay sda-003 --results /path/from/a/stranger.json
```

`package` writes `output/evidence-manifest.json` (a hash of every file, the results hash, the protocol hash, the commit). `replay` compares a stranger's results file to the frozen one; identical means the empty diff is the certificate.

A replay only you have run is a claim. `yardstick attest` records who ran it, on which revision, and whether it matched:

```bash
scripts/yardstick attest sda-003 --results /path/from/a/stranger.json --runner-kind independent --runner-name "J. Doe, Lab X"
```

The `replay-attest` workflow does the same on a clean GitHub runner (`--runner-kind ci`). G4 stays red until at least one attestation from a runner who is not an author says identical.

## Pressure-test

```bash
scripts/yardstick pressure sda-003 --results output/confirmatory-results.json --rerun /path/clean.json \
    --counts counts.json --dimensions dims.json
```

Leakage audit (no confirmatory read before the unlock), determinism, reconciliation (every count that appears twice agrees), and effect plausibility (zero spread, tiny n, effects far outside the bound get flagged — results too clean are investigated like results too messy).

## The public record

`/research` in the app lists the program's registered studies from `studies/index.json` (title, summary, milestones the PI states) with a gate strip computed live from each study directory. `/research/<id>` shows every check as pass/fail for anyone, and the details and next steps when you are signed in. Nothing under `data/` is ever served.

## What the tool does not do

It does not decide bounds, sample size, or hypotheses; those are PI rulings, and the board shows them as open slots until they are filled. It does not replace the human pressure-test; it runs the mechanical half of it. And it cannot see reads that bypass the fence entirely — that is what the guards and the single data-access module are for.
