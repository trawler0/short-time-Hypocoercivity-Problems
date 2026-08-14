"""Controlled vocabularies for the research ledger.

The values in this module are deliberately conservative.  New experimental
vocabulary should use an ``x.`` event type or live inside an event's ``data``
object until it is stable enough to promote into the schema.
"""

from __future__ import annotations

SCHEMA_VERSION = 1
PROBLEM_SCHEMA_VERSION = 1

EVENT_TYPES = frozenset(
    {
        "problem.created",
        "problem.status_changed",
        "run.started",
        "run.ended",
        "approach.created",
        "approach.updated",
        "approach.promoted",
        "approach.blocked",
        "approach.abandoned",
        "claim.created",
        "claim.updated",
        "evidence.recorded",
        "verification.recorded",
        "artifact.recorded",
        "human.intervention",
        "selection.recorded",
        "resource.recorded",
        "protocol.gap",
        "annotation.added",
        "recovery.requested",
        "recovery.completed",
        "recovery.failed",
        "note",
    }
)

STAGES = frozenset(
    {
        "specify",
        "explore",
        "measure",
        "select",
        "intensify",
        "attack",
        "verify",
        "synthesize",
        "reallocate",
        "distill",
        "publish",
        "recover",
        "maintain",
        "unknown",
        "not_applicable",
    }
)

OUTCOMES = frozenset(
    {
        "success",
        "partial",
        "failure",
        "inconclusive",
        "blocked",
        "abandoned",
        "not_attempted",
        "not_applicable",
        "unknown",
    }
)

ACTOR_TYPES = frozenset({"human", "ai", "tool", "mixed", "unknown"})
SUBJECT_TYPES = frozenset(
    {
        "problem",
        "run",
        "approach",
        "claim",
        "evidence",
        "verification",
        "artifact",
        "intervention",
        "selection",
        "recovery",
        "resource",
        "other",
    }
)

PROVENANCE_MODES = frozenset({"live", "backfill", "recovery", "migration"})
REVIEW_STATUSES = frozenset(
    {"unreviewed", "human_reviewed", "machine_validated", "rejected"}
)
TEMPORAL_PRECISIONS = frozenset(
    {"exact", "minute", "hour", "day", "order_only", "unknown"}
)

VERIFICATION_STATUSES = frozenset(
    {
        "unverified",
        "attempted",
        "partial",
        "verified",
        "refuted",
        "inconclusive",
        "not_applicable",
        "unknown",
    }
)
NOVELTY_STATUSES = frozenset(
    {
        "unknown",
        "known",
        "rediscovered",
        "possibly_novel",
        "novel_after_search",
        "not_applicable",
    }
)
CAPTURE_COMPLETENESS = frozenset({"complete", "partial", "minimal", "failed"})

INTERVENTION_TYPES = frozenset(
    {
        "problem_selection",
        "prompting",
        "clarification",
        "correction",
        "method_suggestion",
        "counterexample",
        "verification",
        "distillation",
        "code_edit",
        "literature_input",
        "resource_change",
        "stopping",
        "restart",
        "other",
    }
)

SELECTION_DECISIONS = frozenset(
    {
        "continue",
        "pause",
        "abandon",
        "distill",
        "paper_candidate",
        "publish",
        "do_not_publish",
        "revisit_later",
        "unknown",
    }
)

EVIDENCE_TYPES = frozenset(
    {
        "formal",
        "lean",
        "machine_check",
        "symbolic",
        "numerical",
        "empirical",
        "literature",
        "audit",
        "counterexample_search",
        "human_expert",
        "other",
    }
)
EVIDENCE_RESULTS = frozenset(
    {"supports", "refutes", "inconclusive", "not_run", "error", "unknown"}
)

ARTIFACT_ROLES = frozenset(
    {
        "problem_statement",
        "problem_metadata",
        "human_log",
        "raw_log",
        "state_log",
        "proof",
        "lean",
        "code",
        "test",
        "data",
        "figure",
        "patch",
        "paper",
        "promising_note",
        "recovery_manifest",
        "recovery_prompt",
        "other",
    }
)

REGISTRATION_MODES = frozenset({"prospective", "retrospective", "recovered", "unknown"})

PROBLEM_STATUSES = frozenset(
    {
        "proposed",
        "active",
        "blocked",
        "paused",
        "partially_solved",
        "solved",
        "abandoned",
        "published",
        "archived",
        "unknown",
    }
)

IMMUTABLE_EVENT_FIELDS = frozenset(
    {
        "schema_version",
        "event_id",
        "problem_id",
        "sequence",
        "recorded_at",
        "prev_event_hash",
        "event_hash",
    }
)

DRAFT_FIELDS = frozenset(
    {
        "event_type",
        "summary",
        "occurred_at",
        "temporal_precision",
        "stage",
        "outcome",
        "run_id",
        "actor",
        "subject",
        "metrics",
        "evidence",
        "artifacts",
        "dependencies",
        "tags",
        "data",
        "provenance",
    }
)

MAX_SUMMARY_LENGTH = 4_000
MAX_EVENT_BYTES = 256 * 1024
PROBLEM_ID_PATTERN = r"^[a-z0-9][a-z0-9._-]{1,95}$"
EXTENSION_EVENT_PATTERN = r"^x\.[a-z0-9][a-z0-9._-]{1,95}$"
