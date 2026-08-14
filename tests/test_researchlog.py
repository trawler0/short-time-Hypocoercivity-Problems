from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from researchlog.cli import main as cli_main
from researchlog.constants import (
    ACTOR_TYPES,
    ARTIFACT_ROLES,
    EVENT_TYPES,
    EVIDENCE_RESULTS,
    EVIDENCE_TYPES,
    OUTCOMES,
    PROBLEM_STATUSES,
    REGISTRATION_MODES,
    PROVENANCE_MODES,
    REVIEW_STATUSES,
    STAGES,
    SUBJECT_TYPES,
    TEMPORAL_PRECISIONS,
)
from researchlog.core import (
    append_drafts,
    canonical_json,
    doctor_repository,
    ingest_file,
    init_problem,
    ledger_path,
    repair_truncated_tail,
    validate_repository,
)
from researchlog.export import export_repository
from researchlog.recovery import prepare_recovery
from researchlog.schema import validate_event_draft


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ResearchLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "schemas").mkdir()
        for name in (
            "event-v1.schema.json",
            "event-draft-v1.schema.json",
            "problem-v1.schema.json",
            "analytics-profile-v1.json",
        ):
            (self.root / "schemas" / name).write_bytes(
                (PROJECT_ROOT / "schemas" / name).read_bytes()
            )
        (self.root / "docs").mkdir()
        (self.root / "docs" / "research-logging.md").write_text("protocol\n", encoding="utf-8")
        (self.root / "AGENTS.md").write_text("agent protocol\n", encoding="utf-8")
        (self.root / "pyproject.toml").write_text("[tool.researchlog]\n", encoding="utf-8")
        (self.root / "problems").mkdir()
        (self.root / "problems" / "problem.txt").write_text("Test problem\n", encoding="utf-8")
        init_problem(
            self.root,
            "problem-test",
            title="Test problem",
            domains=["mathematics"],
            source_refs=["problems/problem.txt#L1"],
            actor={"type": "human", "name": "tester"},
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _append_complete_run(self) -> list[dict]:
        run_id = "run_test"
        return append_drafts(
            self.root,
            "problem-test",
            [
                {
                    "event_type": "run.started",
                    "summary": "Started a complete test run.",
                    "stage": "specify",
                    "outcome": "not_applicable",
                    "run_id": run_id,
                    "actor": {
                        "type": "ai",
                        "name": "test model",
                        "model": "test-v1",
                        "interface": "unit-test",
                    },
                    "data": {
                        "interface": "unit-test",
                        "independent": True,
                        "objective": "Exercise the ledger.",
                    },
                },
                {
                    "event_type": "claim.created",
                    "summary": "Created a test claim.",
                    "stage": "synthesize",
                    "outcome": "partial",
                    "subject": {"type": "claim", "id": "claim_test"},
                    "metrics": {"confidence": 0.5, "impact_score": 1},
                    "data": {
                        "claim_status": "candidate",
                        "verification_status": "unverified",
                        "novelty_status": "unknown",
                    },
                },
                {
                    "event_type": "run.ended",
                    "summary": "Ended the complete test run.",
                    "stage": "reallocate",
                    "outcome": "partial",
                    "run_id": run_id,
                    "data": {
                        "end_reason": "completed",
                        "capture_completeness": "complete",
                        "protocol_complete": True,
                    },
                },
            ],
        )

    def test_append_hash_chain_and_validate(self) -> None:
        events = self._append_complete_run()
        self.assertEqual(3, len(events))
        self.assertEqual(events[0]["event_hash"], events[1]["prev_event_hash"])
        issues = validate_repository(self.root, "problem-test")
        errors = [issue for issue in issues if issue.severity == "error"]
        self.assertEqual([], errors)

    def test_tampering_is_detected(self) -> None:
        self._append_complete_run()
        path = ledger_path(self.root, "problem-test")
        lines = path.read_text(encoding="utf-8").splitlines()
        event = json.loads(lines[1])
        event["summary"] = "tampered"
        lines[1] = canonical_json(event)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        issues = validate_repository(self.root, "problem-test")
        self.assertTrue(any(issue.code == "event.hash" for issue in issues))

    def test_annotation_is_materialized_in_export(self) -> None:
        events = self._append_complete_run()
        claim = events[1]
        append_drafts(
            self.root,
            "problem-test",
            [
                {
                    "event_type": "annotation.added",
                    "summary": "Added a verified novelty status from later review.",
                    "stage": "recover",
                    "outcome": "success",
                    "actor": {"type": "human", "name": "reviewer"},
                    "subject": {"type": "claim", "id": "claim_test"},
                    "data": {
                        "target_event_id": claim["event_id"],
                        "set": {"data.novelty_status": "known"},
                        "reason": "Later literature review.",
                    },
                    "provenance": {
                        "mode": "backfill",
                        "source_refs": ["problems/problem.txt#L1"],
                        "review_status": "human_reviewed",
                    },
                }
            ],
        )
        output = self.root / "export"
        export_repository(self.root, output)
        with (output / "events.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        row = next(item for item in rows if item["event_id"] == claim["event_id"])
        self.assertEqual("known", json.loads(row["data_json"])["novelty_status"])
        self.assertEqual("1", row["annotation_count"])
        self.assertTrue((output / "researchlog.sqlite").exists())

    def test_adopt_existing_problem_directory(self) -> None:
        legacy = self.root / "logs" / "legacy-problem"
        legacy.mkdir(parents=True)
        human_log = legacy / "legacy-progress.tex"
        human_log.write_text("Legacy research log\n", encoding="utf-8")

        problem = init_problem(
            self.root,
            "legacy-problem",
            title="Legacy problem",
            human_log="legacy-progress.tex",
            source_refs=["logs/legacy-problem/legacy-progress.tex"],
            actor={"type": "human", "name": "tester"},
            adopt_existing=True,
            registration_mode="retrospective",
        )

        self.assertEqual("Legacy research log\n", human_log.read_text(encoding="utf-8"))
        self.assertEqual("retrospective", problem["metadata"]["registration_mode"])
        self.assertTrue(problem["metadata"]["adopted_existing_directory"])
        first_event = json.loads(
            (legacy / "state.jsonl").read_text(encoding="utf-8").splitlines()[0]
        )
        self.assertEqual("backfill", first_event["provenance"]["mode"])

    def test_inbox_batch_is_consumed_only_after_success(self) -> None:
        inbox = self.root / "logs" / "problem-test" / "inbox" / "run.jsonl"
        inbox.write_text(
            "\n".join(
                canonical_json(value)
                for value in [
                    {
                        "event_type": "run.started",
                        "summary": "Inbox run started.",
                        "stage": "specify",
                        "outcome": "not_applicable",
                        "run_id": "run_inbox",
                        "actor": {
                            "type": "ai",
                            "name": "inbox model",
                            "model": "test-v1",
                            "interface": "patch",
                        },
                        "data": {
                            "interface": "patch",
                            "independent": True,
                            "objective": "Test inbox ingestion.",
                        },
                    },
                    {
                        "event_type": "run.ended",
                        "summary": "Inbox run ended.",
                        "stage": "reallocate",
                        "outcome": "inconclusive",
                        "run_id": "run_inbox",
                        "data": {
                            "end_reason": "completed",
                            "capture_completeness": "complete",
                            "protocol_complete": True,
                        },
                    },
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        events = ingest_file(self.root, "problem-test", inbox, consume=True)
        self.assertEqual(2, len(events))
        self.assertFalse(inbox.exists())
        self.assertEqual([], [i for i in validate_repository(self.root) if i.severity == "error"])

    def test_inbox_retry_is_idempotent(self) -> None:
        inbox = self.root / "logs" / "problem-test" / "inbox" / "retry.jsonl"
        content = canonical_json(
            {
                "event_type": "note",
                "summary": "Exactly-once inbox test.",
                "stage": "maintain",
                "outcome": "not_applicable",
                "actor": {"type": "tool", "name": "unit-test"},
            }
        ) + "\n"
        inbox.write_text(content, encoding="utf-8")
        first = ingest_file(self.root, "problem-test", inbox, consume=True)
        event_count = len(
            (self.root / "logs" / "problem-test" / "state.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        )

        # Simulate a crash after append but before the inbox file was deleted.
        inbox.write_text(content, encoding="utf-8")
        second = ingest_file(self.root, "problem-test", inbox, consume=True)
        new_event_count = len(
            (self.root / "logs" / "problem-test" / "state.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        self.assertEqual(
            [event["event_id"] for event in first],
            [event["event_id"] for event in second],
        )
        self.assertEqual(event_count, new_event_count)
        self.assertFalse(inbox.exists())

    def test_prepare_all_respects_reviewed_gap_resolution(self) -> None:
        gap = append_drafts(
            self.root,
            "problem-test",
            [
                {
                    "event_type": "protocol.gap",
                    "summary": "Temporary protocol gap.",
                    "stage": "recover",
                    "outcome": "partial",
                    "actor": {"type": "human", "name": "tester"},
                    "subject": {"type": "recovery", "id": "gap_test"},
                    "data": {
                        "missing_fields": ["data.example"],
                        "recovery_recommended": True,
                    },
                }
            ],
        )[0]
        append_drafts(
            self.root,
            "problem-test",
            [
                {
                    "event_type": "annotation.added",
                    "summary": "Marked the temporary gap as resolved.",
                    "stage": "recover",
                    "outcome": "success",
                    "actor": {"type": "human", "name": "reviewer"},
                    "subject": {"type": "recovery", "id": "gap_test"},
                    "data": {
                        "target_event_id": gap["event_id"],
                        "set": {"data.recovery_recommended": False},
                        "reason": "The missing field was recovered and reviewed.",
                    },
                    "provenance": {
                        "mode": "recovery",
                        "source_refs": ["problems/problem.txt#L1"],
                        "extraction_confidence": 1.0,
                        "review_status": "human_reviewed",
                        "recovery_bundle_id": "recovery_test",
                    },
                }
            ],
        )
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli_main(
                [
                    "--root",
                    str(self.root),
                    "recover",
                    "prepare-all",
                    "--json",
                ]
            )
        self.assertEqual(0, code)
        payload = json.loads(output.getvalue())
        self.assertEqual([], payload["prepared"])
        self.assertEqual(["problem-test"], payload["skipped"])

    def test_recovery_bundle_reports_profile_gaps(self) -> None:
        append_drafts(
            self.root,
            "problem-test",
            [
                {
                    "event_type": "run.started",
                    "summary": "Historical run with intentionally sparse metadata.",
                    "stage": "specify",
                    "outcome": "not_applicable",
                    "run_id": "run_sparse",
                    "actor": {"type": "ai", "name": "unknown model", "model": "unknown"},
                    "data": {},
                    "provenance": {
                        "mode": "backfill",
                        "source_refs": ["problems/problem.txt#L1"],
                        "review_status": "human_reviewed",
                    },
                },
                {
                    "event_type": "run.ended",
                    "summary": "Historical sparse run ended.",
                    "stage": "reallocate",
                    "outcome": "unknown",
                    "run_id": "run_sparse",
                    "data": {
                        "end_reason": "unknown",
                        "capture_completeness": "partial",
                        "protocol_complete": False,
                    },
                    "provenance": {
                        "mode": "backfill",
                        "source_refs": ["problems/problem.txt#L1"],
                        "review_status": "human_reviewed",
                    },
                },
            ],
        )
        diagnostics = doctor_repository(self.root, "problem-test")
        self.assertTrue(any(issue.code == "profile.event_field" for issue in diagnostics))
        manifest = prepare_recovery(self.root, "problem-test")
        bundle = self.root / "logs" / "problem-test" / "recovery" / manifest["bundle_id"]
        self.assertTrue((bundle / "manifest.json").exists())
        self.assertTrue((bundle / "prompt.md").exists())
        self.assertGreater(len(manifest["analytics_gaps"]), 0)


    def test_published_schemas_match_controlled_vocabularies(self) -> None:
        for filename in ("event-draft-v1.schema.json", "event-v1.schema.json"):
            schema = json.loads((PROJECT_ROOT / "schemas" / filename).read_text(encoding="utf-8"))
            properties = schema["properties"]
            definitions = schema["$defs"]
            self.assertEqual(EVENT_TYPES, frozenset(properties["event_type"]["anyOf"][0]["enum"]))
            self.assertEqual(STAGES, frozenset(properties["stage"]["enum"]))
            self.assertEqual(OUTCOMES, frozenset(properties["outcome"]["enum"]))
            self.assertEqual(
                TEMPORAL_PRECISIONS,
                frozenset(properties["temporal_precision"]["enum"]),
            )
            self.assertEqual(ACTOR_TYPES, frozenset(definitions["actor"]["properties"]["type"]["enum"]))
            self.assertEqual(SUBJECT_TYPES, frozenset(definitions["subject"]["properties"]["type"]["enum"]))
            provenance = definitions["provenance"]["properties"]
            self.assertEqual(PROVENANCE_MODES, frozenset(provenance["mode"]["enum"]))
            self.assertEqual(REVIEW_STATUSES, frozenset(provenance["review_status"]["enum"]))
            self.assertEqual(
                ARTIFACT_ROLES,
                frozenset(definitions["artifact"]["properties"]["role"]["enum"]),
            )
            evidence = definitions["evidence"]["properties"]
            self.assertEqual(EVIDENCE_TYPES, frozenset(evidence["type"]["enum"]))
            self.assertEqual(EVIDENCE_RESULTS, frozenset(evidence["result"]["enum"]))

        problem_schema = json.loads(
            (PROJECT_ROOT / "schemas" / "problem-v1.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            PROBLEM_STATUSES,
            frozenset(problem_schema["properties"]["status"]["enum"]),
        )
        self.assertEqual(
            REGISTRATION_MODES,
            frozenset(
                problem_schema["properties"]["metadata"]["properties"]
                ["registration_mode"]["enum"]
            ),
        )

    def test_unedited_template_placeholders_are_rejected(self) -> None:
        errors = validate_event_draft(
            {
                "event_type": "note",
                "summary": "REPLACE_WITH_A_REAL_SUMMARY",
            }
        )
        self.assertTrue(any("template placeholder" in error for error in errors))

    def test_truncated_tail_is_quarantined(self) -> None:
        path = ledger_path(self.root, "problem-test")
        with path.open("ab") as handle:
            handle.write(b'{"incomplete":')
        quarantine = repair_truncated_tail(self.root, "problem-test")
        self.assertIsNotNone(quarantine)
        self.assertTrue(quarantine.exists())
        self.assertTrue(path.read_bytes().endswith(b"\n"))
        self.assertEqual([], [i for i in validate_repository(self.root) if i.severity == "error"])


if __name__ == "__main__":
    unittest.main()
