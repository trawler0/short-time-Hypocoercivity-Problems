"""Command-line interface for the research provenance ledger."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from .constants import (
    ACTOR_TYPES,
    ARTIFACT_ROLES,
    CAPTURE_COMPLETENESS,
    OUTCOMES,
    PROBLEM_STATUSES,
    REGISTRATION_MODES,
    PROVENANCE_MODES,
    REVIEW_STATUSES,
    STAGES,
)
from .core import (
    Issue,
    ResearchLogError,
    append_drafts,
    artifact_record,
    canonical_json,
    doctor_repository,
    find_repo_root,
    ingest_file,
    init_problem,
    issues_exit_code,
    iter_problem_ids,
    ledger_path,
    load_events,
    load_problem,
    new_id,
    parse_drafts_text,
    problem_dir,
    relative_repo_path,
    repair_truncated_tail,
    snapshot_paths,
    utc_now,
    validate_repository,
)
from .export import apply_annotations, export_repository, repository_stats
from .recovery import ingest_recovery, prepare_recovery


def _json_value(value: str | None, *, default: Any = None) -> Any:
    if value is None:
        return default
    if value.startswith("@"):
        path = Path(value[1:])
        text = path.read_text(encoding="utf-8")
    else:
        text = value
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ResearchLogError(f"invalid JSON value: {exc}") from exc


def _actor_from_args(args: argparse.Namespace) -> dict[str, Any] | None:
    actor_type = getattr(args, "actor_type", None)
    actor_name = getattr(args, "actor_name", None)
    if not actor_type and not actor_name:
        return None
    actor: dict[str, Any] = {
        "type": actor_type or "unknown",
        "name": actor_name or "unknown",
    }
    for argument, key in (
        ("model", "model"),
        ("provider", "provider"),
        ("interface", "interface"),
        ("session_id", "session_id"),
        ("agent_id", "agent_id"),
    ):
        value = getattr(args, argument, None)
        if value:
            actor[key] = value
    return actor


def _add_actor_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--actor-type", choices=sorted(ACTOR_TYPES))
    parser.add_argument("--actor-name")
    parser.add_argument("--model", help="Exact model/version, or 'unknown' when unavailable")
    parser.add_argument("--provider")
    parser.add_argument("--interface", help="For example chatgpt-pro, codex, api, or local")
    parser.add_argument("--session-id")
    parser.add_argument("--agent-id")


def _print_events(events: Sequence[dict[str, Any]], *, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(events, ensure_ascii=False, sort_keys=True, indent=2))
        return
    for event in events:
        print(
            f"{event['problem_id']} #{event['sequence']} {event['event_type']} "
            f"{event['event_id']}"
        )


def _print_issues(issues: Sequence[Issue], *, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps([issue.to_dict() for issue in issues], ensure_ascii=False, indent=2))
        return
    if not issues:
        print("OK: no issues found")
        return
    for issue in issues:
        location = ""
        if issue.path:
            location = f" {issue.path}"
            if issue.line is not None:
                location += f":{issue.line}"
        event = f" [{issue.event_id}]" if issue.event_id else ""
        print(f"{issue.severity.upper():7} {issue.code}{location}{event}: {issue.message}")
    errors = sum(issue.severity == "error" for issue in issues)
    warnings = sum(issue.severity == "warning" for issue in issues)
    print(f"Summary: {errors} error(s), {warnings} warning(s)")


def _command_init(root: Path, args: argparse.Namespace) -> int:
    problem = init_problem(
        root,
        args.problem_id,
        title=args.title,
        status=args.status,
        domains=args.domain,
        tags=args.tag,
        source_refs=args.source_ref,
        human_log=args.human_log,
        description=args.description,
        owners=args.owner,
        actor=_actor_from_args(args),
        created_at=args.created_at,
        adopt_existing=args.adopt_existing,
        registration_mode=(
            args.registration_mode
            or ("retrospective" if args.adopt_existing else "prospective")
        ),
    )
    print(json.dumps(problem, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


def _command_begin(root: Path, args: argparse.Namespace) -> int:
    actor = _actor_from_args(args)
    if actor is None:
        raise ResearchLogError("begin requires --actor-type and --actor-name")
    run_id = args.run_id or new_id("run")
    data: dict[str, Any] = {
        "interface": actor.get("interface", args.interface or "unknown"),
        "independent": args.independent,
        "objective": args.objective,
        "input_refs": args.input_ref,
    }
    if args.contamination_group:
        data["contamination_group"] = args.contamination_group
    events = append_drafts(
        root,
        args.problem_id,
        [
            {
                "event_type": "run.started",
                "summary": args.summary or f"Started research run {run_id}",
                "occurred_at": args.occurred_at or utc_now(),
                "temporal_precision": "exact",
                "stage": args.stage,
                "outcome": "not_applicable",
                "run_id": run_id,
                "actor": actor,
                "subject": {"type": "run", "id": run_id},
                "data": data,
                "provenance": {
                    "mode": args.mode,
                    "source_refs": args.source_ref,
                    "review_status": args.review_status,
                },
            }
        ],
    )
    _print_events(events, as_json=args.json)
    if not args.json:
        print(f"RUN_ID={run_id}")
    return 0


def _command_end(root: Path, args: argparse.Namespace) -> int:
    actor = _actor_from_args(args)
    draft: dict[str, Any] = {
        "event_type": "run.ended",
        "summary": args.summary,
        "occurred_at": args.occurred_at or utc_now(),
        "temporal_precision": "exact",
        "stage": args.stage,
        "outcome": args.outcome,
        "run_id": args.run_id,
        "subject": {"type": "run", "id": args.run_id},
        "data": {
            "end_reason": args.reason,
            "capture_completeness": args.capture_completeness,
            "protocol_complete": args.protocol_complete,
            "next_action": args.next_action,
        },
        "provenance": {
            "mode": args.mode,
            "source_refs": args.source_ref,
            "review_status": args.review_status,
        },
    }
    if actor:
        draft["actor"] = actor
    events = append_drafts(root, args.problem_id, [draft])
    _print_events(events, as_json=args.json)
    return 0


def _parse_artifact_argument(root: Path, value: str) -> dict[str, Any]:
    if ":" in value:
        path, role = value.rsplit(":", 1)
    else:
        path, role = value, "other"
    if role not in ARTIFACT_ROLES:
        raise ResearchLogError(f"unknown artifact role {role!r}")
    return artifact_record(root, path, role)


def _command_record(root: Path, args: argparse.Namespace) -> int:
    draft: dict[str, Any] = {
        "event_type": args.event_type,
        "summary": args.summary,
        "stage": args.stage,
        "outcome": args.outcome,
        "tags": args.tag,
        "dependencies": args.dependency,
        "data": _json_value(args.data, default={}),
        "provenance": {
            "mode": args.mode,
            "source_refs": args.source_ref,
            "review_status": args.review_status,
        },
    }
    if args.occurred_at:
        draft["occurred_at"] = args.occurred_at
        draft["temporal_precision"] = args.temporal_precision
    if args.run_id:
        draft["run_id"] = args.run_id
    actor = _actor_from_args(args)
    if actor:
        draft["actor"] = actor
    if args.subject_type or args.subject_id:
        if not args.subject_type or not args.subject_id:
            raise ResearchLogError("--subject-type and --subject-id must be used together")
        draft["subject"] = {"type": args.subject_type, "id": args.subject_id}
    metrics = {
        key: value
        for key, value in {
            "confidence": args.confidence,
            "success_probability": args.success_probability,
            "information_value": args.information_value,
            "remaining_cost": args.remaining_cost,
            "impact_score": args.impact_score,
        }.items()
        if value is not None
    }
    if metrics:
        draft["metrics"] = metrics
    if args.evidence:
        evidence = _json_value(args.evidence)
        draft["evidence"] = evidence if isinstance(evidence, list) else [evidence]
    if args.artifact:
        draft["artifacts"] = [_parse_artifact_argument(root, value) for value in args.artifact]
    events = append_drafts(root, args.problem_id, [draft])
    _print_events(events, as_json=args.json)
    return 0


def _command_ingest(root: Path, args: argparse.Namespace) -> int:
    actor = _actor_from_args(args)
    if args.file:
        events = ingest_file(
            root,
            args.problem_id,
            Path(args.file),
            default_actor=actor,
            default_run_id=args.run_id,
            default_mode=args.mode,
            default_review_status=args.review_status,
            consume=args.consume,
        )
    else:
        raw = sys.stdin.buffer.read()
        drafts = parse_drafts_text(raw.decode("utf-8"), source="stdin")
        from .core import sha256_bytes

        events = append_drafts(
            root,
            args.problem_id,
            drafts,
            default_actor=actor,
            default_run_id=args.run_id,
            default_mode=args.mode,
            default_review_status=args.review_status,
            input_sha256=sha256_bytes(raw),
        )
    _print_events(events, as_json=args.json)
    return 0


def _command_ingest_inbox(root: Path, args: argparse.Namespace) -> int:
    actor = _actor_from_args(args)
    problem_ids = [args.problem_id] if args.problem_id else list(iter_problem_ids(root))
    total = 0
    for problem_id in problem_ids:
        inbox = problem_dir(root, problem_id) / "inbox"
        for path in sorted(inbox.glob("*.jsonl")) if inbox.exists() else []:
            events = ingest_file(
                root,
                problem_id,
                path,
                default_actor=actor,
                default_mode=args.mode,
                default_review_status=args.review_status,
                consume=True,
            )
            total += len(events)
            print(f"Ingested {len(events)} event(s) from {relative_repo_path(root, path)}")
    print(f"Total ingested: {total}")
    return 0


def _command_validate(root: Path, args: argparse.Namespace) -> int:
    issues = validate_repository(root, args.problem_id)
    _print_issues(issues, as_json=args.json)
    return issues_exit_code(issues, strict=args.strict)


def _command_doctor(root: Path, args: argparse.Namespace) -> int:
    issues = doctor_repository(root, args.problem_id, profile_path=args.profile)
    _print_issues(issues, as_json=args.json)
    return issues_exit_code(issues, strict=args.strict)


def _command_snapshot(root: Path, args: argparse.Namespace) -> int:
    actor = _actor_from_args(args)
    paths = list(args.path)
    if not paths:
        metadata = json.loads(
            (problem_dir(root, args.problem_id) / "problem.json").read_text(encoding="utf-8")
        )
        paths.extend(metadata.get("source_refs") or [])
        paths.append(metadata["human_log"])
        paths = [value.split("#", 1)[0] for value in paths]
    events = snapshot_paths(
        root,
        args.problem_id,
        paths,
        role=args.role,
        summary=args.summary,
        actor=actor,
        run_id=args.run_id,
    )
    _print_events(events, as_json=args.json)
    return 0


def _command_export(root: Path, args: argparse.Namespace) -> int:
    formats = {item.strip() for item in args.formats.split(",") if item.strip()}
    manifest = export_repository(
        root,
        Path(args.output),
        formats=formats,
        include_unreviewed_annotations=not args.reviewed_annotations_only,
        overwrite=args.overwrite,
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


def _command_stats(root: Path, args: argparse.Namespace) -> int:
    stats = repository_stats(
        root, include_unreviewed_annotations=not args.reviewed_annotations_only
    )
    if args.json:
        print(json.dumps(stats, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    print(f"Problems: {stats['problem_count']}")
    print(f"Events:   {stats['event_count']}")
    fraction = stats["recovered_event_fraction"]
    print(f"Recovered-event fraction: {fraction:.3f}" if fraction is not None else "Recovered-event fraction: n/a")
    for title, key in (
        ("Run outcomes", "run_outcomes"),
        ("AI models", "ai_models"),
        ("Human interventions", "human_interventions"),
        ("Verification results", "verification_results"),
        ("Selection decisions", "selection_decisions"),
    ):
        print(f"\n{title}:")
        values = stats[key]
        if not values:
            print("  (none)")
        for name, count in values.items():
            print(f"  {name}: {count}")
    return 0


def _command_recover_prepare(root: Path, args: argparse.Namespace) -> int:
    manifest = prepare_recovery(
        root,
        args.problem_id,
        profile_path=args.profile,
        output_dir=Path(args.output).resolve() if args.output else None,
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


def _command_recover_prepare_all(root: Path, args: argparse.Namespace) -> int:
    prepared: list[dict[str, Any]] = []
    skipped: list[str] = []
    for problem_id in iter_problem_ids(root):
        diagnostics = doctor_repository(root, problem_id, profile_path=args.profile)
        events, _ = apply_annotations(
            load_events(root, problem_id), include_unreviewed=False
        )
        explicit_gap = any(
            event.get("event_type") == "protocol.gap"
            and (event.get("data") or {}).get("recovery_recommended") is True
            for event in events
        )
        analytics_gap = any(
            issue.severity == "error" or issue.code.startswith("profile.")
            for issue in diagnostics
        )
        if not args.all and not (explicit_gap or analytics_gap):
            skipped.append(problem_id)
            continue
        manifest = prepare_recovery(root, problem_id, profile_path=args.profile)
        prepared.append(
            {
                "problem_id": problem_id,
                "bundle_id": manifest["bundle_id"],
                "source_file_count": len(manifest.get("source_files") or []),
                "analytics_gap_count": len(manifest.get("analytics_gaps") or []),
            }
        )
    payload = {"prepared": prepared, "skipped": skipped}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        for item in prepared:
            print(
                f"Prepared {item['problem_id']}: {item['bundle_id']} "
                f"({item['source_file_count']} sources, "
                f"{item['analytics_gap_count']} analytics gaps)"
            )
        print(f"Prepared: {len(prepared)}; skipped: {len(skipped)}")
    return 0


def _command_recover_ingest(root: Path, args: argparse.Namespace) -> int:
    events = ingest_recovery(
        root,
        args.problem_id,
        bundle_id=args.bundle,
        recovered_file=Path(args.file).resolve(),
        review_status=args.review_status,
    )
    _print_events(events, as_json=args.json)
    return 0


def _command_repair(root: Path, args: argparse.Namespace) -> int:
    if not args.truncate_invalid_tail:
        raise ResearchLogError("repair currently requires --truncate-invalid-tail")
    quarantine = repair_truncated_tail(root, args.problem_id)
    if quarantine is None:
        print("No truncated tail found")
    else:
        print(f"Quarantined truncated tail at {relative_repo_path(root, quarantine)}")
    return 0


def _command_brief(root: Path, args: argparse.Namespace) -> int:
    problem = load_problem(root, args.problem_id)
    events = load_events(root, args.problem_id)
    open_runs: dict[str, dict[str, Any]] = {}
    for event in events:
        run_id = event.get("run_id")
        if event.get("event_type") == "run.started" and run_id:
            open_runs[run_id] = event
        elif event.get("event_type") == "run.ended" and run_id:
            open_runs.pop(run_id, None)
    inbox = problem_dir(root, args.problem_id) / "inbox"
    pending = [relative_repo_path(root, path) for path in sorted(inbox.glob("*.jsonl"))] if inbox.exists() else []
    tail = events[-args.tail :] if args.tail else []
    diagnostics = doctor_repository(root, args.problem_id)
    payload = {
        "problem": problem,
        "ledger": relative_repo_path(root, ledger_path(root, args.problem_id)),
        "event_count": len(events),
        "last_event_hash": events[-1].get("event_hash") if events else None,
        "open_run_ids": sorted(open_runs),
        "pending_inbox": pending,
        "recent_events": [
            {
                "sequence": event.get("sequence"),
                "event_id": event.get("event_id"),
                "event_type": event.get("event_type"),
                "run_id": event.get("run_id"),
                "outcome": event.get("outcome"),
                "summary": event.get("summary"),
            }
            for event in tail
        ],
        "diagnostic_counts": {
            "errors": sum(issue.severity == "error" for issue in diagnostics),
            "warnings": sum(issue.severity == "warning" for issue in diagnostics),
        },
        "agent_contract": {
            "read_first": [
                "AGENTS.md",
                "docs/research-logging.md",
                "docs/data-dictionary.md",
                "schemas/event-draft-v1.schema.json",
            ],
            "canonical_ledger_is_append_only": True,
            "remote_patch_inbox": f"logs/{args.problem_id}/inbox/<run-id>.jsonl",
            "post_patch_commands": [
                f"python -m researchlog ingest-inbox {args.problem_id}",
                f"python -m researchlog validate {args.problem_id}",
                f"python -m researchlog doctor {args.problem_id}",
            ],
        },
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
        return 0

    print(f"# Researchlog brief: {args.problem_id}")
    print(f"Title: {problem['title']}")
    print(f"Status: {problem['status']}")
    print(f"Human log: {problem.get('human_log', 'not recorded')}")
    print(f"Canonical ledger: {payload['ledger']} ({len(events)} events)")
    print(f"Open runs: {', '.join(sorted(open_runs)) or 'none'}")
    print(f"Pending inbox files: {len(pending)}")
    print(
        "Diagnostics: "
        f"{payload['diagnostic_counts']['errors']} error(s), "
        f"{payload['diagnostic_counts']['warnings']} warning(s)"
    )
    print("\nNever edit state.jsonl manually. For a remote patch, add draft JSONL at:")
    print(f"  logs/{args.problem_id}/inbox/<run-id>.jsonl")
    print("Then run:")
    for command in payload["agent_contract"]["post_patch_commands"]:
        print(f"  {command}")
    if tail:
        print("\nRecent events:")
        for event in payload["recent_events"]:
            print(
                f"  {event['sequence']:>4} {event['event_type']:<24} "
                f"{event.get('outcome') or '-':<14} {event['summary']}"
            )
    return 0


def _command_template(root: Path, args: argparse.Namespace) -> int:
    run_id = args.run_id or "run_REPLACE_ME"
    problem = load_problem(root, args.problem_id) if args.problem_id else None
    input_refs = list(problem.get("source_refs") or []) if problem else []
    if problem and problem.get("human_log") and problem["human_log"] not in input_refs:
        input_refs.append(problem["human_log"])
    if not input_refs:
        input_refs = ["REPLACE_WITH_REPOSITORY_RELATIVE_SOURCE"]
    actor = {
        "type": "ai",
        "name": args.actor_name,
        "model": args.model,
        "provider": args.provider,
        "interface": args.interface,
    }
    records = [
        {
            "event_type": "run.started",
            "summary": "Started a research attempt on the selected problem.",
            "stage": "specify",
            "outcome": "not_applicable",
            "run_id": run_id,
            "actor": actor,
            "data": {
                "interface": args.interface,
                "independent": args.independent,
                "objective": args.objective,
                "input_refs": input_refs,
            },
        },
        {
            "event_type": "claim.created",
            "summary": "REPLACE_WITH_A_COMPACT_CLAIM_OR_PARTIAL_RESULT",
            "stage": "synthesize",
            "outcome": "partial",
            "run_id": run_id,
            "actor": actor,
            "subject": {"type": "claim", "id": "claim_REPLACE_ME"},
            "data": {
                "claim_status": "candidate",
                "verification_status": "unverified",
                "novelty_status": "unknown",
                "remaining_gap": "REPLACE_ME",
            },
        },
        {
            "event_type": "run.ended",
            "summary": "Ended the run with an explicitly recorded outcome and capture status.",
            "stage": "reallocate",
            "outcome": "partial",
            "run_id": run_id,
            "actor": actor,
            "data": {
                "end_reason": "completed",
                "capture_completeness": "complete",
                "protocol_complete": True,
                "next_action": "REPLACE_ME",
            },
        },
    ]
    for record in records:
        print(canonical_json(record))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="researchlog",
        description="Append-only, schema-versioned research provenance ledger",
    )
    parser.add_argument("--root", help="Repository root (otherwise auto-detected)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Register a new problem")
    init_parser.add_argument("problem_id")
    init_parser.add_argument("--title", required=True)
    init_parser.add_argument("--status", choices=sorted(PROBLEM_STATUSES), default="active")
    init_parser.add_argument("--domain", action="append", default=[])
    init_parser.add_argument("--tag", action="append", default=[])
    init_parser.add_argument("--source-ref", action="append", default=[])
    init_parser.add_argument("--human-log", default="progress.md")
    init_parser.add_argument("--description")
    init_parser.add_argument("--owner", action="append", default=[])
    init_parser.add_argument("--created-at")
    init_parser.add_argument(
        "--adopt-existing",
        action="store_true",
        help="register a non-empty legacy logs/<problem-id> directory without overwriting it",
    )
    init_parser.add_argument(
        "--registration-mode",
        choices=sorted(REGISTRATION_MODES),
        help="prospective by default, or retrospective when --adopt-existing is used",
    )
    _add_actor_arguments(init_parser)
    init_parser.set_defaults(handler=_command_init)

    begin_parser = subparsers.add_parser("begin", help="Start a research run")
    begin_parser.add_argument("problem_id")
    begin_parser.add_argument("--run-id")
    begin_parser.add_argument("--summary")
    begin_parser.add_argument("--objective", required=True)
    begin_parser.add_argument("--input-ref", action="append", default=[])
    begin_parser.add_argument(
        "--independent",
        action=argparse.BooleanOptionalAction,
        required=True,
        help="state explicitly whether this run is independent of earlier candidate solutions",
    )
    begin_parser.add_argument("--contamination-group")
    begin_parser.add_argument("--stage", choices=sorted(STAGES), default="specify")
    begin_parser.add_argument("--occurred-at")
    begin_parser.add_argument("--mode", choices=sorted(PROVENANCE_MODES), default="live")
    begin_parser.add_argument("--review-status", choices=sorted(REVIEW_STATUSES), default="unreviewed")
    begin_parser.add_argument("--source-ref", action="append", default=[])
    begin_parser.add_argument("--json", action="store_true")
    _add_actor_arguments(begin_parser)
    begin_parser.set_defaults(handler=_command_begin)

    end_parser = subparsers.add_parser("end", help="End a research run")
    end_parser.add_argument("problem_id")
    end_parser.add_argument("--run-id", required=True)
    end_parser.add_argument("--summary", required=True)
    end_parser.add_argument("--outcome", choices=sorted(OUTCOMES), required=True)
    end_parser.add_argument("--reason", required=True)
    end_parser.add_argument("--capture-completeness", choices=sorted(CAPTURE_COMPLETENESS), required=True)
    end_parser.add_argument("--protocol-complete", action=argparse.BooleanOptionalAction, default=True)
    end_parser.add_argument("--next-action")
    end_parser.add_argument("--stage", choices=sorted(STAGES), default="reallocate")
    end_parser.add_argument("--occurred-at")
    end_parser.add_argument("--mode", choices=sorted(PROVENANCE_MODES), default="live")
    end_parser.add_argument("--review-status", choices=sorted(REVIEW_STATUSES), default="unreviewed")
    end_parser.add_argument("--source-ref", action="append", default=[])
    end_parser.add_argument("--json", action="store_true")
    _add_actor_arguments(end_parser)
    end_parser.set_defaults(handler=_command_end)

    record_parser = subparsers.add_parser("record", help="Append one typed event")
    record_parser.add_argument("problem_id")
    record_parser.add_argument("--type", dest="event_type", required=True)
    record_parser.add_argument("--summary", required=True)
    record_parser.add_argument("--stage", choices=sorted(STAGES), default="unknown")
    record_parser.add_argument("--outcome", choices=sorted(OUTCOMES), default="unknown")
    record_parser.add_argument("--run-id")
    record_parser.add_argument("--occurred-at")
    record_parser.add_argument("--temporal-precision", default="exact")
    record_parser.add_argument("--subject-type")
    record_parser.add_argument("--subject-id")
    record_parser.add_argument("--confidence", type=float)
    record_parser.add_argument("--success-probability", type=float)
    record_parser.add_argument("--information-value", type=float)
    record_parser.add_argument("--remaining-cost", type=float)
    record_parser.add_argument("--impact-score", type=int)
    record_parser.add_argument("--tag", action="append", default=[])
    record_parser.add_argument("--dependency", action="append", default=[])
    record_parser.add_argument("--data", help="JSON text or @path")
    record_parser.add_argument("--evidence", help="JSON object/array or @path")
    record_parser.add_argument("--artifact", action="append", default=[], help="PATH[:ROLE]")
    record_parser.add_argument("--mode", choices=sorted(PROVENANCE_MODES), default="live")
    record_parser.add_argument("--review-status", choices=sorted(REVIEW_STATUSES), default="unreviewed")
    record_parser.add_argument("--source-ref", action="append", default=[])
    record_parser.add_argument("--json", action="store_true")
    _add_actor_arguments(record_parser)
    record_parser.set_defaults(handler=_command_record)

    ingest_parser = subparsers.add_parser("ingest", help="Ingest JSONL event drafts")
    ingest_parser.add_argument("problem_id")
    ingest_parser.add_argument("--file", help="Read JSONL/JSON array from file; otherwise stdin")
    ingest_parser.add_argument("--consume", action="store_true", help="Delete input after successful append")
    ingest_parser.add_argument("--run-id")
    ingest_parser.add_argument("--mode", choices=sorted(PROVENANCE_MODES), default="live")
    ingest_parser.add_argument("--review-status", choices=sorted(REVIEW_STATUSES), default="unreviewed")
    ingest_parser.add_argument("--json", action="store_true")
    _add_actor_arguments(ingest_parser)
    ingest_parser.set_defaults(handler=_command_ingest)

    inbox_parser = subparsers.add_parser("ingest-inbox", help="Consume AI-created logs/*/inbox/*.jsonl")
    inbox_parser.add_argument("problem_id", nargs="?")
    inbox_parser.add_argument("--mode", choices=sorted(PROVENANCE_MODES), default="live")
    inbox_parser.add_argument("--review-status", choices=sorted(REVIEW_STATUSES), default="unreviewed")
    _add_actor_arguments(inbox_parser)
    inbox_parser.set_defaults(handler=_command_ingest_inbox)

    for name, handler, help_text in (
        ("validate", _command_validate, "Validate schemas, ordering, hashes, runs, and artifacts"),
        ("doctor", _command_doctor, "Validate and audit analytics completeness"),
    ):
        command_parser = subparsers.add_parser(name, help=help_text)
        command_parser.add_argument("problem_id", nargs="?")
        command_parser.add_argument("--strict", action="store_true")
        command_parser.add_argument("--json", action="store_true")
        if name == "doctor":
            command_parser.add_argument("--profile")
        command_parser.set_defaults(handler=handler)

    snapshot_parser = subparsers.add_parser("snapshot", help="Hash and record repository artifacts")
    snapshot_parser.add_argument("problem_id")
    snapshot_parser.add_argument("--path", action="append", default=[])
    snapshot_parser.add_argument("--role", choices=sorted(ARTIFACT_ROLES), default="other")
    snapshot_parser.add_argument("--summary")
    snapshot_parser.add_argument("--run-id")
    snapshot_parser.add_argument("--json", action="store_true")
    _add_actor_arguments(snapshot_parser)
    snapshot_parser.set_defaults(handler=_command_snapshot)

    export_parser = subparsers.add_parser("export", help="Create CSV, JSONL, and/or SQLite datasets")
    export_parser.add_argument("--output", default="data/exports/latest")
    export_parser.add_argument("--formats", default="csv,jsonl,sqlite")
    export_parser.add_argument("--reviewed-annotations-only", action="store_true")
    export_parser.add_argument("--overwrite", action="store_true")
    export_parser.set_defaults(handler=_command_export)

    stats_parser = subparsers.add_parser("stats", help="Show basic longitudinal statistics")
    stats_parser.add_argument("--reviewed-annotations-only", action="store_true")
    stats_parser.add_argument("--json", action="store_true")
    stats_parser.set_defaults(handler=_command_stats)

    recover_parser = subparsers.add_parser("recover", help="Prepare or ingest provenance recovery")
    recover_subparsers = recover_parser.add_subparsers(dest="recover_command", required=True)
    recover_prepare = recover_subparsers.add_parser("prepare")
    recover_prepare.add_argument("problem_id")
    recover_prepare.add_argument("--profile")
    recover_prepare.add_argument("--output")
    recover_prepare.set_defaults(handler=_command_recover_prepare)
    recover_prepare_all = recover_subparsers.add_parser(
        "prepare-all",
        help="prepare recovery bundles for every problem with a protocol/profile gap",
    )
    recover_prepare_all.add_argument("--profile")
    recover_prepare_all.add_argument(
        "--all",
        action="store_true",
        help="prepare every registered problem, even when no current gap is detected",
    )
    recover_prepare_all.add_argument("--json", action="store_true")
    recover_prepare_all.set_defaults(handler=_command_recover_prepare_all)

    recover_ingest = recover_subparsers.add_parser("ingest")
    recover_ingest.add_argument("problem_id")
    recover_ingest.add_argument("--bundle", required=True)
    recover_ingest.add_argument("--file", required=True)
    recover_ingest.add_argument(
        "--review-status",
        choices=["unreviewed", "human_reviewed", "machine_validated"],
        default="unreviewed",
    )
    recover_ingest.add_argument("--json", action="store_true")
    recover_ingest.set_defaults(handler=_command_recover_ingest)

    repair_parser = subparsers.add_parser("repair", help="Repair narrowly defined ledger damage")
    repair_parser.add_argument("problem_id")
    repair_parser.add_argument("--truncate-invalid-tail", action="store_true")
    repair_parser.set_defaults(handler=_command_repair)

    brief_parser = subparsers.add_parser(
        "brief", help="Print a compact, model-facing description of one problem and its logging contract"
    )
    brief_parser.add_argument("problem_id")
    brief_parser.add_argument("--tail", type=int, default=8, help="number of recent events to include")
    brief_parser.add_argument("--json", action="store_true")
    brief_parser.set_defaults(handler=_command_brief)

    template_parser = subparsers.add_parser("template", help="Print an AI-friendly JSONL draft that must be edited before ingestion")
    template_parser.add_argument("problem_id", nargs="?")
    template_parser.add_argument("--run-id")
    template_parser.add_argument("--actor-name", default="ChatGPT")
    template_parser.add_argument("--provider", default="OpenAI")
    template_parser.add_argument("--model", default="unknown")
    template_parser.add_argument("--interface", default="chatgpt-pro")
    template_parser.add_argument(
        "--independent",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="default is non-independent because the template reads existing problem state",
    )
    template_parser.add_argument("--objective", default="REPLACE_ME")
    template_parser.set_defaults(handler=_command_template)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        root = Path(args.root).expanduser().resolve() if args.root else find_repo_root()
        return int(args.handler(root, args))
    except (ResearchLogError, OSError, ValueError) as exc:
        print(f"researchlog: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
