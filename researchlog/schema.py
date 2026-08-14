"""Small, dependency-free validators mirroring the JSON Schemas in ``schemas/``."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Iterable

from .constants import (
    ACTOR_TYPES,
    ARTIFACT_ROLES,
    DRAFT_FIELDS,
    EVIDENCE_RESULTS,
    EVIDENCE_TYPES,
    EVENT_TYPES,
    EXTENSION_EVENT_PATTERN,
    IMMUTABLE_EVENT_FIELDS,
    MAX_EVENT_BYTES,
    MAX_SUMMARY_LENGTH,
    OUTCOMES,
    PROBLEM_ID_PATTERN,
    REGISTRATION_MODES,
    PROBLEM_STATUSES,
    PROVENANCE_MODES,
    REVIEW_STATUSES,
    SCHEMA_VERSION,
    STAGES,
    SUBJECT_TYPES,
    TEMPORAL_PRECISIONS,
)

_ACTOR_FIELDS = {
    "type",
    "name",
    "model",
    "provider",
    "interface",
    "session_id",
    "agent_id",
}
_SUBJECT_FIELDS = {"type", "id"}
_METRIC_FIELDS = {
    "confidence",
    "success_probability",
    "information_value",
    "remaining_cost",
    "impact_score",
}
_EVIDENCE_FIELDS = {
    "type",
    "result",
    "independent",
    "strength",
    "artifact_path",
    "notes",
    "data",
}
_ARTIFACT_FIELDS = {
    "path",
    "role",
    "sha256",
    "size_bytes",
    "mime",
    "description",
}
_PROVENANCE_FIELDS = {
    "mode",
    "source_refs",
    "source_hashes",
    "extraction_confidence",
    "review_status",
    "recovery_bundle_id",
    "repo_revision",
    "base_prompt_sha256",
    "input_sha256",
    "notes",
}
_CANONICAL_FIELDS = DRAFT_FIELDS | IMMUTABLE_EVENT_FIELDS

_PLACEHOLDER_PATTERN = re.compile(
    r"(?:REPLACE_ME|REPLACE_WITH_[A-Z0-9_]+|replace-with-[a-z0-9-]+|identify-exact-model)"
)


def _placeholder_errors(value: Any, where: str = "event") -> list[str]:
    errors: list[str] = []
    if isinstance(value, str):
        if _PLACEHOLDER_PATTERN.search(value):
            errors.append(f"{where} contains an unedited template placeholder: {value!r}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            errors.extend(_placeholder_errors(item, f"{where}[{index}]"))
    elif isinstance(value, dict):
        for key, item in value.items():
            errors.extend(_placeholder_errors(item, f"{where}.{key}"))
    return errors


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _is_timestamp(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except (TypeError, ValueError):
        return False


def _unknown_keys(value: dict[str, Any], allowed: set[str] | frozenset[str], where: str) -> list[str]:
    return [f"{where}: unknown field {key!r}" for key in sorted(set(value) - set(allowed))]


def _validate_relative_path(value: Any, where: str) -> list[str]:
    if not isinstance(value, str) or not value:
        return [f"{where} must be a non-empty repository-relative path"]
    if "\\" in value:
        return [f"{where} must use '/' separators"]
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        return [f"{where} must not be absolute or contain '..'"]
    return []


def validate_problem_id(problem_id: str) -> list[str]:
    if not isinstance(problem_id, str) or not re.fullmatch(PROBLEM_ID_PATTERN, problem_id):
        return [
            "problem_id must match "
            f"{PROBLEM_ID_PATTERN!r} (lower-case letters, digits, '.', '_' and '-')"
        ]
    return []


def validate_problem(problem: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(problem, dict):
        return ["problem metadata must be a JSON object"]
    allowed = {
        "schema_version",
        "problem_id",
        "title",
        "status",
        "created_at",
        "updated_at",
        "domains",
        "tags",
        "source_refs",
        "human_log",
        "description",
        "owners",
        "metadata",
    }
    errors += _unknown_keys(problem, allowed, "problem")
    for required in ("schema_version", "problem_id", "title", "status", "created_at"):
        if required not in problem:
            errors.append(f"problem: missing required field {required!r}")
    if problem.get("schema_version") != 1:
        errors.append("problem.schema_version must be 1")
    if "problem_id" in problem:
        errors += validate_problem_id(problem["problem_id"])
    if not isinstance(problem.get("title"), str) or not problem.get("title", "").strip():
        errors.append("problem.title must be a non-empty string")
    if problem.get("status") not in PROBLEM_STATUSES:
        errors.append(f"problem.status must be one of {sorted(PROBLEM_STATUSES)}")
    for field in ("created_at", "updated_at"):
        if field in problem and not _is_timestamp(problem[field]):
            errors.append(f"problem.{field} must be an RFC 3339 timestamp")
    for field in ("domains", "tags", "source_refs", "owners"):
        if field in problem and (
            not isinstance(problem[field], list)
            or not all(isinstance(item, str) and item for item in problem[field])
        ):
            errors.append(f"problem.{field} must be an array of non-empty strings")
    if "human_log" in problem:
        errors += _validate_relative_path(problem["human_log"], "problem.human_log")
    if "metadata" in problem:
        metadata = problem["metadata"]
        if not isinstance(metadata, dict):
            errors.append("problem.metadata must be an object")
        else:
            registration_mode = metadata.get("registration_mode")
            if registration_mode is not None and registration_mode not in REGISTRATION_MODES:
                errors.append(
                    "problem.metadata.registration_mode must be one of "
                    f"{sorted(REGISTRATION_MODES)}"
                )
            adopted = metadata.get("adopted_existing_directory")
            if adopted is not None and not isinstance(adopted, bool):
                errors.append("problem.metadata.adopted_existing_directory must be boolean")
    return errors


def validate_event_draft(draft: Any, *, canonical: bool = False) -> list[str]:
    """Validate an event draft or a canonical event.

    The validator is intentionally stricter than generic JSON parsing so that
    misspelled analytics fields fail early rather than silently fragmenting the
    future dataset.
    """

    errors: list[str] = []
    if not isinstance(draft, dict):
        return ["event must be a JSON object"]

    allowed = _CANONICAL_FIELDS if canonical else DRAFT_FIELDS
    errors += _unknown_keys(draft, allowed, "event")

    for required in ("event_type", "summary"):
        if required not in draft:
            errors.append(f"event: missing required field {required!r}")

    event_type = draft.get("event_type")
    if not isinstance(event_type, str) or not (
        event_type in EVENT_TYPES or re.fullmatch(EXTENSION_EVENT_PATTERN, event_type)
    ):
        errors.append(
            "event.event_type must be a controlled value or an extension beginning with 'x.'"
        )

    summary = draft.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        errors.append("event.summary must be a non-empty string")
    elif len(summary) > MAX_SUMMARY_LENGTH:
        errors.append(f"event.summary exceeds {MAX_SUMMARY_LENGTH} characters")

    if "occurred_at" in draft and draft["occurred_at"] is not None and not _is_timestamp(draft["occurred_at"]):
        errors.append("event.occurred_at must be null or an RFC 3339 timestamp")
    if "temporal_precision" in draft and draft["temporal_precision"] not in TEMPORAL_PRECISIONS:
        errors.append(f"event.temporal_precision must be one of {sorted(TEMPORAL_PRECISIONS)}")
    if "stage" in draft and draft["stage"] not in STAGES:
        errors.append(f"event.stage must be one of {sorted(STAGES)}")
    if "outcome" in draft and draft["outcome"] not in OUTCOMES:
        errors.append(f"event.outcome must be one of {sorted(OUTCOMES)}")
    if "run_id" in draft and (not isinstance(draft["run_id"], str) or not draft["run_id"]):
        errors.append("event.run_id must be a non-empty string")

    if "actor" in draft:
        actor = draft["actor"]
        if not isinstance(actor, dict):
            errors.append("event.actor must be an object")
        else:
            errors += _unknown_keys(actor, _ACTOR_FIELDS, "event.actor")
            if actor.get("type") not in ACTOR_TYPES:
                errors.append(f"event.actor.type must be one of {sorted(ACTOR_TYPES)}")
            for key, value in actor.items():
                if key != "type" and (not isinstance(value, str) or not value):
                    errors.append(f"event.actor.{key} must be a non-empty string")

    if "subject" in draft:
        subject = draft["subject"]
        if not isinstance(subject, dict):
            errors.append("event.subject must be an object")
        else:
            errors += _unknown_keys(subject, _SUBJECT_FIELDS, "event.subject")
            if subject.get("type") not in SUBJECT_TYPES:
                errors.append(f"event.subject.type must be one of {sorted(SUBJECT_TYPES)}")
            if not isinstance(subject.get("id"), str) or not subject.get("id"):
                errors.append("event.subject.id must be a non-empty string")

    if "metrics" in draft:
        metrics = draft["metrics"]
        if not isinstance(metrics, dict):
            errors.append("event.metrics must be an object")
        else:
            errors += _unknown_keys(metrics, _METRIC_FIELDS, "event.metrics")
            for key in ("confidence", "success_probability", "information_value", "remaining_cost"):
                if key in metrics and (not _is_number(metrics[key]) or not 0 <= metrics[key] <= 1):
                    errors.append(f"event.metrics.{key} must be a finite number in [0, 1]")
            if "impact_score" in metrics and (
                not isinstance(metrics["impact_score"], int)
                or isinstance(metrics["impact_score"], bool)
                or not 0 <= metrics["impact_score"] <= 4
            ):
                errors.append("event.metrics.impact_score must be an integer from 0 to 4")

    if "evidence" in draft:
        evidence = draft["evidence"]
        if not isinstance(evidence, list):
            errors.append("event.evidence must be an array")
        else:
            for index, item in enumerate(evidence):
                where = f"event.evidence[{index}]"
                if not isinstance(item, dict):
                    errors.append(f"{where} must be an object")
                    continue
                errors += _unknown_keys(item, _EVIDENCE_FIELDS, where)
                if item.get("type") not in EVIDENCE_TYPES:
                    errors.append(f"{where}.type must be one of {sorted(EVIDENCE_TYPES)}")
                if item.get("result") not in EVIDENCE_RESULTS:
                    errors.append(f"{where}.result must be one of {sorted(EVIDENCE_RESULTS)}")
                if "independent" in item and not isinstance(item["independent"], bool):
                    errors.append(f"{where}.independent must be boolean")
                if "strength" in item and (
                    not _is_number(item["strength"]) or not 0 <= item["strength"] <= 1
                ):
                    errors.append(f"{where}.strength must be in [0, 1]")
                if "artifact_path" in item:
                    errors += _validate_relative_path(item["artifact_path"], f"{where}.artifact_path")
                if "data" in item and not isinstance(item["data"], dict):
                    errors.append(f"{where}.data must be an object")

    if "artifacts" in draft:
        artifacts = draft["artifacts"]
        if not isinstance(artifacts, list):
            errors.append("event.artifacts must be an array")
        else:
            for index, item in enumerate(artifacts):
                where = f"event.artifacts[{index}]"
                if not isinstance(item, dict):
                    errors.append(f"{where} must be an object")
                    continue
                errors += _unknown_keys(item, _ARTIFACT_FIELDS, where)
                errors += _validate_relative_path(item.get("path"), f"{where}.path")
                if item.get("role") not in ARTIFACT_ROLES:
                    errors.append(f"{where}.role must be one of {sorted(ARTIFACT_ROLES)}")
                if "sha256" in item and not re.fullmatch(r"[0-9a-f]{64}", str(item["sha256"])):
                    errors.append(f"{where}.sha256 must be 64 lower-case hexadecimal characters")
                if "size_bytes" in item and (
                    not isinstance(item["size_bytes"], int) or item["size_bytes"] < 0
                ):
                    errors.append(f"{where}.size_bytes must be a non-negative integer")

    for field in ("dependencies", "tags"):
        if field in draft and (
            not isinstance(draft[field], list)
            or not all(isinstance(item, str) and item for item in draft[field])
        ):
            errors.append(f"event.{field} must be an array of non-empty strings")

    if "data" in draft and not isinstance(draft["data"], dict):
        errors.append("event.data must be an object")

    if "provenance" in draft:
        provenance = draft["provenance"]
        if not isinstance(provenance, dict):
            errors.append("event.provenance must be an object")
        else:
            errors += _unknown_keys(provenance, _PROVENANCE_FIELDS, "event.provenance")
            if provenance.get("mode") not in PROVENANCE_MODES:
                errors.append(f"event.provenance.mode must be one of {sorted(PROVENANCE_MODES)}")
            if provenance.get("review_status") not in REVIEW_STATUSES:
                errors.append(
                    f"event.provenance.review_status must be one of {sorted(REVIEW_STATUSES)}"
                )
            for field in ("source_refs", "source_hashes"):
                if field in provenance and (
                    not isinstance(provenance[field], list)
                    or not all(isinstance(item, str) and item for item in provenance[field])
                ):
                    errors.append(f"event.provenance.{field} must be an array of strings")
            if "extraction_confidence" in provenance and (
                not _is_number(provenance["extraction_confidence"])
                or not 0 <= provenance["extraction_confidence"] <= 1
            ):
                errors.append("event.provenance.extraction_confidence must be in [0, 1]")
            if provenance.get("mode") == "recovery" and not provenance.get("source_refs"):
                errors.append("recovery events require provenance.source_refs")

    if event_type in {"run.started", "run.ended"} and not draft.get("run_id"):
        errors.append(f"{event_type} requires run_id")
    if event_type == "run.started" and "actor" not in draft:
        errors.append("run.started requires actor")
    if event_type == "run.ended" and "outcome" not in draft:
        errors.append("run.ended requires outcome")
    if event_type == "annotation.added":
        data = draft.get("data")
        if not isinstance(data, dict):
            errors.append("annotation.added requires event.data")
        else:
            if not isinstance(data.get("target_event_id"), str) or not data.get("target_event_id"):
                errors.append("annotation.added data.target_event_id must be a non-empty string")
            if not isinstance(data.get("set"), dict):
                errors.append("annotation.added data.set must be an object of dotted paths to values")
            if "unset" in data and (
                not isinstance(data["unset"], list)
                or not all(isinstance(item, str) and item for item in data["unset"])
            ):
                errors.append("annotation.added data.unset must be an array of dotted paths")

    if canonical:
        for field in IMMUTABLE_EVENT_FIELDS:
            if field not in draft:
                errors.append(f"canonical event: missing required field {field!r}")
        if draft.get("schema_version") != SCHEMA_VERSION:
            errors.append(f"event.schema_version must be {SCHEMA_VERSION}")
        if "problem_id" in draft:
            errors += validate_problem_id(draft["problem_id"])
        if not isinstance(draft.get("sequence"), int) or draft.get("sequence", 0) < 1:
            errors.append("event.sequence must be a positive integer")
        if not _is_timestamp(draft.get("recorded_at")):
            errors.append("event.recorded_at must be an RFC 3339 timestamp")
        for field in ("prev_event_hash", "event_hash"):
            value = draft.get(field)
            if value is not None and not re.fullmatch(r"[0-9a-f]{64}", str(value)):
                errors.append(f"event.{field} must be null or a SHA-256 hex digest")

    errors.extend(_placeholder_errors(draft))

    try:
        encoded = json.dumps(draft, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_EVENT_BYTES:
            errors.append(
                f"event exceeds {MAX_EVENT_BYTES} bytes; store raw material as an artifact instead"
            )
    except (TypeError, ValueError) as exc:
        errors.append(f"event is not JSON-serializable: {exc}")

    return errors


def require_valid(value: Any, errors: Iterable[str]) -> None:
    collected = list(errors)
    if collected:
        raise ValueError("\n".join(collected))
