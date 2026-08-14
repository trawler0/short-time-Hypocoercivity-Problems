"""Materialize append-only ledgers into analysis-friendly datasets."""

from __future__ import annotations

import copy
import csv
import json
import shutil
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Iterator

from .constants import IMMUTABLE_EVENT_FIELDS
from .core import (
    ResearchLogError,
    canonical_json,
    git_revision,
    iter_problem_ids,
    ledger_path,
    load_events,
    load_problem,
    sha256_file,
    utc_now,
    write_json_atomic,
)

_EVENT_COLUMNS = [
    "schema_version",
    "event_id",
    "problem_id",
    "sequence",
    "recorded_at",
    "occurred_at",
    "temporal_precision",
    "event_type",
    "stage",
    "outcome",
    "summary",
    "run_id",
    "actor_type",
    "actor_name",
    "actor_model",
    "actor_provider",
    "actor_interface",
    "subject_type",
    "subject_id",
    "confidence",
    "success_probability",
    "information_value",
    "remaining_cost",
    "impact_score",
    "provenance_mode",
    "review_status",
    "recovery_bundle_id",
    "annotation_count",
    "tags_json",
    "dependencies_json",
    "data_json",
    "event_hash",
]

_PROBLEM_COLUMNS = [
    "schema_version",
    "problem_id",
    "title",
    "status",
    "created_at",
    "updated_at",
    "description",
    "human_log",
    "registration_mode",
    "adopted_existing_directory",
    "domains_json",
    "tags_json",
    "source_refs_json",
    "owners_json",
    "metadata_json",
]

_ARTIFACT_COLUMNS = [
    "event_id",
    "problem_id",
    "sequence",
    "path",
    "role",
    "sha256",
    "size_bytes",
    "mime",
    "description",
]

_EVIDENCE_COLUMNS = [
    "event_id",
    "problem_id",
    "sequence",
    "evidence_index",
    "type",
    "result",
    "independent",
    "strength",
    "artifact_path",
    "notes",
    "data_json",
]

_ANNOTATION_COLUMNS = [
    "annotation_event_id",
    "problem_id",
    "sequence",
    "target_event_id",
    "review_status",
    "provenance_mode",
    "set_json",
    "unset_json",
    "reason",
]


def _set_nested(target: dict[str, Any], dotted_path: str, value: Any) -> None:
    if not dotted_path or dotted_path.split(".", 1)[0] in IMMUTABLE_EVENT_FIELDS:
        raise ResearchLogError(f"annotation may not change immutable field {dotted_path!r}")
    parts = dotted_path.split(".")
    current: dict[str, Any] = target
    for part in parts[:-1]:
        child = current.get(part)
        if child is None:
            child = {}
            current[part] = child
        if not isinstance(child, dict):
            raise ResearchLogError(
                f"annotation path {dotted_path!r} crosses non-object field {part!r}"
            )
        current = child
    current[parts[-1]] = copy.deepcopy(value)


def _unset_nested(target: dict[str, Any], dotted_path: str) -> None:
    if not dotted_path or dotted_path.split(".", 1)[0] in IMMUTABLE_EVENT_FIELDS:
        raise ResearchLogError(f"annotation may not change immutable field {dotted_path!r}")
    parts = dotted_path.split(".")
    current: Any = target
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            return
        current = current[part]
    if isinstance(current, dict):
        current.pop(parts[-1], None)


def apply_annotations(
    events: list[dict[str, Any]],
    *,
    include_unreviewed: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply append-only annotation overlays without mutating canonical events."""

    effective = {event["event_id"]: copy.deepcopy(event) for event in events}
    counts: Counter[str] = Counter()
    applied: list[dict[str, Any]] = []
    for annotation in events:
        if annotation.get("event_type") != "annotation.added":
            continue
        review_status = (annotation.get("provenance") or {}).get("review_status", "unreviewed")
        if review_status == "rejected" or (review_status == "unreviewed" and not include_unreviewed):
            continue
        data = annotation.get("data") or {}
        target_id = data.get("target_event_id")
        if target_id not in effective:
            continue
        target = effective[target_id]
        for path, value in (data.get("set") or {}).items():
            _set_nested(target, path, value)
        for path in data.get("unset") or []:
            _unset_nested(target, path)
        counts[target_id] += 1
        applied.append(annotation)
    output: list[dict[str, Any]] = []
    for event in events:
        item = effective[event["event_id"]]
        item["_annotation_count"] = counts[event["event_id"]]
        output.append(item)
    return output, applied


def _flatten_event(event: dict[str, Any]) -> dict[str, Any]:
    actor = event.get("actor") or {}
    subject = event.get("subject") or {}
    metrics = event.get("metrics") or {}
    provenance = event.get("provenance") or {}
    return {
        "schema_version": event.get("schema_version"),
        "event_id": event.get("event_id"),
        "problem_id": event.get("problem_id"),
        "sequence": event.get("sequence"),
        "recorded_at": event.get("recorded_at"),
        "occurred_at": event.get("occurred_at"),
        "temporal_precision": event.get("temporal_precision"),
        "event_type": event.get("event_type"),
        "stage": event.get("stage"),
        "outcome": event.get("outcome"),
        "summary": event.get("summary"),
        "run_id": event.get("run_id"),
        "actor_type": actor.get("type"),
        "actor_name": actor.get("name"),
        "actor_model": actor.get("model"),
        "actor_provider": actor.get("provider"),
        "actor_interface": actor.get("interface"),
        "subject_type": subject.get("type"),
        "subject_id": subject.get("id"),
        "confidence": metrics.get("confidence"),
        "success_probability": metrics.get("success_probability"),
        "information_value": metrics.get("information_value"),
        "remaining_cost": metrics.get("remaining_cost"),
        "impact_score": metrics.get("impact_score"),
        "provenance_mode": provenance.get("mode"),
        "review_status": provenance.get("review_status"),
        "recovery_bundle_id": provenance.get("recovery_bundle_id"),
        "annotation_count": event.get("_annotation_count", 0),
        "tags_json": canonical_json(event.get("tags") or []),
        "dependencies_json": canonical_json(event.get("dependencies") or []),
        "data_json": canonical_json(event.get("data") or {}),
        "event_hash": event.get("event_hash"),
    }


def _flatten_problem(problem: dict[str, Any]) -> dict[str, Any]:
    metadata = problem.get("metadata") or {}
    return {
        "schema_version": problem.get("schema_version"),
        "problem_id": problem.get("problem_id"),
        "title": problem.get("title"),
        "status": problem.get("status"),
        "created_at": problem.get("created_at"),
        "updated_at": problem.get("updated_at"),
        "description": problem.get("description"),
        "human_log": problem.get("human_log"),
        "registration_mode": metadata.get("registration_mode"),
        "adopted_existing_directory": metadata.get("adopted_existing_directory"),
        "domains_json": canonical_json(problem.get("domains") or []),
        "tags_json": canonical_json(problem.get("tags") or []),
        "source_refs_json": canonical_json(problem.get("source_refs") or []),
        "owners_json": canonical_json(problem.get("owners") or []),
        "metadata_json": canonical_json(problem.get("metadata") or {}),
    }


def _write_csv(path: Path, columns: list[str], rows: Iterable[dict[str, Any]]) -> int:
    count = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
            count += 1
    return count


def _artifact_rows(events: Iterable[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for event in events:
        for artifact in event.get("artifacts") or []:
            yield {
                "event_id": event.get("event_id"),
                "problem_id": event.get("problem_id"),
                "sequence": event.get("sequence"),
                **artifact,
            }


def _evidence_rows(events: Iterable[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for event in events:
        for index, evidence in enumerate(event.get("evidence") or []):
            yield {
                "event_id": event.get("event_id"),
                "problem_id": event.get("problem_id"),
                "sequence": event.get("sequence"),
                "evidence_index": index,
                "type": evidence.get("type"),
                "result": evidence.get("result"),
                "independent": evidence.get("independent"),
                "strength": evidence.get("strength"),
                "artifact_path": evidence.get("artifact_path"),
                "notes": evidence.get("notes"),
                "data_json": canonical_json(evidence.get("data") or {}),
            }


def _annotation_rows(annotations: Iterable[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for event in annotations:
        data = event.get("data") or {}
        provenance = event.get("provenance") or {}
        yield {
            "annotation_event_id": event.get("event_id"),
            "problem_id": event.get("problem_id"),
            "sequence": event.get("sequence"),
            "target_event_id": data.get("target_event_id"),
            "review_status": provenance.get("review_status"),
            "provenance_mode": provenance.get("mode"),
            "set_json": canonical_json(data.get("set") or {}),
            "unset_json": canonical_json(data.get("unset") or []),
            "reason": data.get("reason"),
        }


def _create_sqlite(
    path: Path,
    problem_rows: list[dict[str, Any]],
    event_rows: list[dict[str, Any]],
    artifact_rows: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
    annotation_rows: list[dict[str, Any]],
) -> None:
    if path.exists():
        path.unlink()
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA foreign_keys=ON")
        problem_types = {"schema_version": "INTEGER"}
        connection.execute(
            "CREATE TABLE problems ("
            + ",".join(
                f'\"{column}\" {problem_types.get(column, "TEXT")}'
                for column in _PROBLEM_COLUMNS
            )
            + ", PRIMARY KEY(problem_id))"
        )
        event_types = {
            "schema_version": "INTEGER",
            "sequence": "INTEGER",
            "confidence": "REAL",
            "success_probability": "REAL",
            "information_value": "REAL",
            "remaining_cost": "REAL",
            "impact_score": "INTEGER",
            "annotation_count": "INTEGER",
        }
        event_columns_sql = ",".join(
            f'\"{column}\" {event_types.get(column, "TEXT")}' for column in _EVENT_COLUMNS
        )
        connection.execute(
            f"CREATE TABLE events ({event_columns_sql}, PRIMARY KEY(event_id), FOREIGN KEY(problem_id) REFERENCES problems(problem_id))"
        )
        artifact_types = {"sequence": "INTEGER", "size_bytes": "INTEGER"}
        connection.execute(
            "CREATE TABLE artifacts ("
            + ",".join(
                f'\"{column}\" {artifact_types.get(column, "TEXT")}'
                for column in _ARTIFACT_COLUMNS
            )
            + ", FOREIGN KEY(event_id) REFERENCES events(event_id))"
        )
        evidence_types = {
            "sequence": "INTEGER",
            "evidence_index": "INTEGER",
            "independent": "INTEGER",
            "strength": "REAL",
        }
        connection.execute(
            "CREATE TABLE evidence ("
            + ",".join(
                f'\"{column}\" {evidence_types.get(column, "TEXT")}'
                for column in _EVIDENCE_COLUMNS
            )
            + ", FOREIGN KEY(event_id) REFERENCES events(event_id))"
        )
        annotation_types = {"sequence": "INTEGER"}
        connection.execute(
            "CREATE TABLE annotations ("
            + ",".join(
                f'\"{column}\" {annotation_types.get(column, "TEXT")}'
                for column in _ANNOTATION_COLUMNS
            )
            + ", FOREIGN KEY(annotation_event_id) REFERENCES events(event_id))"
        )

        def insert_many(table: str, columns: list[str], rows: list[dict[str, Any]]) -> None:
            if not rows:
                return
            placeholders = ",".join("?" for _ in columns)
            column_sql = ",".join(f'\"{column}\"' for column in columns)
            connection.executemany(
                f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders})",
                [[row.get(column) for column in columns] for row in rows],
            )

        insert_many("problems", _PROBLEM_COLUMNS, problem_rows)
        insert_many("events", _EVENT_COLUMNS, event_rows)
        insert_many("artifacts", _ARTIFACT_COLUMNS, artifact_rows)
        insert_many("evidence", _EVIDENCE_COLUMNS, evidence_rows)
        insert_many("annotations", _ANNOTATION_COLUMNS, annotation_rows)
        connection.execute("CREATE INDEX idx_events_problem_type ON events(problem_id, event_type)")
        connection.execute("CREATE INDEX idx_events_run ON events(run_id)")
        connection.execute("CREATE INDEX idx_events_actor_model ON events(actor_model)")
        connection.commit()
    finally:
        connection.close()


def export_repository(
    root: Path,
    output: Path,
    *,
    formats: set[str] | None = None,
    include_unreviewed_annotations: bool = True,
    overwrite: bool = False,
) -> dict[str, Any]:
    formats = formats or {"csv", "jsonl", "sqlite"}
    unknown = formats - {"csv", "jsonl", "sqlite"}
    if unknown:
        raise ResearchLogError(f"unknown export format(s): {sorted(unknown)}")
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        if not overwrite:
            raise ResearchLogError(f"export directory is not empty: {output}; use --overwrite")
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)

    problems: list[dict[str, Any]] = []
    effective_events: list[dict[str, Any]] = []
    applied_annotations: list[dict[str, Any]] = []
    ledger_hashes: dict[str, str] = {}
    for problem_id in iter_problem_ids(root):
        problems.append(load_problem(root, problem_id))
        raw_events = load_events(root, problem_id)
        effective, annotations = apply_annotations(
            raw_events, include_unreviewed=include_unreviewed_annotations
        )
        effective_events.extend(effective)
        applied_annotations.extend(annotations)
        ledger_hashes[problem_id] = sha256_file(ledger_path(root, problem_id))

    problem_rows = [_flatten_problem(problem) for problem in problems]
    event_rows = [_flatten_event(event) for event in effective_events]
    artifact_rows = list(_artifact_rows(effective_events))
    evidence_rows = list(_evidence_rows(effective_events))
    annotation_rows = list(_annotation_rows(applied_annotations))

    if "csv" in formats:
        _write_csv(output / "problems.csv", _PROBLEM_COLUMNS, problem_rows)
        _write_csv(output / "events.csv", _EVENT_COLUMNS, event_rows)
        _write_csv(output / "artifacts.csv", _ARTIFACT_COLUMNS, artifact_rows)
        _write_csv(output / "evidence.csv", _EVIDENCE_COLUMNS, evidence_rows)
        _write_csv(output / "annotations.csv", _ANNOTATION_COLUMNS, annotation_rows)
    if "jsonl" in formats:
        with (output / "events.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
            for event in effective_events:
                value = dict(event)
                value.pop("_annotation_count", None)
                handle.write(canonical_json(value) + "\n")
        with (output / "problems.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
            for problem in problems:
                handle.write(canonical_json(problem) + "\n")
    if "sqlite" in formats:
        _create_sqlite(
            output / "researchlog.sqlite",
            problem_rows,
            event_rows,
            artifact_rows,
            evidence_rows,
            annotation_rows,
        )

    manifest = {
        "dataset_schema_version": 1,
        "generated_at": utc_now(),
        "repo_revision": git_revision(root),
        "annotations_applied": True,
        "include_unreviewed_annotations": include_unreviewed_annotations,
        "formats": sorted(formats),
        "counts": {
            "problems": len(problem_rows),
            "events": len(event_rows),
            "artifacts": len(artifact_rows),
            "evidence": len(evidence_rows),
            "annotations_applied": len(annotation_rows),
        },
        "source_ledger_sha256": ledger_hashes,
    }
    write_json_atomic(output / "dataset-manifest.json", manifest)
    return manifest


def repository_stats(root: Path, *, include_unreviewed_annotations: bool = True) -> dict[str, Any]:
    event_type_counts: Counter[str] = Counter()
    outcome_counts: Counter[str] = Counter()
    model_counts: Counter[str] = Counter()
    provenance_counts: Counter[str] = Counter()
    review_counts: Counter[str] = Counter()
    intervention_counts: Counter[str] = Counter()
    verification_result_counts: Counter[str] = Counter()
    selection_counts: Counter[str] = Counter()
    problem_status_counts: Counter[str] = Counter()
    registration_mode_counts: Counter[str] = Counter()
    run_outcomes: Counter[str] = Counter()
    events_per_problem: dict[str, int] = {}
    recovered_events = 0
    total_events = 0

    for problem_id in iter_problem_ids(root):
        problem = load_problem(root, problem_id)
        problem_status_counts[problem.get("status", "unknown")] += 1
        registration_mode_counts[(problem.get("metadata") or {}).get("registration_mode", "unknown")] += 1
        events, _ = apply_annotations(
            load_events(root, problem_id), include_unreviewed=include_unreviewed_annotations
        )
        events_per_problem[problem_id] = len(events)
        for event in events:
            total_events += 1
            event_type_counts[event.get("event_type", "unknown")] += 1
            outcome_counts[event.get("outcome", "not_recorded")] += 1
            actor = event.get("actor") or {}
            if actor.get("type") == "ai":
                model_counts[actor.get("model", "unknown")] += 1
            provenance = event.get("provenance") or {}
            mode = provenance.get("mode", "unknown")
            provenance_counts[mode] += 1
            review_counts[provenance.get("review_status", "unknown")] += 1
            recovered_events += int(mode == "recovery")
            data = event.get("data") or {}
            if event.get("event_type") == "human.intervention":
                intervention_counts[data.get("intervention_type", "unknown")] += 1
            if event.get("event_type") == "verification.recorded":
                verification_result_counts[data.get("result", "unknown")] += 1
            if event.get("event_type") == "selection.recorded":
                selection_counts[data.get("decision", "unknown")] += 1
            if event.get("event_type") == "run.ended":
                run_outcomes[event.get("outcome", "unknown")] += 1

    return {
        "generated_at": utc_now(),
        "problem_count": len(events_per_problem),
        "event_count": total_events,
        "recovered_event_fraction": (recovered_events / total_events) if total_events else None,
        "events_per_problem": dict(sorted(events_per_problem.items())),
        "problem_status": dict(problem_status_counts.most_common()),
        "registration_modes": dict(registration_mode_counts.most_common()),
        "event_types": dict(event_type_counts.most_common()),
        "outcomes": dict(outcome_counts.most_common()),
        "run_outcomes": dict(run_outcomes.most_common()),
        "ai_models": dict(model_counts.most_common()),
        "provenance_modes": dict(provenance_counts.most_common()),
        "review_status": dict(review_counts.most_common()),
        "human_interventions": dict(intervention_counts.most_common()),
        "verification_results": dict(verification_result_counts.most_common()),
        "selection_decisions": dict(selection_counts.most_common()),
    }
