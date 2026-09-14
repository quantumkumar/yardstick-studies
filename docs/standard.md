# A verifiable-study standard for LLM-in-the-loop research

*Version 0.1 — draft for review. This is the specification the YardStick study kernel enforces; the kernel is the reference implementation, and the SDA studies are its first cases.*

## 1. Purpose

A study is verifiable when a stranger can check its claims from the artifacts alone, without trusting the authors. Computational reproducibility has had that ideal for thirty years, and the institutions around it exist: preregistration and Registered Reports, the TOP guidelines, artifact badges, archived code with DOIs. What those institutions do not yet cover is a study in which a language model generates data or judgments inside the pipeline. Such a study has properties no earlier standard anticipated: the generator cannot be re-run to the same output; it is retired on a vendor's schedule; it may have memorized the public data it is being tested on; and its cost shapes the sample size. This document states what such a study must record and check so that its claims remain checkable after the model that produced the data is gone.

The standard is deliberately narrow. It specifies what an artifact can prove. It does not replace peer review, measurement validation, or independent replication on new data; §8 says what it leaves open.

## 2. Terms

- **Unit** — the entity a claim is made about (a transcript, a student, an item).
- **Partition** — the exploratory half, used for development, and the confirmatory half, used once for the registered test.
- **Fence** — the mechanism that separates the partitions and logs every read.
- **Generator** — the model, its parameters, and the exact request structure used to produce data.
- **Registration** — the frozen text and configuration that fix the hypotheses, measures, bounds, sample size, and analysis before confirmatory data exist.
- **Replay** — re-running the analysis from the archived inputs and obtaining a byte-identical results file.
- **Attestation** — a record of who performed a replay, on which revision, and whether it matched.

Requirement levels follow RFC 2119: MUST, SHOULD, MAY.

## 3. Data (gate G0)

3.1 The dataset's access terms MUST be recorded verbatim with the file hashes of what was received. Public packages MUST contain no restricted text; pointers (unit identifiers) and hashes stand in for it.

3.2 Units MUST be assigned to partitions by a deterministic function of a unit identifier and a committed seed, before any exploratory analysis. Both partition manifests (row counts, content hashes) MUST be committed.

3.3 Every read of the data MUST pass through one access path that records the partition, the reason, and the time, and that refuses confirmatory reads while the fence is locked. The refusal MUST be exercised at least once (a negative test) and the refusal logged.

3.4 Scripts MUST NOT name the raw data files directly. A repository check SHOULD fail on any script that does, and on any restricted file that is tracked.

## 4. Measures and exploratory work (gate G1)

4.1 Every measure MUST be versioned code. The registration MUST cite measure versions by code, not by description.

4.2 Every measure MUST declare its units and its direction ("higher means more questions"). A reported direction MUST be checked against the native units; folded or inverted scores (similarities, 1 − rate) MUST NOT be interpreted by sign.

4.3 A measure that classifies (a cue rule, a lexicon, a judge) MUST be validated against a human or ground-truth reference, and the registration MUST state its precision and recall against a registered floor. A measure that falls below the floor MAY be registered only as secondary, and the shortfall MUST be disclosed.

4.4 The checkable facts of the registration — manifests, configuration hashes, generator and parameters, measure versions, reference spreads, power tables — MUST be generated from the repository, and the generated file MUST be checked for staleness in continuous integration.

4.5 Exploratory or pilot data MUST exist for every registered condition before freeze. A condition with no pilot data cannot be powered and MUST NOT be registered.

## 5. Registration (gate G2)

5.1 The registration MUST fix, before any confirmatory data exist: hypotheses; the unit; the measures by version; bounds or effect sizes with their derivation; the test and its decision rule; the multiplicity rule; exclusions; the sample size with its derivation, including the assumed residual shift; the draw procedure and seed; and the results schema naming every quantity that will be reported.

5.2 The results schema MUST be strict: it names every field, rejects unknown fields, and is frozen with the registration.

5.3 The registration text MUST be frozen with a content hash, at a tagged revision, while the fence is locked. A freeze after unlock is not a pre-registration.

5.4 Deviations MUST be logged with the registered item they affect and whether an amendment was filed. An empty log is a valid entry; an absent log is not.

## 6. The generator (LLM-specific; gates G1–G3)

6.1 The generator MUST be pinned: model identifier, every sampling parameter, the request structure (hashed with content removed), and the instructions (hashed). The pin MUST record the provider's retirement date and a fallback policy.

6.2 The exploratory or pilot pass and the confirmatory run MUST use the same generator. A difference in any element of the pin is a registration matter and MUST be disclosed; a study MUST NOT power a confirmatory run on exploratory data from a different generator.

6.3 Every generated unit of text MUST be archived with a content hash, the template hash, the model identifier, and the token usage at the time it was produced. The public package carries the hashes; the text may be withheld under the data's terms.

6.4 Contamination MUST be tested when the source data are public: overlap between generated and real text against a chance baseline, and direct probes for memorized continuations, drawn from the exploratory partition. Results MUST be reported twice, with and without flagged units.

6.5 Spend MUST be priced per call at registered rates against a registered cap, and the runner MUST stop at the cap with a logged reason. Sample size chosen under a cap MUST say so.

## 7. Confirmatory run, verification, and replay (gates G3–G4)

7.1 The fence MUST be unlocked by a logged action that names the registration revision, and the confirmatory run MUST be a single scripted execution from that revision.

7.2 The results file MUST validate against the frozen schema before it is written; a non-conforming file MUST NOT be written.

7.3 The analysis stage MUST be deterministic from the archive: a rerun on a clean checkout yields a byte-identical results file. The results file MUST NOT contain timestamps or other run-specific values; those belong in the run log.

7.4 The evidence package MUST contain a manifest hashing every public artifact, replay instructions, the deviations log, and the archive hashes. It MUST NOT contain restricted data.

7.5 A replay attestation records the runner, the revision, the hashes of the frozen and replayed results, and whether they matched. A study SHOULD carry at least one attestation from a runner who is not an author. The author's own replay is a claim; an outsider's is evidence.

## 8. What conformance does not prove

A study can meet every requirement above and be wrong. Conformance proves that the claims are checkable, that the confirmatory data were untouched until the registration was frozen, that the reported numbers follow from the archived inputs, and that the generator is what the registration says it is. It does not prove that the measures capture what they are named for, that the bounds are the right bounds, that the sample generalizes beyond the corpus, or that the finding will replicate on new data. Replay is the floor of verifiability, not the ceiling of validity. A conforming study SHOULD say this in its own text.

## 9. Conformance claim

A study claims conformance by publishing its gate board with every check passing, its generated facts, its evidence manifest, and at least one independent replay attestation. A board with red lines is not a failed claim; it is an honest one, and a study MAY publish it as its current state. What a study MUST NOT do is publish a status it typed rather than computed.

## 10. Relationship to existing practice

This standard sits on top of preregistration (OSF) and Registered Reports, not beside them: §5 is what a Registered Report's Stage 1 already asks for, made checkable. It is compatible with TOP levels and with artifact-evaluation badges, which it can inform with the attestation record. Its contribution is §3's enforced fence, §4.4's generated facts, §6 in its entirety, and §7.5's attestations — the parts that did not exist for studies with a model in the loop.

## 11. Reference implementation

The YardStick study kernel (`packages/engine/studies`, exported as `yardstick-studies`) implements §3–§7 as `yardstick init | split | facts | check | freeze | unlock | analyze | verify | package | replay | attest | pressure`, with the gate board as the conformance record. `docs/first-study.md` walks a study through it; `tests/studies/test_walkthrough.py` executes the walk.
