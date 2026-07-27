from __future__ import annotations

from datetime import datetime, timedelta, timezone
from importlib import resources
from pathlib import Path
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from swarm_core.engine import SwarmEngine
from swarm_core.learning import (
    GoldenResult,
    LessonExporter,
    PromptCandidates,
    ReputationLedger,
    assess_golden_results,
)
from swarm_core.memory import ProjectMemory
from swarm_core.models import ModelResponse
from swarm_core.packs import PackRegistry
from swarm_core.store import ProjectSwarmStore
from swarm_core import store as swarm_store_module


def test_memory_persists_each_kind_with_source_and_evidence_references(
    tmp_path: Path,
):
    """Catches memory types or their provenance being lost across a restart."""
    memory = ProjectMemory(ProjectSwarmStore(tmp_path))

    remembered = [
        memory.remember(
            kind,
            statement,
            claim_key=f"claim:{kind}",
            source_refs=(f"source:{kind}",),
            evidence_refs=(f"evidence:{kind}",),
        )
        for kind, statement in [
            ("fact", "The project has a verified test suite."),
            ("opinion", "The current module boundary is easy to review."),
            ("decision", "Use project-local swarm state."),
            ("evidence", "Verifier run 17 passed the focused suite."),
        ]
    ]

    reopened = ProjectMemory(ProjectSwarmStore(tmp_path))
    restored = reopened.list()

    assert [item.kind for item in restored] == [
        "fact",
        "opinion",
        "decision",
        "evidence",
    ]
    assert [item.item_id for item in restored] == [item.item_id for item in remembered]
    assert restored[0].source_refs == ("source:fact",)
    assert restored[3].evidence_refs == ("evidence:evidence",)


def test_exact_memory_retry_merges_new_provenance_without_losing_prior_refs(
    tmp_path: Path,
):
    """Catches exact-claim retry dedupe silently discarding new provenance."""
    memory = ProjectMemory(ProjectSwarmStore(tmp_path))
    first = memory.remember(
        "fact",
        "The release was verified.",
        claim_key="release-verification",
        source_refs=("source:first",),
        evidence_refs=("evidence:first",),
    )

    merged = memory.remember(
        "fact",
        "The release was verified.",
        claim_key="release-verification",
        source_refs=("source:second",),
        evidence_refs=("evidence:second",),
    )

    assert merged.item_id == first.item_id
    assert merged.source_refs == ("source:first", "source:second")
    assert merged.evidence_refs == ("evidence:first", "evidence:second")
    restored = ProjectMemory(ProjectSwarmStore(tmp_path)).get(merged.item_id)
    assert restored is not None
    assert restored.source_refs == ("source:first", "source:second")
    assert restored.evidence_refs == ("evidence:first", "evidence:second")


def test_memory_lifecycle_hides_stale_and_expired_items_without_deleting_evidence(
    tmp_path: Path,
):
    """Catches retrieval surfacing unvalidated memory or expiry erasing audit data."""
    memory = ProjectMemory(ProjectSwarmStore(tmp_path))
    item = memory.remember(
        "evidence",
        "Verifier output confirms the migration.",
        claim_key="migration-evidence",
        source_refs=("run:17",),
        evidence_refs=("evidence:verifier-17",),
    )

    stale = memory.mark_stale(item.item_id)

    assert stale.lifecycle == "stale"
    assert memory.list() == []
    assert memory.list(audit=True) == [stale]

    revalidated = memory.revalidate(item.item_id)
    expired = memory.expire(item.item_id)

    assert revalidated.lifecycle == "active"
    assert revalidated.revalidated_at is not None
    assert expired.lifecycle == "expired"
    assert memory.list() == []
    assert memory.list(audit=True) == [expired]
    assert expired.statement == "Verifier output confirms the migration."
    assert expired.evidence_refs == ("evidence:verifier-17",)


def test_memory_deadlines_derive_stale_and_expired_reads_without_mutating_storage(
    tmp_path: Path,
):
    """Catches deadline-driven stale/expired state being missing or read-side writes."""
    memory = ProjectMemory(ProjectSwarmStore(tmp_path))
    start = datetime(2030, 1, 1, tzinfo=timezone.utc)
    item = memory.remember(
        "evidence",
        "A deadline-governed verifier result.",
        claim_key="deadline-evidence",
        source_refs=("run:deadline",),
        evidence_refs=("evidence:deadline",),
        revalidate_after=start + timedelta(hours=1),
        expires_at=start + timedelta(hours=2),
    )
    stored_before_read = memory.get(item.item_id, now=start)
    assert stored_before_read is not None

    assert [entry.item_id for entry in memory.list(now=start)] == [item.item_id]
    assert memory.list(now=start + timedelta(hours=1)) == []
    stale = memory.list(audit=True, now=start + timedelta(hours=1))[0]
    assert stale.lifecycle == "stale"
    expired = memory.list(audit=True, now=start + timedelta(hours=3))[0]
    assert expired.lifecycle == "expired"
    assert expired.evidence_refs == ("evidence:deadline",)
    assert (
        memory.get(item.item_id, now=start).updated_at == stored_before_read.updated_at
    )

    revalidated = memory.revalidate(
        item.item_id,
        revalidate_after=start + timedelta(hours=5),
        expires_at=start + timedelta(hours=6),
    )

    assert revalidated.evidence_refs == ("evidence:deadline",)
    reopened = ProjectMemory(ProjectSwarmStore(tmp_path))
    assert (
        reopened.get(item.item_id, now=start + timedelta(hours=4)).lifecycle == "active"
    )
    assert (
        reopened.get(item.item_id, now=start + timedelta(hours=5)).lifecycle == "stale"
    )
    assert (
        reopened.get(item.item_id, now=start + timedelta(hours=7)).lifecycle
        == "expired"
    )


def test_exact_memory_retry_tightens_deadlines_and_persists_them_after_reopen(
    tmp_path: Path,
):
    """Catches duplicate retry making a claim less bounded or dropping deadlines."""
    memory = ProjectMemory(ProjectSwarmStore(tmp_path))
    start = datetime(2030, 1, 1, tzinfo=timezone.utc)
    first = memory.remember(
        "fact",
        "The release scope is bounded.",
        claim_key="bounded-release",
    )

    tightened = memory.remember(
        "fact",
        "The release scope is bounded.",
        claim_key="bounded-release",
        revalidate_after=start + timedelta(hours=1),
        expires_at=start + timedelta(hours=2),
    )
    later_retry = memory.remember(
        "fact",
        "The release scope is bounded.",
        claim_key="bounded-release",
        revalidate_after=start + timedelta(hours=5),
        expires_at=start + timedelta(hours=6),
    )

    assert tightened.item_id == first.item_id
    assert tightened.revalidate_after == start + timedelta(hours=1)
    assert tightened.expires_at == start + timedelta(hours=2)
    assert later_retry.revalidate_after == start + timedelta(hours=1)
    assert later_retry.expires_at == start + timedelta(hours=2)
    reopened = ProjectMemory(ProjectSwarmStore(tmp_path))
    assert reopened.list(now=start + timedelta(hours=3)) == []
    assert (
        reopened.get(
            first.item_id,
            now=start + timedelta(hours=3),
        ).lifecycle
        == "expired"
    )


def test_conflicting_merged_deadlines_fail_closed_as_expired(tmp_path: Path):
    """Catches an invalid merged deadline order leaving a duplicate claim active."""
    memory = ProjectMemory(ProjectSwarmStore(tmp_path))
    start = datetime(2030, 1, 1, tzinfo=timezone.utc)
    memory.remember(
        "fact",
        "The verification window is finite.",
        claim_key="finite-window",
        revalidate_after=start + timedelta(hours=5),
    )

    merged = memory.remember(
        "fact",
        "The verification window is finite.",
        claim_key="finite-window",
        expires_at=start + timedelta(hours=2),
    )

    assert merged.revalidate_after == start + timedelta(hours=5)
    assert merged.expires_at == start + timedelta(hours=2)
    assert merged.lifecycle == "expired"
    assert memory.list(now=start) == []


def test_memory_deadline_columns_migrate_an_existing_task4_memory_table(
    tmp_path: Path,
):
    """Catches deadline migration breaking existing Task4 memory records."""
    database_dir = tmp_path / ".swarm" / "runtime"
    database_dir.mkdir(parents=True)
    database_path = database_dir / "swarm.sqlite"
    created_at = datetime(2026, 7, 27, tzinfo=timezone.utc).isoformat()
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE memory_items (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL,
                claim_key TEXT NOT NULL,
                statement TEXT NOT NULL,
                source_refs_json TEXT NOT NULL,
                evidence_refs_json TEXT NOT NULL,
                lifecycle TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                revalidated_at TEXT,
                lesson_opt_in INTEGER NOT NULL DEFAULT 0,
                redacted_statement TEXT,
                UNIQUE (kind, claim_key, statement)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO memory_items (
                item_id, kind, claim_key, statement, source_refs_json,
                evidence_refs_json, lifecycle, created_at, updated_at,
                revalidated_at, lesson_opt_in, redacted_statement
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-memory",
                "fact",
                "legacy-key",
                "Preserve this legacy memory.",
                '["source:legacy"]',
                '["evidence:legacy"]',
                "active",
                created_at,
                created_at,
                None,
                0,
                None,
            ),
        )

    store = ProjectSwarmStore(tmp_path)
    migrated = ProjectMemory(store).get("legacy-memory")
    with sqlite3.connect(store.db_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(memory_items)")
        }

    assert {"revalidate_after", "expires_at"} <= columns
    assert migrated is not None
    assert migrated.statement == "Preserve this legacy memory."
    assert migrated.evidence_refs == ("evidence:legacy",)


def test_conflicting_same_kind_claim_creates_one_durable_clarification_and_event(
    tmp_path: Path,
):
    """Catches a contradiction being overwritten, ignored, or duplicated on retry."""
    memory = ProjectMemory(ProjectSwarmStore(tmp_path))
    first = memory.remember(
        "fact",
        "The deployment requires approval.",
        claim_key="deployment-approval",
        evidence_refs=("evidence:one",),
    )
    conflicting = memory.remember(
        "fact",
        "The deployment does not require approval.",
        claim_key="deployment-approval",
        evidence_refs=("evidence:two",),
    )
    retry = memory.remember(
        "fact",
        "The deployment does not require approval.",
        claim_key="deployment-approval",
        evidence_refs=("evidence:two",),
    )

    assert retry.item_id == conflicting.item_id
    assert {item.item_id for item in memory.list(audit=True)} == {
        first.item_id,
        conflicting.item_id,
    }
    clarifications = memory.list_clarifications()
    assert [(task.kind, task.claim_key, task.status) for task in clarifications] == [
        ("fact", "deployment-approval", "open")
    ]
    assert [event.event_type for event in memory.list_events()] == [
        "memory.conflict_detected"
    ]


def test_memory_schema_additions_preserve_a_legacy_run_database(tmp_path: Path):
    """Catches an additive memory migration destroying previously persisted runs."""
    database_dir = tmp_path / ".swarm" / "runtime"
    database_dir.mkdir(parents=True)
    database_path = database_dir / "swarm.sqlite"
    created_at = datetime(2026, 7, 27, tzinfo=timezone.utc).isoformat()
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?)",
            ("legacy-run", "running", created_at, created_at, "{}"),
        )

    store = ProjectSwarmStore(tmp_path)
    remembered = ProjectMemory(store).remember(
        "decision",
        "Keep compatibility during the migration.",
        claim_key="migration-decision",
    )

    assert store.get_run("legacy-run").run_id == "legacy-run"
    assert ProjectMemory(ProjectSwarmStore(tmp_path)).list() == [remembered]


def test_reputation_is_role_capability_scoped_persistent_and_reference_deduplicated(
    tmp_path: Path,
):
    """Catches repeated evidence inflating a reputation or leaking across roles."""
    ledger = ReputationLedger(ProjectSwarmStore(tmp_path))
    first = GoldenResult("verifier:builder:one", score=0.8, safety_passed=True)
    second = GoldenResult("golden:builder:two", score=0.6, safety_passed=True)

    ledger.record_outcome("builder", "coding", first, source_kind="verifier")
    ledger.record_outcome("builder", "coding", second, source_kind="golden")
    ledger.record_outcome(
        "builder",
        "coding",
        GoldenResult("verifier:builder:one", score=1.0, safety_passed=True),
        source_kind="verifier",
    )
    ledger.record_outcome(
        "planner",
        "coding",
        GoldenResult("verifier:planner:one", score=0.2, safety_passed=True),
        source_kind="verifier",
    )

    assert ledger.score("builder", "coding") == pytest.approx(0.7)
    assert ledger.score("planner", "coding") == pytest.approx(0.2)
    assert ReputationLedger(ProjectSwarmStore(tmp_path)).score(
        "builder", "coding"
    ) == pytest.approx(0.7)


def test_reputation_keeps_verifier_and_golden_results_with_the_same_reference(
    tmp_path: Path,
):
    """Catches source kind being omitted from reputation-result identity."""
    ledger = ReputationLedger(ProjectSwarmStore(tmp_path))

    ledger.record_outcome(
        "builder",
        "coding",
        GoldenResult("shared:reference", score=0.2, safety_passed=True),
        source_kind="verifier",
    )
    ledger.record_outcome(
        "builder",
        "coding",
        GoldenResult("shared:reference", score=0.8, safety_passed=True),
        source_kind="golden",
    )
    ledger.record_outcome(
        "builder",
        "coding",
        GoldenResult("shared:reference", score=1.0, safety_passed=True),
        source_kind="verifier",
    )

    assert [record.source_kind for record in ledger.list("builder", "coding")] == [
        "verifier",
        "golden",
    ]
    assert ledger.score("builder", "coding") == pytest.approx(0.5)


def test_reputation_v2_migration_preserves_legacy_results_and_allows_source_kinds(
    tmp_path: Path,
):
    """Catches v2 identity migration losing Task4 reputation data."""
    database_dir = tmp_path / ".swarm" / "runtime"
    database_dir.mkdir(parents=True)
    database_path = database_dir / "swarm.sqlite"
    created_at = datetime(2026, 7, 27, tzinfo=timezone.utc).isoformat()
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE reputation_results (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                result_id TEXT NOT NULL UNIQUE,
                role TEXT NOT NULL,
                capability TEXT NOT NULL,
                source_kind TEXT NOT NULL,
                source_ref TEXT NOT NULL,
                score REAL NOT NULL CHECK (score >= 0.0 AND score <= 1.0),
                created_at TEXT NOT NULL,
                UNIQUE (role, capability, source_ref)
            )
            """
        )
        connection.execute(
            "INSERT INTO reputation_results VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                1,
                "legacy-result",
                "builder",
                "coding",
                "verifier",
                "shared:legacy",
                0.4,
                created_at,
            ),
        )

    ledger = ReputationLedger(ProjectSwarmStore(tmp_path))
    ledger.record_outcome(
        "builder",
        "coding",
        GoldenResult("shared:legacy", score=0.8, safety_passed=True),
        source_kind="golden",
    )

    assert ledger.score("builder", "coding") == pytest.approx(0.6)
    assert [record.source_kind for record in ledger.list("builder", "coding")] == [
        "verifier",
        "golden",
    ]


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -0.01, 1.01])
def test_learning_rejects_non_finite_or_out_of_range_scores(score: float):
    """Catches malformed verifier or golden scores entering durable reputation."""
    with pytest.raises(ValueError, match="finite"):
        GoldenResult("result:invalid", score=score, safety_passed=True)


def test_golden_assessment_is_pure_and_requires_unique_safe_quality_results():
    """Catches golden assessment needing a transport or accepting duplicate evidence."""
    results = (
        GoldenResult("golden:one", score=0.8, safety_passed=True),
        GoldenResult("golden:two", score=0.6, safety_passed=True),
    )

    assessment = assess_golden_results(results, baseline_quality=0.7)

    assert assessment.quality == pytest.approx(0.7)
    assert assessment.all_safety_passed is True
    assert assessment.eligible_for_promotion is True
    with pytest.raises(ValueError, match="duplicate"):
        assess_golden_results(
            (
                GoldenResult("golden:one", score=0.8, safety_passed=True),
                GoldenResult("golden:one", score=1.0, safety_passed=True),
            ),
            baseline_quality=0.7,
        )


def test_prompt_candidate_needs_golden_eligibility_and_human_approval_to_promote(
    tmp_path: Path,
):
    """Catches candidate prompts activating from scores alone or without a human."""
    candidates = PromptCandidates(ProjectSwarmStore(tmp_path))
    created = candidates.create(
        "safer-builder",
        "Produce a concise, evidence-backed implementation plan.",
        baseline_quality=0.7,
    )

    eligible = candidates.evaluate(
        created.candidate_id,
        (
            GoldenResult("safety:one", score=0.8, safety_passed=True),
            GoldenResult("safety:two", score=0.7, safety_passed=True),
        ),
    )

    assert eligible.status == "eligible"
    with pytest.raises(PermissionError, match="human approval"):
        candidates.promote(created.candidate_id)

    approved = candidates.approve(created.candidate_id, approver_id="owner")

    assert approved.status == "eligible"
    assert candidates.get(created.candidate_id).status == "eligible"
    assert candidates.promote(created.candidate_id).status == "promoted"


def test_prompt_approval_requires_a_current_passing_assessment_and_resets_on_reassessment(
    tmp_path: Path,
):
    """Catches approvals being reusable after a failed or replaced assessment."""
    candidates = PromptCandidates(ProjectSwarmStore(tmp_path))
    candidate = candidates.create(
        "assessment-bound",
        "A candidate with approval bound to a passing assessment.",
        baseline_quality=0.7,
    )

    with pytest.raises(PermissionError, match="eligible"):
        candidates.approve(candidate.candidate_id, approver_id="owner")

    candidates.evaluate(
        candidate.candidate_id,
        (GoldenResult("pass:first", score=0.8, safety_passed=True),),
    )
    approved = candidates.approve(candidate.candidate_id, approver_id="owner")
    assert approved.human_approver_id == "owner"

    failed = candidates.evaluate(
        candidate.candidate_id,
        (GoldenResult("fail:second", score=1.0, safety_passed=False),),
    )
    passed_again = candidates.evaluate(
        candidate.candidate_id,
        (GoldenResult("pass:third", score=0.8, safety_passed=True),),
    )

    assert failed.human_approver_id is None
    assert passed_again.human_approver_id is None
    with pytest.raises(PermissionError, match="human approval"):
        candidates.promote(candidate.candidate_id)
    candidates.approve(candidate.candidate_id, approver_id="owner")
    assert candidates.promote(candidate.candidate_id).status == "promoted"


def test_reassessment_cannot_overwrite_a_concurrently_promoted_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Catches promoted terminal state being overwritten after a stale assessment read."""
    candidates = PromptCandidates(ProjectSwarmStore(tmp_path))
    candidate = candidates.create(
        "terminal-assessment",
        "A candidate that can be promoted before a stale reassessment writes.",
        baseline_quality=0.7,
    )
    candidates.evaluate(
        candidate.candidate_id,
        (GoldenResult("terminal:pass", score=0.8, safety_passed=True),),
    )
    candidates.approve(candidate.candidate_id, approver_id="owner")

    reassessor = ProjectSwarmStore(tmp_path)
    promoter = ProjectSwarmStore(tmp_path)
    original_connection = reassessor._connection
    triggered = False

    class InterceptingCursor:
        def __init__(self, cursor: sqlite3.Cursor) -> None:
            self._cursor = cursor

        def fetchone(self):  # type: ignore[no-untyped-def]
            nonlocal triggered
            row = self._cursor.fetchone()
            # Complete the stale read before the other store commits.  This
            # intentionally models an assessment that made its status decision
            # just before a separate actor promoted the same candidate.
            self._cursor.close()
            if not triggered:
                triggered = True
                _promoted, claimed = promoter.promote_prompt_candidate(
                    candidate.candidate_id
                )
                assert claimed is True
            return row

        def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
            return getattr(self._cursor, name)

    class InterceptingConnection:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self._connection = connection

        def execute(self, sql: str, parameters: tuple[object, ...] = ()):  # type: ignore[no-untyped-def]
            cursor = self._connection.execute(sql, parameters)
            if "SELECT status FROM prompt_candidates" in sql and not triggered:
                return InterceptingCursor(cursor)
            return cursor

        def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
            return getattr(self._connection, name)

    class InterceptingContext:
        def __init__(self, context) -> None:  # type: ignore[no-untyped-def]
            self._context = context

        def __enter__(self) -> InterceptingConnection:
            return InterceptingConnection(self._context.__enter__())

        def __exit__(self, exc_type, exc_value, traceback) -> None:  # type: ignore[no-untyped-def]
            self._context.__exit__(exc_type, exc_value, traceback)

    monkeypatch.setattr(
        reassessor,
        "_connection",
        lambda: InterceptingContext(original_connection()),
    )

    rejection: ValueError | None = None
    try:
        reassessor.record_prompt_assessment(
            candidate.candidate_id,
            quality=0.0,
            safety_passed=False,
            references=("terminal:stale",),
            eligible=False,
            assessment_digest="stale-assessment",
        )
    except ValueError as error:
        rejection = error

    terminal = PromptCandidates(ProjectSwarmStore(tmp_path)).get(candidate.candidate_id)
    assert triggered is True
    assert terminal.status == "promoted"
    assert terminal.human_approver_id == "owner"
    assert terminal.approved_assessment_revision == terminal.assessment_revision
    assert terminal.approved_assessment_digest == terminal.assessment_digest
    assert rejection is not None
    assert "Promoted" in str(rejection)


def test_prompt_assessment_migration_invalidates_unbound_legacy_approval(
    tmp_path: Path,
):
    """Catches a Task4 approval surviving without an assessment revision/digest."""
    database_dir = tmp_path / ".swarm" / "runtime"
    database_dir.mkdir(parents=True)
    database_path = database_dir / "swarm.sqlite"
    created_at = datetime(2026, 7, 27, tzinfo=timezone.utc).isoformat()
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE prompt_candidates (
                candidate_id TEXT PRIMARY KEY,
                prompt_text TEXT NOT NULL,
                baseline_quality REAL NOT NULL,
                status TEXT NOT NULL,
                assessed_quality REAL,
                safety_passed INTEGER,
                assessment_refs_json TEXT,
                human_approver_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO prompt_candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy-candidate",
                "Legacy candidate prompt.",
                0.7,
                "eligible",
                0.8,
                1,
                '["legacy:assessment"]',
                "legacy-owner",
                created_at,
                created_at,
            ),
        )

    candidates = PromptCandidates(ProjectSwarmStore(tmp_path))
    migrated = candidates.get("legacy-candidate")

    assert migrated.human_approver_id is None
    with pytest.raises(PermissionError, match="human approval"):
        candidates.promote(migrated.candidate_id)
    candidates.evaluate(
        migrated.candidate_id,
        (GoldenResult("legacy:replacement", score=0.8, safety_passed=True),),
    )
    candidates.approve(migrated.candidate_id, approver_id="owner")
    assert candidates.promote(migrated.candidate_id).status == "promoted"


def test_concurrent_legacy_store_opens_migrate_columns_without_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Catches two v1 openers racing between PRAGMA and ADD COLUMN."""
    database_dir = tmp_path / ".swarm" / "runtime"
    database_dir.mkdir(parents=True)
    database_path = database_dir / "swarm.sqlite"
    created_at = datetime(2026, 7, 27, tzinfo=timezone.utc).isoformat()
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE memory_items (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL,
                claim_key TEXT NOT NULL,
                statement TEXT NOT NULL,
                source_refs_json TEXT NOT NULL,
                evidence_refs_json TEXT NOT NULL,
                lifecycle TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                revalidated_at TEXT,
                lesson_opt_in INTEGER NOT NULL DEFAULT 0,
                redacted_statement TEXT,
                UNIQUE (kind, claim_key, statement)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE prompt_candidates (
                candidate_id TEXT PRIMARY KEY,
                prompt_text TEXT NOT NULL,
                baseline_quality REAL NOT NULL,
                status TEXT NOT NULL,
                assessed_quality REAL,
                safety_passed INTEGER,
                assessment_refs_json TEXT,
                human_approver_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO memory_items (
                item_id, kind, claim_key, statement, source_refs_json,
                evidence_refs_json, lifecycle, created_at, updated_at,
                revalidated_at, lesson_opt_in, redacted_statement
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "concurrent-memory",
                "fact",
                "concurrent-key",
                "Keep this memory during concurrent migration.",
                "[]",
                '["evidence:concurrent"]',
                "active",
                created_at,
                created_at,
                None,
                0,
                None,
            ),
        )

    original_table_columns = swarm_store_module._table_columns
    race_barrier = threading.Barrier(2)

    def synchronized_table_columns(connection: sqlite3.Connection, table_name: str):
        columns = original_table_columns(connection, table_name)
        if table_name == "memory_items" and "revalidate_after" not in columns:
            try:
                race_barrier.wait(timeout=1)
            except threading.BrokenBarrierError:
                pass
        return columns

    monkeypatch.setattr(
        swarm_store_module,
        "_table_columns",
        synchronized_table_columns,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(ProjectSwarmStore, tmp_path) for _ in range(2)]
        stores = [future.result(timeout=5) for future in futures]

    assert len(stores) == 2
    migrated = ProjectMemory(ProjectSwarmStore(tmp_path)).get("concurrent-memory")
    assert migrated is not None
    assert migrated.evidence_refs == ("evidence:concurrent",)


def test_prompt_candidate_cannot_promote_when_safety_or_quality_fails(tmp_path: Path):
    """Catches human approval bypassing failed golden safety or quality checks."""
    candidates = PromptCandidates(ProjectSwarmStore(tmp_path))
    candidate = candidates.create(
        "unsafe-builder",
        "A candidate that must remain inactive.",
        baseline_quality=0.9,
    )
    assessed = candidates.evaluate(
        candidate.candidate_id,
        (GoldenResult("safety:failed", score=1.0, safety_passed=False),),
    )

    assert assessed.status == "candidate"
    with pytest.raises(PermissionError, match="eligible"):
        candidates.approve(candidate.candidate_id, approver_id="owner")
    with pytest.raises(PermissionError, match="eligible"):
        candidates.promote(candidate.candidate_id)


def test_prompt_candidate_cannot_promote_below_its_stated_quality_baseline(
    tmp_path: Path,
):
    """Catches a passing safety check bypassing the candidate's quality baseline."""
    candidates = PromptCandidates(ProjectSwarmStore(tmp_path))
    candidate = candidates.create(
        "low-quality-builder",
        "A candidate that is safe but below the required quality.",
        baseline_quality=0.9,
    )
    assessed = candidates.evaluate(
        candidate.candidate_id,
        (GoldenResult("quality:below-baseline", score=0.8, safety_passed=True),),
    )

    assert assessed.status == "candidate"
    with pytest.raises(PermissionError, match="eligible"):
        candidates.approve(candidate.candidate_id, approver_id="owner")
    with pytest.raises(PermissionError, match="eligible"):
        candidates.promote(candidate.candidate_id)


def test_lesson_exports_only_opted_in_redacted_statements(tmp_path: Path):
    """Catches cross-project lesson output leaking raw claims or provenance."""
    memory = ProjectMemory(ProjectSwarmStore(tmp_path))
    item = memory.remember(
        "fact",
        "Raw secret appears in C:/private/run-17 and must not leave this project.",
        claim_key="private-lesson",
        source_refs=("C:/private/run-17",),
        evidence_refs=("evidence:raw-secret",),
    )

    with pytest.raises(ValueError, match="differ"):
        memory.mark_lesson_opt_in(item.item_id, item.statement)
    memory.mark_lesson_opt_in(
        item.item_id,
        "Redact project-specific details before sharing a verified lesson.",
    )

    with pytest.raises(PermissionError, match="opt-in"):
        LessonExporter(memory).export()
    with pytest.raises(PermissionError, match="opt-in"):
        LessonExporter(memory).export(opt_in=False)
    exports = LessonExporter(memory).export(opt_in=True)

    assert [(lesson.kind, lesson.statement) for lesson in exports] == [
        ("fact", "Redact project-specific details before sharing a verified lesson.")
    ]
    assert "C:/private/run-17" not in str(exports)
    assert "evidence:raw-secret" not in str(exports)


def test_pack_registry_has_exact_packaged_defaults_and_safe_project_override(
    tmp_path: Path,
):
    """Catches missing shipped packs or project metadata overriding execution policy."""
    default_registry = PackRegistry()
    assert {definition.pack_id for definition in default_registry.list()} == {
        "coding-team",
        "bug-hunt",
        "research-team",
        "release-audit",
    }
    assert resources.files("swarm_core").joinpath("packs", "coding-team.yaml").is_file()

    override_dir = tmp_path / ".swarm" / "packs"
    override_dir.mkdir(parents=True)
    (override_dir / "coding-team.yaml").write_text(
        "description: Local metadata only\n"
        "workflow: local-review-metadata\n"
        "roles:\n"
        "  scout: Local project reconnaissance\n",
        encoding="utf-8",
    )

    overridden = PackRegistry(tmp_path).get("coding-team")

    assert overridden.description == "Local metadata only"
    assert overridden.workflow == "local-review-metadata"
    assert overridden.roles["scout"] == "Local project reconnaissance"
    assert "builder" in overridden.roles


@pytest.mark.parametrize(
    "document",
    [
        "provider: another-provider\n",
        "roles:\n  scout:\n    provider: another-provider\n",
    ],
)
def test_pack_registry_rejects_malformed_or_unsafe_project_yaml(
    tmp_path: Path,
    document: str,
):
    """Catches pack files granting models, providers, tools, or policy authority."""
    override_dir = tmp_path / ".swarm" / "packs"
    override_dir.mkdir(parents=True)
    (override_dir / "coding-team.yaml").write_text(document, encoding="utf-8")

    with pytest.raises(ValueError):
        PackRegistry(tmp_path)


def test_known_non_coding_pack_runs_the_reviewed_workflow(tmp_path: Path):
    """Catches a selectable pack still pausing instead of doing its reviewed work."""

    class PackTransport:
        def __init__(self) -> None:
            self.requests: list[object] = []

        def complete(self, request) -> ModelResponse:
            self.requests.append(request)
            return ModelResponse(
                model=request.model,
                content="reviewed pack work",
                data={
                    "work": f"{request.role} work",
                    "evidence": [f"{request.role}:evidence"],
                    "decision": "continue",
                },
            )

    transport = PackTransport()

    summary = SwarmEngine(transport).run(
        "Investigate the regression.",
        tmp_path,
        pack="bug-hunt",
    )

    assert summary.status == "completed"
    assert summary.pause_reason is None
    assert summary.call_count == 8
    assert len(transport.requests) == 8
    assert all("Pack: bug-hunt" in request.prompt for request in transport.requests)
    assert summary.events[-1].event_type == "run.completed"
