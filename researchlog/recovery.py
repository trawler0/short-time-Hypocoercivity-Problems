"""Recovery bundles for missing or obsolete structured provenance."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .core import (
    Issue,
    ResearchLogError,
    append_drafts,
    canonical_json,
    doctor_repository,
    iter_problem_ids,
    ledger_path,
    load_problem,
    new_id,
    parse_drafts_text,
    problem_dir,
    relative_repo_path,
    safe_repo_path,
    sha256_bytes,
    sha256_file,
    utc_now,
    validate_ledger,
    write_json_atomic,
)


def _inventory_file(root: Path, path: Path, role: str) -> dict[str, Any]:
    return {
        "path": relative_repo_path(root, path),
        "role": role,
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _collect_source_paths(root: Path, problem_id: str) -> list[dict[str, Any]]:
    problem = load_problem(root, problem_id)
    candidates: dict[str, str] = {}
    directory = problem_dir(root, problem_id)
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        relative = relative_repo_path(root, path)
        if "/recovery/" in f"/{relative}/" or "/inbox/" in f"/{relative}/":
            continue
        role = "state_log" if path.name == "state.jsonl" else "raw_or_human_log"
        candidates[relative] = role
    for source_ref in problem.get("source_refs") or []:
        candidate = source_ref.split("#", 1)[0]
        try:
            path = safe_repo_path(root, candidate)
        except ResearchLogError:
            continue
        if path.is_file():
            candidates[candidate] = "problem_source"
    for always in (
        "base_prompt.txt",
        "AGENTS.md",
        "docs/research-logging.md",
        "docs/data-dictionary.md",
        "schemas/event-draft-v1.schema.json",
        "schemas/problem-v1.schema.json",
        "schemas/analytics-profile-v1.json",
    ):
        path = root / always
        if path.is_file():
            candidates[always] = "protocol"
    return [
        _inventory_file(root, root / relative, role)
        for relative, role in sorted(candidates.items())
        if (root / relative).is_file()
    ]


def _issues_payload(issues: Iterable[Issue]) -> list[dict[str, Any]]:
    return [issue.to_dict() for issue in issues]


def _recovery_prompt(problem_id: str, bundle_id: str, manifest_path: str) -> str:
    return f"""# Structured research-log recovery task

You are reconstructing missing or obsolete **research provenance**, not solving the
research problem again.

Repository protocol:

1. Read `base_prompt.txt`, `AGENTS.md`, `docs/research-logging.md`,
   `docs/data-dictionary.md`, `schemas/event-draft-v1.schema.json`, and
   `{manifest_path}`.
2. Inspect every source file listed in the manifest. Follow references from those
   files when they are available in the repository.
3. Compare the raw/human-readable record with the existing canonical ledger for
   `{problem_id}`. Recover facts that are supported by identifiable source text.
4. For an event that exists but lacks a newly required analytics field, emit an
   `annotation.added` draft targeting that event. Do not rewrite old ledger lines.
   When recovery resolves a `protocol.gap`, annotate that gap with
   `data.recovery_recommended = false`; leave it true when material uncertainty
   remains.
5. For an event that is absent, emit a new historical event draft with
   `provenance.mode = "recovery"`.
6. Every recovered draft must contain:
   - a concise factual `summary`;
   - `provenance.source_refs` pointing to the evidence used;
   - `provenance.extraction_confidence` in `[0,1]`;
   - `provenance.recovery_bundle_id = "{bundle_id}"`;
   - `provenance.review_status = "unreviewed"` unless a human has actually
     reviewed that exact recovered record.
7. Do not invent exact timestamps, model versions, run IDs, costs, human actions,
   verification results, or novelty judgments. Use `unknown`, omit the field, or
   emit a `protocol.gap` event. When order is known but time is not, use
   `temporal_precision = "order_only"` and omit `occurred_at`.
8. Do not include private chain-of-thought. Record only compact conclusions,
   actions, evidence, uncertainty, and source locations.
9. Preserve failures, abandoned routes, partial results, negative verifications,
   and human interventions. Do not recover only the successful narrative.
10. Output **JSONL event drafts only**, one JSON object per line, with no Markdown
    fence and no explanatory prose.

After human inspection, ingest the result with:

```bash
python -m researchlog recover ingest {problem_id} \\
  --bundle {bundle_id} --file recovered-events.jsonl
python -m researchlog validate {problem_id}
python -m researchlog doctor {problem_id}
```
"""


def prepare_recovery(
    root: Path,
    problem_id: str,
    *,
    profile_path: str | Path | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    load_problem(root, problem_id)
    bundle_id = new_id("recovery")
    directory = output_dir or (problem_dir(root, problem_id) / "recovery" / bundle_id)
    if directory.exists():
        raise ResearchLogError(f"recovery bundle already exists: {directory}")
    directory.mkdir(parents=True, exist_ok=False)

    validation = validate_ledger(root, problem_id)
    diagnostics = doctor_repository(root, problem_id, profile_path=profile_path)
    source_files = _collect_source_paths(root, problem_id)
    manifest = {
        "recovery_manifest_version": 1,
        "bundle_id": bundle_id,
        "problem_id": problem_id,
        "created_at": utc_now(),
        "purpose": "Recover missing or obsolete structured research provenance from repository artifacts.",
        "ledger_path": relative_repo_path(root, ledger_path(root, problem_id)),
        "ledger_appendable": not any(issue.severity == "error" for issue in validation),
        "validation_issues": _issues_payload(validation),
        "analytics_gaps": _issues_payload(
            issue for issue in diagnostics if issue.code.startswith("profile.")
        ),
        "source_files": source_files,
        "output_contract": {
            "format": "JSON Lines",
            "schema": "schemas/event-draft-v1.schema.json",
            "allowed_recovery_mechanisms": ["historical_event", "annotation.added", "protocol.gap"],
            "required_provenance_fields": [
                "mode",
                "source_refs",
                "extraction_confidence",
                "recovery_bundle_id",
                "review_status",
            ],
        },
    }
    manifest_path = directory / "manifest.json"
    write_json_atomic(manifest_path, manifest)
    prompt_path = directory / "prompt.md"
    prompt_path.write_text(
        _recovery_prompt(problem_id, bundle_id, relative_repo_path(root, manifest_path)),
        encoding="utf-8",
        newline="\n",
    )
    example = {
        "event_type": "annotation.added",
        "summary": "Recovered one previously unrecorded analytics field from a cited raw log.",
        "stage": "recover",
        "outcome": "partial",
        "actor": {"type": "ai", "name": "frontier recovery model", "model": "identify-exact-model"},
        "subject": {"type": "recovery", "id": bundle_id},
        "data": {
            "target_event_id": "replace-with-existing-event-id",
            "set": {"data.example_field": "replace-with-recovered-value"},
            "reason": "Backfill for analytics profile v1.",
        },
        "provenance": {
            "mode": "recovery",
            "source_refs": ["replace-with-repository-path-and-locator"],
            "extraction_confidence": 0.5,
            "review_status": "unreviewed",
            "recovery_bundle_id": bundle_id,
        },
    }
    (directory / "recovered-events.example.jsonl").write_text(
        canonical_json(example) + "\n", encoding="utf-8", newline="\n"
    )

    if manifest["ledger_appendable"]:
        append_drafts(
            root,
            problem_id,
            [
                {
                    "event_type": "recovery.requested",
                    "summary": "Prepared a recovery bundle for missing or obsolete structured provenance.",
                    "stage": "recover",
                    "outcome": "partial",
                    "actor": {"type": "tool", "name": "researchlog"},
                    "subject": {"type": "recovery", "id": bundle_id},
                    "artifacts": [
                        {
                            "path": relative_repo_path(root, manifest_path),
                            "role": "recovery_manifest",
                            "sha256": sha256_file(manifest_path),
                            "size_bytes": manifest_path.stat().st_size,
                            "mime": "application/json",
                        },
                        {
                            "path": relative_repo_path(root, prompt_path),
                            "role": "recovery_prompt",
                            "sha256": sha256_file(prompt_path),
                            "size_bytes": prompt_path.stat().st_size,
                            "mime": "text/markdown",
                        },
                    ],
                    "data": {
                        "bundle_id": bundle_id,
                        "validation_error_count": sum(
                            issue.severity == "error" for issue in validation
                        ),
                        "analytics_gap_count": len(manifest["analytics_gaps"]),
                    },
                    "provenance": {
                        "mode": "live",
                        "source_refs": [relative_repo_path(root, manifest_path)],
                        "review_status": "machine_validated",
                    },
                }
            ],
        )
    return manifest


def ingest_recovery(
    root: Path,
    problem_id: str,
    *,
    bundle_id: str,
    recovered_file: Path,
    review_status: str = "unreviewed",
) -> list[dict[str, Any]]:
    bundle_dir = problem_dir(root, problem_id) / "recovery" / bundle_id
    manifest_path = bundle_dir / "manifest.json"
    if not manifest_path.exists():
        raise ResearchLogError(f"recovery manifest does not exist: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("problem_id") != problem_id or manifest.get("bundle_id") != bundle_id:
        raise ResearchLogError("recovery bundle identity does not match command arguments")
    if review_status not in {"unreviewed", "human_reviewed", "machine_validated"}:
        raise ResearchLogError("invalid recovery review status")

    raw = recovered_file.read_bytes()
    drafts = parse_drafts_text(raw.decode("utf-8"), source=str(recovered_file))
    default_sources = [entry["path"] for entry in manifest.get("source_files", [])]
    for draft in drafts:
        provenance = dict(draft.get("provenance") or {})
        provenance["mode"] = "recovery"
        provenance["recovery_bundle_id"] = bundle_id
        provenance["review_status"] = review_status
        provenance.setdefault("source_refs", default_sources)
        provenance.setdefault("extraction_confidence", 0.5)
        draft["provenance"] = provenance
        draft.setdefault("stage", "recover")
        draft.setdefault("actor", {"type": "ai", "name": "frontier recovery model", "model": "unknown"})

    events = append_drafts(
        root,
        problem_id,
        drafts,
        default_mode="recovery",
        default_review_status=review_status,
        input_sha256=sha256_bytes(raw),
    )
    completion = append_drafts(
        root,
        problem_id,
        [
            {
                "event_type": "recovery.completed",
                "summary": f"Ingested {len(events)} recovered event or annotation record(s).",
                "stage": "recover",
                "outcome": "success" if events else "inconclusive",
                "actor": {"type": "tool", "name": "researchlog"},
                "subject": {"type": "recovery", "id": bundle_id},
                "data": {
                    "bundle_id": bundle_id,
                    "records_ingested": len(events),
                    "input_sha256": sha256_bytes(raw),
                    "human_reviewed": review_status == "human_reviewed",
                },
                "provenance": {
                    "mode": "live",
                    "source_refs": [relative_repo_path(root, recovered_file)]
                    if recovered_file.resolve().is_relative_to(root.resolve())
                    else [f"external:{recovered_file.name}"],
                    "review_status": review_status,
                },
            }
        ],
    )
    return events + completion
