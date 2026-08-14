# Data dictionary and statistical design

## Unit of observation

The primary unit is an **event**, nested within a **problem** and optionally a
**run**. Claims, approaches, artifacts, and interventions are stable subjects
identified by `subject.type` and `subject.id`. A run is one bounded attempt under
a reasonably coherent model, prompt, tool, and resource configuration.

Do not treat every event as independent in statistical models. Typical cluster
levels are problem, run, model/version, and human curator.

## Missingness

Absence of a field means **not recorded**. Use literal controlled values
`unknown` or `not_applicable` only when that fact itself is known. Never infer
failure from a missing success event or human independence from a missing
intervention event.

Recovered values carry:

- `provenance.mode = recovery`;
- `source_refs`;
- `extraction_confidence`;
- `review_status`;
- `recovery_bundle_id`.

Analyses should report sensitivity to excluding unreviewed recovery and to
confidence thresholds.

## Problem registration and sampling

`problem.metadata.registration_mode` is a first-class design variable:

- `prospective`: registered before the outcome of the tracked attempt was known;
- `retrospective`: pre-existing material adopted after some or all outcomes were
  already visible;
- `recovered`: the problem registration itself was reconstructed from sources;
- `unknown`: timing relative to the outcome cannot be established.

`problem.metadata.adopted_existing_directory` records whether registration
preserved an already non-empty `logs/<problem-id>/` directory. These fields are
exported as direct problem-table columns. Comparative success-rate analyses
should normally stratify by registration mode or restrict the primary analysis
to prospectively registered problems.

## Core event dimensions

### `event_type`

Stable controlled categories include problem/run lifecycle, approach and claim
updates, evidence/verification, artifacts, human interventions, selection,
resource usage, protocol gaps, annotations, and recovery. Experimental types
must begin with `x.`; promote them only after the concept stabilizes.

### `stage`

One of `specify`, `explore`, `measure`, `select`, `intensify`, `attack`,
`verify`, `synthesize`, `reallocate`, `distill`, `publish`, `recover`, or
`maintain`, plus explicit `unknown`/`not_applicable`.

### `outcome`

`success`, `partial`, `failure`, `inconclusive`, `blocked`, `abandoned`,
`not_attempted`, `not_applicable`, or `unknown`. The meaning is local to the
event. A successful counterexample search can coexist with an overall failed
proof approach.

### `metrics`

- `confidence`: subjective belief in the event's principal claim, `[0,1]`.
- `success_probability`: prospective probability that an approach will meet the
  success predicate, `[0,1]`.
- `information_value`: prospective value of the next test even if the route
  fails, `[0,1]`.
- `remaining_cost`: normalized subjective remaining effort, `[0,1]`.
- `impact_score`: curator's ordinal estimate: 0 none, 1 low, 2 moderate,
  3 high/paper-candidate, 4 potentially field-advancing.

These are not automatically calibrated or interval-scaled. Preserve the rater
identity and use them mainly for ranking, calibration studies, or ordinal models.

## Run fields

A `run.started` record should contain:

- exact actor/model/interface, with `unknown` rather than a guess;
- objective and input references;
- whether the attempt was genuinely independent;
- contamination group when agents shared context or candidate solutions.

A `run.ended` record should contain:

- outcome and end reason;
- `capture_completeness`: `complete`, `partial`, `minimal`, or `failed`;
- `protocol_complete` boolean;
- next action.

The pair makes denominators explicit and distinguishes research failure from
logging/tool failure.

## Claim fields

Use `data.claim_status` such as `candidate`, `conjecture`, `proved`, `refuted`,
or `superseded`; `data.verification_status`; `data.novelty_status`; and the
remaining gap. Novelty values should not advance beyond `possibly_novel` without
a documented literature search, and beyond `novel_after_search` without stating
scope and date.

## Verification fields

A `verification.recorded` event should use:

- `data.verification_type`: proof audit, Lean, symbolic, numerical,
  counterexample search, literature check, experiment, or another explicit type;
- `data.result`: verified, partial, refuted, inconclusive, error, or not run;
- tool/version, command/procedure, checked artifact, and independence;
- any residual uncertainty.

For Lean, distinguish generated source from successful kernel checking.

## Human intervention fields

`data.intervention_type` is one of problem selection, prompting, clarification,
correction, method suggestion, counterexample, verification, distillation, code
edit, literature input, resource change, stopping, restart, or other. Record
what changed and the target subject. This supports analyses of human effort,
causal routing, and selection effects.

## Selection fields

`data.decision` includes continue, pause, abandon, distill, paper candidate,
publish, do not publish, revisit later, or unknown. Record the decision before
or at the time it is acted upon where practical. Retrospective selection records
must use backfill/recovery provenance.

## Resource fields

Use `resource.recorded` with `data.resource_kind` and directly observed values
where available, for example:

- wall-clock seconds;
- model/API calls;
- input/output tokens;
- estimated monetary cost and currency;
- tool calls or solver time;
- human minutes;
- hardware/configuration.

Do not fabricate unavailable token or cost values. Resource changes within a
run should be separate records.

## Evidence table

Each evidence item has a type, result, `independent` flag, optional strength,
artifact path, and notes. “Independent” means independent failure modes, not
merely a second model call using the same prompt and context.

## Recommended derived outcomes

The exporter deliberately does not hard-code scientific success metrics.
Reasonable derived variables include:

- any partial or successful claim per registered problem;
- verified result per run and per unit resource;
- time/events from problem registration to first candidate, verification,
  distillation, and publication;
- failure/blocked-route recovery rates;
- fraction and type of runs receiving human intervention;
- promotion probability conditional on verification state;
- rate at which candidate novelty survives literature search;
- Lean formalization attempt and kernel-acceptance rates;
- protocol-complete and recovery-dependent fractions;
- model/version comparisons stratified by domain, problem source, resource
  envelope, and curator.

Pre-register analyses when making causal or comparative claims. The repository
is otherwise an observational, adaptively sampled research record with strong
post-selection and changing-model effects.
