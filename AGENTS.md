# Instructions for AI and coding agents

This repository is both a research notebook and a longitudinal dataset about
AI-assisted research. A mathematically useful result is not enough: the record
must also preserve how the result was produced, tested, selected, corrected,
or abandoned.

## Read these files first

1. `base_prompt.txt` — research and verification policy.
2. `docs/research-logging.md` — operational logging workflow.
3. `docs/data-dictionary.md` — controlled analytics fields and their meaning.
4. `schemas/event-draft-v1.schema.json` — machine-readable event-draft contract.

For an already registered problem, run:

```bash
python -m researchlog brief <problem-id>
```

The package has no runtime dependencies and is the only supported writer for
canonical ledgers.

## Repository map

- `logs/<problem-id>/problem.json`: stable problem metadata.
- `logs/<problem-id>/progress.*`: human-readable research state. Existing names
  such as `logs/problem-1/problem1-progress.tex` remain valid.
- `logs/<problem-id>/state.jsonl`: compact canonical event ledger.
- `logs/<problem-id>/raw/`: raw transcripts, calculations, or evidence retained
  for audit and recovery.
- `logs/<problem-id>/inbox/*.jsonl`: temporary event drafts supplied in a patch.
- `promising-approaches/`: distilled high-value candidate results.
- `problems/`: optional source problem statements and references when the project
  uses that directory.
- `researchlog/`: logging, validation, export, and recovery package.
- `schemas/`: versioned schemas and analytics completeness profile.

## Non-negotiable ledger rules

1. **Never edit `state.jsonl` by hand.** It is append-only and hash-chained.
2. Register a new problem before substantial work:

   ```bash
   python -m researchlog init <problem-id> --title "..." \
     --source-ref <repository-relative-source> --domain mathematics
   ```

   For a pre-existing non-empty `logs/<problem-id>/` directory, use
   `--adopt-existing --registration-mode retrospective`. Do not overwrite the
   human-readable log.
3. Every substantive AI attempt needs `run.started` and `run.ended` events.
4. Log failures, blocked approaches, negative checks, partial solutions, and
   tool/protocol failures—not only promoted results.
5. Identify the AI model exactly when possible. Use the literal value `unknown`
   rather than guessing.
6. Record human interventions as `human.intervention`, including problem
   selection, prompting, corrections, method suggestions, counterexamples,
   verification, resource changes, stopping, code edits, and distillation.
7. Link claims to evidence and artifacts with repository-relative paths. Do not
   put long transcripts or private chain-of-thought into event summaries.
8. A `run.ended` event must include `data.capture_completeness` and
   `data.protocol_complete`. A protocol failure is data; record it explicitly.
9. Recovery/backfill facts require source references and extraction confidence.
10. Run validation before returning a patch:

    ```bash
    python -m researchlog validate <problem-id>
    python -m researchlog doctor <problem-id>
    python -m unittest discover -s tests -v
    ```

## How an AI should supply logging changes in a patch

When execution and canonical append are safe, use `python -m researchlog` and
include the resulting ledger changes in the patch.

When the environment cannot safely append to the maintainer's local hash chain,
add one temporary file:

```text
logs/<problem-id>/inbox/<run-id>.jsonl
```

Generate a problem-aware starter batch when possible:

```bash
python -m researchlog template <problem-id>
```

The template conservatively marks the run as non-independent because it reads
existing problem state. Pass `--independent` only for a genuinely blind attempt,
and replace every `REPLACE_*` marker; the validator rejects unedited template
placeholders.

Each line must be an event draft. The first and last lines should normally be
`run.started` and `run.ended`. Do not include `event_id`, `problem_id`,
`sequence`, `recorded_at`, `prev_event_hash`, or `event_hash`; the package adds
those fields. After applying the patch, the maintainer runs:

```bash
python -m researchlog ingest-inbox <problem-id>
python -m researchlog validate <problem-id>
python -m researchlog doctor <problem-id>
```

The inbox file is removed only after a successful append. Ingestion is
idempotent by the SHA-256 of the draft bytes, so retrying after a crash between
append and deletion does not duplicate the batch. A pending inbox file is
reported by `doctor`.

Do not include unrelated generated PDFs, LaTeX auxiliaries, or replacements for
existing mathematical notes merely to update the structured log.

## Required records for common situations

### Candidate mathematical result

Create or update a stable `claim` subject. Record at least:

- `data.claim_status`;
- `data.verification_status`;
- `data.novelty_status`;
- the exact remaining gap;
- evidence records and artifact paths;
- confidence as a subjective value in `[0,1]`, never as proof.

### Verification, including Lean

Use `verification.recorded`. For Lean, set
`data.verification_type = "lean"`, identify the Lean version/toolchain and
checked artifact, and record whether the kernel actually accepted it. Generated
Lean source or a plan to formalize is not a successful kernel verification.

### Distillation or paper selection

Record both:

- `human.intervention` with `data.intervention_type = "distillation"`; and
- `selection.recorded` with a controlled `data.decision` such as `distill` or
  `paper_candidate`.

Link the distilled artifact as `promising_note` or `paper`. This preserves the
post-selection mechanism needed to study curation and survivorship bias.

### Failure or abandonment

Record the failed or blocked approach, the strongest fact that survived, the
exact failure mode, resource usage when observed, and the condition that could
justify reopening it. End the run with an honest outcome.

## Recovery when the protocol was absent or became insufficient

Do not rewrite old events to fit a newer analytics question. Create a recovery
bundle:

```bash
python -m researchlog recover prepare <problem-id>
# repository-wide when the protocol changed:
python -m researchlog recover prepare-all
```

The command inventories and hashes available raw/human logs, diagnoses
schema/profile gaps, and writes a frontier-model recovery prompt. A recovery
model must chase every listed source and emit historical event drafts,
`annotation.added` overlays, or `protocol.gap` records. It must not invent
unavailable timestamps, model versions, resources, human interventions,
verification, or novelty.

After human inspection:

```bash
python -m researchlog recover ingest <problem-id> \
  --bundle <bundle-id> --file recovered-events.jsonl \
  --review-status human_reviewed
```

Recovered data remains distinguishable through `provenance.mode = "recovery"`,
source references, extraction confidence, and review status.

## Schema evolution

Canonical event lines are immutable. Add experimental optional fields under
`data` first. Promote stable dimensions through a new versioned schema/profile.
Backfill with `annotation.added`; do not mass-edit old ledgers. Derived CSV,
JSONL, and SQLite exports are disposable and must be regenerated from canonical
logs.
