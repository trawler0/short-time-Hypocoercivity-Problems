# Short-Time Hypocoercivity Problems

This repository is an open, AI-assisted research notebook for difficult
questions about the short- and long-time behaviour of hypocoercive evolution
equations. It is also designed as a longitudinal dataset about the research
process itself: complete and partial solutions, failed approaches, verification
attempts, formalization, human interventions, selection, and recovery of
missing provenance.

The central mathematical object in the current problem family is the squared
propagator norm

\[
\lVert P(t)\rVert^2,
\]

where \((P(t))_{t\geq 0}\) is a contraction semigroup. Future problems need not
be restricted to Problem 1 or to one specific method.

## AI use and epistemic status

Frontier models may generate approaches, calculations, code, proof drafts,
literature leads, and research logs. The human maintainer chooses problems,
controls prompts and resources, reviews outputs, corrects or redirects work, and
decides what to distill or publish.

AI generation is not evidence of correctness. Exploratory claims can remain
unverified, but their verification and novelty status must be recorded
explicitly. The maintainer is responsible for accurately reporting that status
and for the claims ultimately advanced in a paper; readers should independently
check notebook-level arguments before relying on them.

The repository therefore keeps two complementary records for each problem:

- a human-readable mathematical research log; and
- a compact, append-only JSONL provenance ledger for later statistics.

## Repository structure

```text
base_prompt.txt                    research and verification policy
AGENTS.md                          operational contract for AI/coding agents
logs/<problem-id>/problem.json     stable problem metadata
logs/<problem-id>/progress.*       human-readable research log
logs/<problem-id>/state.jsonl      append-only, hash-chained event ledger
logs/<problem-id>/raw/             raw material retained for audit/recovery
logs/<problem-id>/inbox/           temporary event drafts supplied in patches
promising-approaches/              distilled high-value candidate results
researchlog/                       logging, validation, export, recovery package
schemas/                           versioned schemas and analytics profile
docs/                              workflow and statistical data dictionary
```

Problem 1 keeps its existing human-readable source at
`logs/problem-1/problem1-progress.tex`. Its structured files are added beside
it; the existing distilled TeX/PDF files under `promising-approaches/` are not
replaced by the logging package.

The `.tex` and `.bib` files remain the mathematical sources of truth. Generated
CSV, consolidated JSONL, and SQLite datasets are disposable exports and are not
committed.

## Quick start

Python 3.10 or later is sufficient; the package has no runtime dependencies.
Installation is optional.

```bash
python -m researchlog --help
python -m researchlog brief problem-1
python -m researchlog validate
python -m researchlog doctor
python -m unittest discover -s tests -v
```

Register a genuinely new problem before its outcome is known:

```bash
python -m researchlog init problem-0002-example \
  --title "Example research problem" \
  --source-ref problems/example.tex \
  --domain mathematics --tag hypocoercivity \
  --actor-type human --actor-name "repository maintainer"
```

Register an existing non-empty legacy problem directory without overwriting its
human log:

```bash
python -m researchlog init problem-legacy \
  --title "Legacy problem" \
  --human-log legacy-progress.tex \
  --source-ref logs/problem-legacy/legacy-progress.tex \
  --adopt-existing --registration-mode retrospective \
  --actor-type human --actor-name "repository maintainer"
```

Prospective versus retrospective registration is exported as a first-class
variable so later analyses can account for survivorship and selection bias.

## One research run

Start and end every substantive attempt, including failures:

```bash
python -m researchlog begin problem-0002-example \
  --objective "Solve part (a), or isolate the exact obstruction" \
  --input-ref problems/example.tex \
  --actor-type ai --actor-name ChatGPT \
  --provider OpenAI --model "exact-model-version-or-unknown" \
  --interface chatgpt-pro --independent

python -m researchlog end problem-0002-example \
  --run-id <printed-run-id> --outcome partial --reason completed \
  --capture-completeness complete --protocol-complete \
  --summary "Recorded the strongest result and exact remaining gap."
```

Use `record`, `snapshot`, or an ingested JSONL batch for claims, approaches,
evidence, Lean checks, resource observations, human interventions, and selection
decisions. Long transcripts and proofs belong in artifacts, not in ledger
summaries.

## Remote ChatGPT/Codex patch workflow

A remote model should first inspect:

```bash
python -m researchlog brief <problem-id>
python -m researchlog template <problem-id>
```

The template conservatively marks the run as non-independent because it reads
existing problem state. Pass `--independent` only for a genuinely blind attempt,
and replace every `REPLACE_*` marker; the validator rejects unedited template
placeholders.

When it cannot safely append to the local hash chain, its patch adds only an
event-draft batch at:

```text
logs/<problem-id>/inbox/<run-id>.jsonl
```

After applying the patch locally:

```bash
python -m researchlog ingest-inbox <problem-id>
python -m researchlog validate <problem-id>
python -m researchlog doctor <problem-id>
```

Inbox ingestion is idempotent by the SHA-256 of the draft bytes. If a process
appends the batch but crashes before deleting the inbox file, retrying does not
duplicate the events.

See [`AGENTS.md`](AGENTS.md) for the exact agent contract and
[`docs/research-logging.md`](docs/research-logging.md) for worked examples.

## Analysis-ready exports

```bash
python -m researchlog export --output data/exports/latest --overwrite
python -m researchlog stats
```

The export contains normalized CSV tables, consolidated JSONL, and an indexed
SQLite database. Native, backfilled, migrated, and recovered observations remain
distinguishable. Append-only annotations can enrich historical events without
rewriting the canonical hash chain.

## Recovery when logging failed or the protocol evolved

If an old run was incompletely logged—or a later analytics profile requires
fields the old protocol did not collect—prepare a hashed recovery bundle and a
frontier-model extraction prompt:

```bash
python -m researchlog recover prepare <problem-id>
# or prepare every registered problem with an explicit/profile gap:
python -m researchlog recover prepare-all
```

The recovery model must inspect every listed raw/human source, cite exact source
locations, assign extraction confidence, and emit historical events,
`annotation.added` overlays, or explicit irrecoverable gaps. After human review:

```bash
python -m researchlog recover ingest <problem-id> \
  --bundle <bundle-id> --file recovered-events.jsonl \
  --review-status human_reviewed
```

This preserves the data-science branch even when the original protocol was
absent or later became insufficient, without making reconstructed facts look
contemporaneously recorded.

## Current scientific status

The present distilled candidate concerns a finite-dimensional matrix \(C\) with
positive-semidefinite Hermitian part and \(P(t)=e^{-Ct}\). If \(C\) has
hypocoercivity index \(m\geq 1\) and

\[
\lVert P(t)\rVert_2^2=1-c\,t^{2m+1}+O(t^{2m+2}),
\]

then the proposed result says that the coefficient of \(t^{2m+2}\) vanishes,
so that

\[
\lVert P(t)\rVert_2^2=1-c\,t^{2m+1}+O(t^{2m+3}).
\]

The proposed proof is in
`promising-approaches/rest_is_one_order_higher.tex`. Its structured bootstrap
record deliberately labels it as a candidate with unresolved verification and
novelty status; the logging infrastructure does not certify the theorem.
