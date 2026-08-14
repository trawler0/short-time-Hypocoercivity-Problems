"""Core repository, ledger, integrity, and diagnostics operations."""

from __future__ import annotations

import copy
import hashlib
import json
import mimetypes
import os
import re
import subprocess
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Iterator, Sequence

from .constants import (
    CAPTURE_COMPLETENESS,
    IMMUTABLE_EVENT_FIELDS,
    PROBLEM_SCHEMA_VERSION,
    REGISTRATION_MODES,
    REVIEW_STATUSES,
    SCHEMA_VERSION,
)
from .schema import require_valid, validate_event_draft, validate_problem, validate_problem_id


@dataclass(frozen=True)
class Issue:
    severity: str
    code: str
    message: str
    path: str | None = None
    line: int | None = None
    event_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


class ResearchLogError(RuntimeError):
    """User-facing error raised by the package."""


class LedgerCorruptionError(ResearchLogError):
    """Raised when a canonical ledger is not safe to append to."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def compact_timestamp(timestamp: str | None = None) -> str:
    value = timestamp or utc_now()
    return re.sub(r"[-:.]", "", value).replace("+0000", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}_{compact_timestamp()}_{uuid.uuid4().hex[:8]}"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_revision(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    revision = result.stdout.strip()
    return revision if re.fullmatch(r"[0-9a-f]{40,64}", revision) else None


def find_repo_root(start: str | Path | None = None) -> Path:
    env = os.environ.get("RESEARCHLOG_ROOT")
    current = Path(env or start or Path.cwd()).expanduser().resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        pyproject = candidate / "pyproject.toml"
        if pyproject.exists():
            try:
                text = pyproject.read_text(encoding="utf-8")
            except OSError:
                text = ""
            if "[tool.researchlog]" in text:
                return candidate
        if (candidate / "AGENTS.md").exists() and (candidate / "schemas").exists():
            return candidate
    raise ResearchLogError(
        "could not find the repository root; use --root or set RESEARCHLOG_ROOT"
    )


def relative_repo_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ResearchLogError(f"path is outside repository: {path}") from exc


def safe_repo_path(root: Path, value: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ResearchLogError(f"invalid repository-relative path: {value!r}")
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts:
        raise ResearchLogError(f"unsafe repository-relative path: {value!r}")
    resolved = (root / Path(*pure.parts)).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ResearchLogError(f"path escapes repository: {value!r}") from exc
    return resolved


def problem_dir(root: Path, problem_id: str) -> Path:
    require_valid(problem_id, validate_problem_id(problem_id))
    return root / "logs" / problem_id


def problem_meta_path(root: Path, problem_id: str) -> Path:
    return problem_dir(root, problem_id) / "problem.json"


def ledger_path(root: Path, problem_id: str) -> Path:
    return problem_dir(root, problem_id) / "state.jsonl"


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ResearchLogError(f"missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ResearchLogError(f"invalid JSON in {path}: {exc}") from exc


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


@contextmanager
def ledger_lock(path: Path, *, timeout: float = 15.0, stale_after: float = 900.0) -> Iterator[None]:
    lock_path = path.with_name(f".{path.name}.lock")
    deadline = time.monotonic() + timeout
    lock_payload = canonical_json({"pid": os.getpid(), "created_at": utc_now()}) + "\n"
    while True:
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
            except FileNotFoundError:
                continue
            if age > stale_after:
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise ResearchLogError(f"timed out waiting for ledger lock {lock_path}")
            time.sleep(0.05)
            continue
        else:
            try:
                os.write(descriptor, lock_payload.encode("utf-8"))
            finally:
                os.close(descriptor)
            break
    try:
        yield
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def load_problem(root: Path, problem_id: str) -> dict[str, Any]:
    path = problem_meta_path(root, problem_id)
    problem = read_json(path)
    errors = validate_problem(problem)
    if errors:
        raise ResearchLogError(f"invalid problem metadata {path}:\n" + "\n".join(errors))
    if problem["problem_id"] != problem_id:
        raise ResearchLogError(
            f"problem metadata id {problem['problem_id']!r} does not match directory {problem_id!r}"
        )
    return problem


def init_problem(
    root: Path,
    problem_id: str,
    *,
    title: str,
    status: str = "active",
    domains: Sequence[str] = (),
    tags: Sequence[str] = (),
    source_refs: Sequence[str] = (),
    human_log: str = "progress.md",
    description: str | None = None,
    owners: Sequence[str] = (),
    actor: dict[str, Any] | None = None,
    created_at: str | None = None,
    adopt_existing: bool = False,
    registration_mode: str = "prospective",
) -> dict[str, Any]:
    require_valid(problem_id, validate_problem_id(problem_id))
    if registration_mode not in REGISTRATION_MODES:
        raise ResearchLogError(
            f"registration_mode must be one of {sorted(REGISTRATION_MODES)}"
        )
    if not isinstance(human_log, str) or not human_log or "\\" in human_log:
        raise ResearchLogError("human_log must be a non-empty relative path using '/' separators")
    human_log_path = PurePosixPath(human_log)
    if human_log_path.is_absolute() or ".." in human_log_path.parts:
        raise ResearchLogError("human_log must stay inside the problem directory")

    directory = problem_dir(root, problem_id)
    directory_nonempty = directory.exists() and any(directory.iterdir())
    if directory_nonempty and not adopt_existing:
        raise ResearchLogError(
            f"problem directory already exists and is not empty: {directory}; "
            "use --adopt-existing to register legacy material without overwriting it"
        )
    if (directory / "problem.json").exists() or (directory / "state.jsonl").exists():
        raise ResearchLogError(
            f"problem {problem_id} is already registered or has a canonical ledger"
        )
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "raw").mkdir(exist_ok=True)
    (directory / "inbox").mkdir(exist_ok=True)
    timestamp = created_at or utc_now()
    problem: dict[str, Any] = {
        "schema_version": PROBLEM_SCHEMA_VERSION,
        "problem_id": problem_id,
        "title": title.strip(),
        "status": status,
        "created_at": timestamp,
        "domains": sorted(set(domains)),
        "tags": sorted(set(tags)),
        "source_refs": list(dict.fromkeys(source_refs)),
        "human_log": f"logs/{problem_id}/{human_log}",
        "owners": list(dict.fromkeys(owners)),
        "metadata": {
            "registration_mode": registration_mode,
            "adopted_existing_directory": bool(directory_nonempty),
        },
    }
    if description:
        problem["description"] = description.strip()
    require_valid(problem, validate_problem(problem))
    write_json_atomic(directory / "problem.json", problem)

    human_path = directory / Path(*human_log_path.parts)
    if not human_path.exists():
        human_path.parent.mkdir(parents=True, exist_ok=True)
        human_path.write_text(
            f"# {title}\n\n"
            f"Problem ID: `{problem_id}`\n\n"
            "## Status\n\nRegistered; no research result has yet been verified in this log.\n\n"
            "## Problem source\n\n"
            + ("\n".join(f"- `{ref}`" for ref in source_refs) if source_refs else "Not recorded.")
            + "\n\n## Current results\n\nNone recorded.\n\n"
            "## Open gaps\n\nNone recorded.\n",
            encoding="utf-8",
            newline="\n",
        )

    ledger = directory / "state.jsonl"
    ledger.touch(exist_ok=False)
    append_drafts(
        root,
        problem_id,
        [
            {
                "event_type": "problem.created",
                "summary": f"Registered research problem: {title}",
                "occurred_at": timestamp,
                "temporal_precision": "exact",
                "stage": "specify",
                "outcome": "not_applicable",
                "actor": actor or {"type": "human", "name": "repository maintainer"},
                "subject": {"type": "problem", "id": problem_id},
                "artifacts": [
                    _artifact_record(root, directory / "problem.json", "problem_metadata"),
                    _artifact_record(root, human_path, "human_log"),
                ],
                "data": {
                    "status": status,
                    "registration_mode": registration_mode,
                    "adopted_existing_directory": bool(directory_nonempty),
                },
                "provenance": {
                    "mode": "backfill" if registration_mode != "prospective" else "live",
                    "source_refs": list(source_refs),
                    "review_status": "human_reviewed",
                },
            }
        ],
    )
    return problem


def _artifact_record(root: Path, path: Path, role: str, description: str | None = None) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        raise ResearchLogError(f"artifact does not exist: {path}")
    record: dict[str, Any] = {
        "path": relative_repo_path(root, path),
        "role": role,
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
        "mime": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
    }
    if description:
        record["description"] = description
    return record


def artifact_record(root: Path, repo_relative_path: str, role: str, description: str | None = None) -> dict[str, Any]:
    return _artifact_record(root, safe_repo_path(root, repo_relative_path), role, description)


def _read_events_unchecked(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not path.exists():
        return events
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise LedgerCorruptionError(
                    f"invalid JSON in {path} line {line_number}: {exc}; run validate/recover before appending"
                ) from exc
            if not isinstance(value, dict):
                raise LedgerCorruptionError(
                    f"non-object event in {path} line {line_number}; run validate/recover before appending"
                )
            events.append(value)
    return events


def load_events(root: Path, problem_id: str) -> list[dict[str, Any]]:
    load_problem(root, problem_id)
    path = ledger_path(root, problem_id)
    issues = validate_ledger(root, problem_id, check_artifacts=False)
    errors = [issue for issue in issues if issue.severity == "error"]
    if errors:
        details = "\n".join(f"{item.code}: {item.message}" for item in errors[:20])
        raise LedgerCorruptionError(f"ledger validation failed for {problem_id}:\n{details}")
    return _read_events_unchecked(path)


def _normalize_provenance(
    draft: dict[str, Any],
    *,
    default_mode: str,
    default_review_status: str,
    input_sha256: str | None,
    repo_revision: str | None,
    base_prompt_sha256: str | None,
) -> None:
    provenance = copy.deepcopy(draft.get("provenance") or {})
    provenance.setdefault("mode", default_mode)
    provenance.setdefault("review_status", default_review_status)
    provenance.setdefault("source_refs", [])
    if repo_revision:
        provenance.setdefault("repo_revision", repo_revision)
    if base_prompt_sha256:
        provenance.setdefault("base_prompt_sha256", base_prompt_sha256)
    if input_sha256:
        provenance.setdefault("input_sha256", input_sha256)
    draft["provenance"] = provenance


def _inherit_batch_context(
    drafts: list[dict[str, Any]],
    *,
    default_actor: dict[str, Any] | None,
    default_run_id: str | None,
) -> None:
    current_actor = copy.deepcopy(default_actor)
    current_run_id = default_run_id
    for draft in drafts:
        event_type = draft.get("event_type")
        if event_type == "run.started":
            current_run_id = draft.get("run_id") or current_run_id or new_id("run")
            draft["run_id"] = current_run_id
            if draft.get("actor"):
                current_actor = copy.deepcopy(draft["actor"])
            elif current_actor:
                draft["actor"] = copy.deepcopy(current_actor)
            if "subject" not in draft:
                draft["subject"] = {"type": "run", "id": current_run_id}
        else:
            if current_run_id and "run_id" not in draft:
                draft["run_id"] = current_run_id
            if current_actor and "actor" not in draft:
                draft["actor"] = copy.deepcopy(current_actor)
            if event_type == "run.ended" and "subject" not in draft and draft.get("run_id"):
                draft["subject"] = {"type": "run", "id": draft["run_id"]}


def append_drafts(
    root: Path,
    problem_id: str,
    drafts: Iterable[dict[str, Any]],
    *,
    default_actor: dict[str, Any] | None = None,
    default_run_id: str | None = None,
    default_mode: str = "live",
    default_review_status: str = "unreviewed",
    input_sha256: str | None = None,
) -> list[dict[str, Any]]:
    """Validate and atomically append a batch of event drafts.

    A lock prevents concurrent writers from assigning the same sequence or
    previous hash.  The write itself is a single append, so a process crash can
    affect at most the final line; ``validate`` and ``recover prepare`` detect
    that condition explicitly.
    """

    root = root.resolve()
    load_problem(root, problem_id)
    batch = [copy.deepcopy(item) for item in drafts]
    if not batch:
        return []
    for index, draft in enumerate(batch, 1):
        forbidden = set(draft) & IMMUTABLE_EVENT_FIELDS
        if forbidden:
            raise ResearchLogError(
                f"draft {index} contains canonical fields managed by the ledger: {sorted(forbidden)}"
            )
    _inherit_batch_context(batch, default_actor=default_actor, default_run_id=default_run_id)
    revision = git_revision(root)
    base_prompt = root / "base_prompt.txt"
    base_prompt_hash = sha256_file(base_prompt) if base_prompt.is_file() else None
    for draft in batch:
        _normalize_provenance(
            draft,
            default_mode=default_mode,
            default_review_status=default_review_status,
            input_sha256=input_sha256,
            repo_revision=revision,
            base_prompt_sha256=base_prompt_hash,
        )
        require_valid(draft, validate_event_draft(draft))

    path = ledger_path(root, problem_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    with ledger_lock(path):
        issues = validate_ledger(root, problem_id, check_artifacts=False)
        errors = [item for item in issues if item.severity == "error"]
        if errors:
            details = "\n".join(f"{item.code}: {item.message}" for item in errors[:20])
            raise LedgerCorruptionError(
                f"refusing to append to invalid ledger {path}:\n{details}"
            )
        existing = _read_events_unchecked(path)
        if input_sha256:
            already_ingested = [
                event
                for event in existing
                if (event.get("provenance") or {}).get("input_sha256") == input_sha256
            ]
            if already_ingested:
                if len(already_ingested) != len(batch):
                    raise LedgerCorruptionError(
                        "ledger contains only part of a previously ingested batch "
                        f"with input SHA-256 {input_sha256}; run validate/recover before retrying"
                    )
                return already_ingested
        sequence = existing[-1]["sequence"] if existing else 0
        previous_hash = existing[-1]["event_hash"] if existing else None
        canonical_events: list[dict[str, Any]] = []
        for draft in batch:
            sequence += 1
            event: dict[str, Any] = {
                "schema_version": SCHEMA_VERSION,
                "event_id": new_id("evt"),
                "problem_id": problem_id,
                "sequence": sequence,
                "recorded_at": utc_now(),
                **draft,
                "prev_event_hash": previous_hash,
            }
            digest_payload = canonical_json(event).encode("utf-8")
            event["event_hash"] = sha256_bytes(digest_payload)
            require_valid(event, validate_event_draft(event, canonical=True))
            canonical_events.append(event)
            previous_hash = event["event_hash"]
        text = "".join(canonical_json(event) + "\n" for event in canonical_events)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    return canonical_events


def parse_drafts_text(text: str, *, source: str = "<input>") -> list[dict[str, Any]]:
    stripped = text.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ResearchLogError(f"invalid JSON array in {source}: {exc}") from exc
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise ResearchLogError(f"{source} must contain a JSON array of objects")
        return value
    drafts: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ResearchLogError(f"invalid JSON in {source} line {line_number}: {exc}") from exc
        if not isinstance(value, dict):
            raise ResearchLogError(f"{source} line {line_number} must be a JSON object")
        drafts.append(value)
    return drafts


def ingest_file(
    root: Path,
    problem_id: str,
    path: Path,
    *,
    default_actor: dict[str, Any] | None = None,
    default_run_id: str | None = None,
    default_mode: str = "live",
    default_review_status: str = "unreviewed",
    consume: bool = False,
) -> list[dict[str, Any]]:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ResearchLogError(f"event draft file must be UTF-8: {path}") from exc
    drafts = parse_drafts_text(text, source=str(path))
    events = append_drafts(
        root,
        problem_id,
        drafts,
        default_actor=default_actor,
        default_run_id=default_run_id,
        default_mode=default_mode,
        default_review_status=default_review_status,
        input_sha256=sha256_bytes(raw),
    )
    if consume:
        path.unlink()
    return events


def iter_problem_ids(root: Path) -> Iterator[str]:
    logs = root / "logs"
    if not logs.exists():
        return
    for path in sorted(logs.iterdir()):
        if path.is_dir() and (path / "problem.json").exists():
            yield path.name


def validate_ledger(
    root: Path,
    problem_id: str,
    *,
    check_artifacts: bool = True,
) -> list[Issue]:
    issues: list[Issue] = []
    directory = problem_dir(root, problem_id)
    meta_path = directory / "problem.json"
    if not meta_path.exists():
        return [Issue("error", "problem.missing", "problem.json is missing", str(meta_path))]
    try:
        problem = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [Issue("error", "problem.invalid_json", str(exc), str(meta_path))]
    for message in validate_problem(problem):
        issues.append(Issue("error", "problem.schema", message, str(meta_path)))
    if problem.get("problem_id") != problem_id:
        issues.append(
            Issue(
                "error",
                "problem.id_mismatch",
                f"metadata id {problem.get('problem_id')!r} differs from directory {problem_id!r}",
                str(meta_path),
            )
        )

    path = directory / "state.jsonl"
    if not path.exists():
        issues.append(Issue("error", "ledger.missing", "state.jsonl is missing", str(path)))
        return issues

    try:
        raw = path.read_bytes()
    except OSError as exc:
        issues.append(Issue("error", "ledger.read", str(exc), str(path)))
        return issues
    if raw and not raw.endswith(b"\n"):
        issues.append(
            Issue(
                "error",
                "ledger.truncated_tail",
                "ledger does not end with a newline; the last append may be truncated",
                str(path),
            )
        )

    previous_hash: str | None = None
    expected_sequence = 1
    seen_ids: set[str] = set()
    event_ids: set[str] = set()
    annotation_targets: list[tuple[int, str, str]] = []
    open_runs: dict[str, int] = {}
    latest_artifacts: dict[str, tuple[int, str | None, dict[str, Any]]] = {}
    for line_number, raw_line in enumerate(raw.splitlines(), 1):
        if not raw_line.strip():
            issues.append(
                Issue("warning", "ledger.blank_line", "blank line in ledger", str(path), line_number)
            )
            continue
        try:
            line = raw_line.decode("utf-8")
            event = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            issues.append(
                Issue("error", "ledger.invalid_json", str(exc), str(path), line_number)
            )
            continue
        event_id = event.get("event_id") if isinstance(event, dict) else None
        for message in validate_event_draft(event, canonical=True):
            issues.append(
                Issue("error", "event.schema", message, str(path), line_number, event_id)
            )
        if not isinstance(event, dict):
            continue
        if event.get("problem_id") != problem_id:
            issues.append(
                Issue(
                    "error",
                    "event.problem_mismatch",
                    f"event problem_id is {event.get('problem_id')!r}",
                    str(path),
                    line_number,
                    event_id,
                )
            )
        if event.get("sequence") != expected_sequence:
            issues.append(
                Issue(
                    "error",
                    "event.sequence",
                    f"expected sequence {expected_sequence}, found {event.get('sequence')!r}",
                    str(path),
                    line_number,
                    event_id,
                )
            )
        expected_sequence += 1
        if event_id in seen_ids:
            issues.append(
                Issue("error", "event.duplicate_id", f"duplicate event id {event_id}", str(path), line_number, event_id)
            )
        elif isinstance(event_id, str):
            seen_ids.add(event_id)
            event_ids.add(event_id)
        if event.get("prev_event_hash") != previous_hash:
            issues.append(
                Issue(
                    "error",
                    "event.prev_hash",
                    f"expected previous hash {previous_hash!r}, found {event.get('prev_event_hash')!r}",
                    str(path),
                    line_number,
                    event_id,
                )
            )
        claimed_hash = event.get("event_hash")
        payload = dict(event)
        payload.pop("event_hash", None)
        computed_hash = sha256_bytes(canonical_json(payload).encode("utf-8"))
        if claimed_hash != computed_hash:
            issues.append(
                Issue(
                    "error",
                    "event.hash",
                    f"hash mismatch; computed {computed_hash}",
                    str(path),
                    line_number,
                    event_id,
                )
            )
        previous_hash = claimed_hash if isinstance(claimed_hash, str) else None

        if event.get("event_type") == "annotation.added":
            target = (event.get("data") or {}).get("target_event_id")
            if isinstance(target, str):
                annotation_targets.append((line_number, event_id or "", target))
        run_id = event.get("run_id")
        if event.get("event_type") == "run.started" and isinstance(run_id, str):
            if run_id in open_runs:
                issues.append(
                    Issue(
                        "error",
                        "run.duplicate_start",
                        f"run {run_id} was already started at line {open_runs[run_id]}",
                        str(path),
                        line_number,
                        event_id,
                    )
                )
            open_runs[run_id] = line_number
        elif event.get("event_type") == "run.ended" and isinstance(run_id, str):
            if run_id not in open_runs:
                issues.append(
                    Issue(
                        "warning",
                        "run.end_without_start",
                        f"run {run_id} ended without a preceding start in this ledger",
                        str(path),
                        line_number,
                        event_id,
                    )
                )
            else:
                open_runs.pop(run_id)
            completeness = (event.get("data") or {}).get("capture_completeness")
            if completeness not in CAPTURE_COMPLETENESS:
                issues.append(
                    Issue(
                        "warning",
                        "run.capture_completeness",
                        "run.ended should record data.capture_completeness",
                        str(path),
                        line_number,
                        event_id,
                    )
                )

        actor = event.get("actor") or {}
        if actor.get("type") == "ai" and not actor.get("model"):
            issues.append(
                Issue(
                    "warning",
                    "actor.model_missing",
                    "AI event does not identify the model; use 'unknown' when it cannot be recovered",
                    str(path),
                    line_number,
                    event_id,
                )
            )

        if check_artifacts:
            for artifact in event.get("artifacts") or []:
                artifact_path_value = artifact.get("path")
                if isinstance(artifact_path_value, str):
                    # A snapshot is a historical fact.  Compare the working tree
                    # only with the newest snapshot for a path; otherwise every
                    # legitimate later edit would make all old snapshots warn.
                    latest_artifacts[artifact_path_value] = (line_number, event_id, artifact)

    if check_artifacts:
        for artifact_path_value, (line_number, event_id, artifact) in latest_artifacts.items():
            try:
                artifact_path = safe_repo_path(root, artifact_path_value)
            except ResearchLogError as exc:
                issues.append(
                    Issue("error", "artifact.path", str(exc), str(path), line_number, event_id)
                )
                continue
            if not artifact_path.exists():
                issues.append(
                    Issue(
                        "warning",
                        "artifact.missing",
                        f"referenced artifact does not exist: {artifact_path_value}",
                        str(path),
                        line_number,
                        event_id,
                    )
                )
            elif artifact.get("sha256") and sha256_file(artifact_path) != artifact["sha256"]:
                issues.append(
                    Issue(
                        "warning",
                        "artifact.changed",
                        f"artifact differs from its latest recorded snapshot: {artifact_path_value}",
                        str(path),
                        line_number,
                        event_id,
                    )
                )

    for line_number, annotation_id, target in annotation_targets:
        if target not in event_ids:
            issues.append(
                Issue(
                    "error",
                    "annotation.dangling_target",
                    f"annotation target does not exist in ledger: {target}",
                    str(path),
                    line_number,
                    annotation_id,
                )
            )
    for run_id, line_number in open_runs.items():
        issues.append(
            Issue(
                "warning",
                "run.open",
                f"run {run_id} started at line {line_number} has no run.ended event",
                str(path),
                line_number,
            )
        )

    inbox = directory / "inbox"
    if inbox.exists():
        for pending in sorted(inbox.glob("*.jsonl")):
            issues.append(
                Issue(
                    "warning",
                    "inbox.pending",
                    f"pending event draft has not been ingested: {relative_repo_path(root, pending)}",
                    str(pending),
                )
            )
    return issues


def validate_repository(root: Path, problem_id: str | None = None) -> list[Issue]:
    ids = [problem_id] if problem_id else list(iter_problem_ids(root))
    issues: list[Issue] = []
    if not ids:
        issues.append(Issue("warning", "repository.no_problems", "no registered problems found"))
    for item in ids:
        issues.extend(validate_ledger(root, item))
    return issues


def get_nested(value: Any, dotted_path: str) -> Any:
    current = value
    for part in dotted_path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def load_analytics_profile(root: Path, path: str | Path | None = None) -> dict[str, Any]:
    profile_path = Path(path) if path else root / "schemas" / "analytics-profile-v1.json"
    if not profile_path.is_absolute():
        profile_path = root / profile_path
    profile = read_json(profile_path)
    if not isinstance(profile, dict) or not isinstance(profile.get("event_requirements"), dict):
        raise ResearchLogError(f"invalid analytics profile: {profile_path}")
    return profile


def profile_issues(root: Path, problem_id: str, profile: dict[str, Any]) -> list[Issue]:
    issues: list[Issue] = []
    try:
        problem = load_problem(root, problem_id)
    except ResearchLogError as exc:
        return [Issue("error", "profile.problem", str(exc))]
    for field in profile.get("problem_requirements", []):
        if get_nested(problem, field) in (None, "", []):
            issues.append(
                Issue(
                    "warning",
                    "profile.problem_field",
                    f"analytics profile is missing problem field {field}",
                    relative_repo_path(root, problem_meta_path(root, problem_id)),
                )
            )
    try:
        events = _read_events_unchecked(ledger_path(root, problem_id))
    except LedgerCorruptionError as exc:
        return [Issue("error", "profile.ledger", str(exc))]
    requirements = profile.get("event_requirements", {})
    for event in events:
        for field in requirements.get(event.get("event_type"), []):
            if get_nested(event, field) in (None, "", []):
                issues.append(
                    Issue(
                        "warning",
                        "profile.event_field",
                        f"{event.get('event_type')} is missing analytics field {field}",
                        relative_repo_path(root, ledger_path(root, problem_id)),
                        event.get("sequence"),
                        event.get("event_id"),
                    )
                )
    required_types = profile.get("required_event_types", [])
    seen_types = {event.get("event_type") for event in events}
    for event_type in required_types:
        if event_type not in seen_types:
            issues.append(
                Issue(
                    "warning",
                    "profile.event_type",
                    f"analytics profile has no {event_type} event",
                    relative_repo_path(root, ledger_path(root, problem_id)),
                )
            )
    return issues


def doctor_repository(
    root: Path,
    problem_id: str | None = None,
    *,
    profile_path: str | Path | None = None,
) -> list[Issue]:
    issues = validate_repository(root, problem_id)
    try:
        profile = load_analytics_profile(root, profile_path)
    except ResearchLogError as exc:
        issues.append(Issue("error", "profile.invalid", str(exc)))
        return issues
    ids = [problem_id] if problem_id else list(iter_problem_ids(root))
    for item in ids:
        issues.extend(profile_issues(root, item, profile))
    return issues


def snapshot_paths(
    root: Path,
    problem_id: str,
    paths: Sequence[str],
    *,
    role: str,
    summary: str | None = None,
    actor: dict[str, Any] | None = None,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    artifacts = [artifact_record(root, value, role) for value in paths]
    draft: dict[str, Any] = {
        "event_type": "artifact.recorded",
        "summary": summary or f"Recorded {len(artifacts)} artifact snapshot(s)",
        "stage": "maintain",
        "outcome": "not_applicable",
        "actor": actor or {"type": "human", "name": "repository maintainer"},
        "subject": {"type": "artifact", "id": new_id("artifact_set")},
        "artifacts": artifacts,
        "data": {"artifact_count": len(artifacts)},
    }
    if run_id:
        draft["run_id"] = run_id
    return append_drafts(root, problem_id, [draft])


def repair_truncated_tail(root: Path, problem_id: str) -> Path | None:
    """Quarantine and remove only an invalid final fragment.

    This deliberately refuses to repair corruption before the final newline.
    """

    path = ledger_path(root, problem_id)
    raw = path.read_bytes()
    if not raw or raw.endswith(b"\n"):
        return None
    last_newline = raw.rfind(b"\n")
    prefix = raw[: last_newline + 1] if last_newline >= 0 else b""
    fragment = raw[last_newline + 1 :]
    if prefix:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_bytes(prefix)
        os.replace(temporary, path)
    else:
        path.write_bytes(b"")
    quarantine = problem_dir(root, problem_id) / "recovery" / f"truncated-{new_id('fragment')}.bin"
    quarantine.parent.mkdir(parents=True, exist_ok=True)
    quarantine.write_bytes(fragment)
    return quarantine


def issues_exit_code(issues: Sequence[Issue], *, strict: bool = False) -> int:
    if any(item.severity == "error" for item in issues):
        return 2
    if strict and any(item.severity == "warning" for item in issues):
        return 1
    return 0
