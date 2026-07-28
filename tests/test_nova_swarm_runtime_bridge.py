from __future__ import annotations

import builtins
from contextlib import contextmanager
import copy
from dataclasses import replace
import inspect
import json
import os
from pathlib import Path
import pickle
import sqlite3
import sys
import threading
import time
from types import MappingProxyType

import pytest

from cli.swarm_host import (
    OLLAMA_CLOUD_VERIFIED_CATALOG_SOURCE,
    SidekickSwarmService,
)
import nova.actions as nova_actions
from nova.actions import ActionRegistry
import nova.swarm_adapter as nova_adapter
from nova.swarm_adapter import get_nova_action_spec
import nova.swarm_runtime_bridge as bridge
from nova.swarm_runtime_bridge import (
    NOVA_AUTOMATIC_ACTIONS,
    NovaIntentReadOnlyVerifier,
    NovaIntentSnapshot,
    configure_nova_bridge,
    load_nova_bridge_config,
)
import swarm_core.config as swarm_config
from swarm_core.models import ModelCatalogSnapshot
from swarm_core.store import ProjectSwarmStore
from swarm_core.types import ActionCapabilities
from swarm_core.verifier import (
    InvalidVerifierResult,
    VERIFIED_DECISION,
    VerificationResult,
)


_ROUTED_MODELS = (
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    "kimi-k2.6",
    "minimax-m3",
    "glm-5.2",
    "kimi-k2.7-code",
    "nemotron-3-super",
)
_TEST_WORKER_THREADS: list[threading.Thread] = []
_TEST_WORKER_THREADS_LOCK = threading.Lock()


def _extract_rendered_prompt_context(rendered: str) -> dict[str, object]:
    """Return the JSON context bounded by the prompt's required delimiters."""
    _prefix, context_delimiter, after_context = rendered.partition("\n\nContext:\n")
    assert context_delimiter == "\n\nContext:\n"
    context_json, output_delimiter, _output_contract = after_context.partition(
        "\n\nOutput contract:\n"
    )
    assert output_delimiter == "\n\nOutput contract:\n"
    context = json.loads(context_json)
    assert isinstance(context, dict)
    return context


def _capture_test_worker() -> threading.Thread:
    worker = threading.current_thread()
    with _TEST_WORKER_THREADS_LOCK:
        _TEST_WORKER_THREADS.append(worker)
    return worker


@pytest.fixture(autouse=True)
def _join_workers_and_restore_runtime_bindings():
    """Keep daemon workers and process-only capabilities inside each test."""
    with _TEST_WORKER_THREADS_LOCK:
        worker_start = len(_TEST_WORKER_THREADS)
    with bridge._RUNTIME_BINDINGS_LOCK:
        bindings_before = dict(bridge._RUNTIME_BINDINGS)
    yield
    with _TEST_WORKER_THREADS_LOCK:
        workers = list(_TEST_WORKER_THREADS[worker_start:])
    for worker in workers:
        worker.join(timeout=2)
        assert not worker.is_alive(), f"Task 8 worker leaked: {worker.name}"
    with _TEST_WORKER_THREADS_LOCK:
        del _TEST_WORKER_THREADS[worker_start:]
    with bridge._RUNTIME_BINDINGS_LOCK:
        for root in set(bridge._RUNTIME_BINDINGS) - set(bindings_before):
            del bridge._RUNTIME_BINDINGS[root]
        assert all(
            bridge._RUNTIME_BINDINGS.get(root) is binding
            for root, binding in bindings_before.items()
        )


class _FakeNovaPolicy:
    _TIERS = {
        "agenda_update": "silent",
        "blog_draft": "external",
        "mind_diary": "internal",
        "prioritize_thread": "silent",
    }

    def __init__(self) -> None:
        self.tiers = dict(self._TIERS)

    def action_tier(self, action: str) -> str:
        return self.tiers[action]


class _FakeNovaActionRecorder:
    def __init__(self, project_root: Path, timeline: list[str]) -> None:
        self.space_dir = project_root
        self.calls: list[dict[str, object]] = []
        self._timeline = timeline
        self.crash_after_recording = False

    def execute(self, decision: dict[str, object]) -> dict[str, object]:
        self._timeline.append("act")
        self.calls.append(copy.deepcopy(decision))
        if self.crash_after_recording:
            raise SystemExit("fake post-claim action crash")
        return {"executed": True, "status": "done"}


class _FakeNovaKernel:
    def __init__(self, project_root: Path, *, yolo: bool = False) -> None:
        self.space_dir = project_root
        self.timeline: list[str] = []
        self.actions = _FakeNovaActionRecorder(project_root, self.timeline)
        self.policy = _FakeNovaPolicy()
        self.yolo = yolo
        self.yolo_checks = 0
        self.govern_mode = "allow"
        self.root_mismatch_target: Path | None = None
        self.govern_calls: list[dict[str, object]] = []
        self.policy_claimed_before_govern: list[bool] = []

    def is_yolo_enabled(self) -> bool:
        self.yolo_checks += 1
        return self.yolo

    def govern(self, intent: dict[str, object]) -> dict[str, object]:
        self.timeline.append("govern")
        self.govern_calls.append(copy.deepcopy(intent))
        with sqlite3.connect(
            self.space_dir / ".swarm" / "runtime" / "swarm.sqlite"
        ) as connection:
            claimed = bool(
                connection.execute(
                    "SELECT COUNT(*) FROM action_executions WHERE proposal_id = ?",
                    (intent["id"],),
                ).fetchone()[0]
            )
        self.policy_claimed_before_govern.append(claimed)
        if self.govern_mode == "crash":
            raise RuntimeError("fake post-claim crash")
        if self.root_mismatch_target is not None:
            self.space_dir = self.root_mismatch_target
        allowed = self.govern_mode != "deny"
        return {
            "intent": copy.deepcopy(intent),
            "policy": {
                "allowed": allowed,
                "reason": None if allowed else "nova_policy_blocked",
                "tier": intent["tier"],
            },
            "state": {},
            "autonomy": {},
        }

    def act(self, decision: dict[str, object]) -> dict[str, object]:
        return self.actions.execute(decision)


class _FakeBridgeHost:
    """A deterministic Sidekick host with no process, network, or live Nova access."""

    def __init__(
        self,
        project_root: Path,
        *,
        yolo: bool = False,
        review_denied_model: str | None = None,
        provider_unavailable: bool = False,
    ) -> None:
        self.project_root = project_root.resolve()
        self.kernel = _FakeNovaKernel(self.project_root, yolo=yolo)
        self.context = bridge._create_nova_bridge_context(
            self.project_root,
            validator=lambda candidate: candidate,
        )
        self.review_denied_model = review_denied_model
        self.provider_unavailable = provider_unavailable
        self.model_calls: list[dict[str, object]] = []
        self.provider_slots: list[tuple[str, str]] = []
        self.worker_run_ids: list[str] = []
        self.worker_threads: dict[str, threading.Thread] = {}
        self.summaries: dict[str, object] = {}
        self.worker_errors: dict[str, BaseException] = {}
        self.options_mutator = None
        self.after_options_resolved = None
        self._condition = threading.Condition()
        ProjectSwarmStore(self.project_root).save_model_catalog_snapshot(
            ModelCatalogSnapshot(
                provider="ollama-cloud",
                models=_ROUTED_MODELS,
                healthy=True,
                source=OLLAMA_CLOUD_VERIFIED_CATALOG_SOURCE,
            )
        )
        self.service = SidekickSwarmService(
            call_llm=self._call_llm,
            catalog_refresher=self._unexpected_catalog_refresh,
            provider_slot=self._provider_slot,
            execution_options_resolver=self._resolve_options,
            pause_poll_seconds=0.001,
        )

    def bridge(self, *, dispatcher=None) -> bridge.NovaSwarmRuntimeBridge:
        return bridge.NovaSwarmRuntimeBridge(
            self.kernel,
            project_root=self.project_root,
            trusted_project_root=self.context,
            dispatcher=dispatcher or self.dispatch,
        )

    def dispatch(self, project_root: Path, run_id: str) -> None:
        worker = _capture_test_worker()
        with self._condition:
            self.worker_run_ids.append(run_id)
            self.worker_threads[run_id] = worker
            self._condition.notify_all()
        try:
            summary = self.service.execute_run(project_root, run_id)
        except BaseException as exc:
            with self._condition:
                self.worker_errors[run_id] = exc
                self._condition.notify_all()
            raise
        with self._condition:
            self.summaries[run_id] = summary
            self._condition.notify_all()

    def wait_for_worker(self, run_id: str, *, timeout: float = 3.0):
        deadline = time.monotonic() + timeout
        with self._condition:
            while (
                run_id not in self.summaries
                and run_id not in self.worker_errors
                and time.monotonic() < deadline
            ):
                self._condition.wait(deadline - time.monotonic())
            if run_id in self.worker_errors:
                raise AssertionError("fake bridge worker crashed") from self.worker_errors[
                    run_id
                ]
            if run_id not in self.summaries:
                raise AssertionError("fake bridge worker did not finish")
            return self.summaries[run_id]

    def wait_for_dispatch(self, *, count: int = 1, timeout: float = 1.0) -> None:
        deadline = time.monotonic() + timeout
        with self._condition:
            while len(self.worker_run_ids) < count and time.monotonic() < deadline:
                self._condition.wait(deadline - time.monotonic())
            if len(self.worker_run_ids) < count:
                raise AssertionError("fake bridge worker was not dispatched")

    def join_worker(self, run_id: str, *, timeout: float = 2.0) -> None:
        self.wait_for_dispatch()
        with self._condition:
            worker = self.worker_threads[run_id]
        worker.join(timeout=timeout)
        assert not worker.is_alive(), f"fake bridge worker did not stop: {run_id}"

    def _resolve_options(self, project_root: Path, run):
        options = bridge.nova_execution_options_for_run(project_root, run)
        assert options is not None
        if self.options_mutator is not None:
            options = self.options_mutator(options, run)
        if self.after_options_resolved is not None:
            callback = self.after_options_resolved
            self.after_options_resolved = None
            callback(run)
        return options

    def _call_llm(self, **kwargs):
        self.model_calls.append(dict(kwargs))
        if self.provider_unavailable:
            raise ConnectionError("fake Ollama Cloud unavailable")
        model = kwargs["model"]
        approved = model != self.review_denied_model
        content = json.dumps(
            {
                "work": f"{model} completed deterministic fake work",
                "evidence": [f"fake-cloud:{model}"],
                "decision": "approved" if approved else "denied",
                "approved": approved,
            }
        )
        return {"choices": [{"message": {"content": content}}]}

    @contextmanager
    def _provider_slot(self, run_id: str, provider: str):
        self.provider_slots.append((run_id, provider))
        yield

    @staticmethod
    def _unexpected_catalog_refresh():
        raise AssertionError("durable fake catalog must not refresh")


class _FixedVerifier:
    def __init__(self, *, decision: str, evidence: str) -> None:
        self.decision = decision
        self.evidence = evidence

    def verify(self, _request) -> VerificationResult:
        return VerificationResult(
            work="Deterministic fake verifier outcome.",
            evidence=(self.evidence,),
            decision=self.decision,
            provenance={"adapter": "task-8-fake", "mode": "read_only"},
        )


def _configure_enabled_fake_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    **kwargs,
) -> _FakeBridgeHost:
    project = tmp_path / "spaces" / "nova"
    project.mkdir(parents=True)
    monkeypatch.setenv("OLLAMA_BASE_URL", "https://ollama.com/v1")
    configure_nova_bridge(project, enabled=True)
    return _FakeBridgeHost(project, **kwargs)


def _pause_reason(project_root: Path, run_id: str) -> str | None:
    paused = [
        event
        for event in ProjectSwarmStore.open_read_only(project_root).list_events(run_id)
        if event.event_type == "run.paused"
    ]
    return paused[-1].payload["reason"] if paused else None


def _tamper_run_metadata(
    project_root: Path,
    run_id: str,
    mutator,
) -> dict[str, object]:
    database = project_root / ".swarm" / "runtime" / "swarm.sqlite"
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT metadata_json FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        assert row is not None
        metadata = json.loads(row[0])
        mutator(metadata)
        connection.execute(
            "UPDATE runs SET metadata_json = ? WHERE run_id = ?",
            (json.dumps(metadata, sort_keys=True), run_id),
        )
    return metadata


def _admit_paused_without_model_calls(
    host: _FakeBridgeHost,
    *,
    source_slot: int,
):
    finished = threading.Event()
    worker_threads: list[threading.Thread] = []

    def pause_dispatch(project_root: Path, run_id: str) -> None:
        worker_threads.append(_capture_test_worker())
        ProjectSwarmStore(project_root).set_run_status(run_id, "paused")
        finished.set()

    result = host.bridge(dispatcher=pause_dispatch).submit(
        _diary_suggestion(), source_slot=source_slot
    )
    assert result.status == "created"
    assert result.run_id is not None
    assert finished.wait(timeout=1)
    assert worker_threads
    worker_threads[0].join(timeout=1)
    assert not worker_threads[0].is_alive()
    durable = ProjectSwarmStore.open_read_only(host.project_root).get_run(
        result.run_id
    )
    assert durable is not None and durable.status == "paused"
    return durable


def _diary_suggestion() -> dict[str, object]:
    return {
        "id": "caller-controlled-id",
        "proposal_id": "caller-controlled-proposal",
        "action": "mind_diary",
        "need": "continuity",
        "title": "Keep a local diary note",
        "why": "A local reflection preserves useful context.",
        "target": {},
        "payload": {"content": "Draft a concise reflection."},
        "expected_outcome": {"effect": "diary_entry_persisted"},
        "priority": 0.8,
        "tier": "silent",
        "policy_tier": "silent",
        "capabilities": {"external": False},
        "evidence_refs": ["builder:untrusted"],
    }


@pytest.fixture
def nova_project(tmp_path: Path) -> Path:
    project = tmp_path / "spaces" / "nova"
    project.mkdir(parents=True)
    return project


@pytest.fixture
def trusted_nova_project(nova_project: Path):
    return bridge._create_nova_bridge_context(
        nova_project,
        validator=lambda candidate: candidate,
    )


def test_snapshot_identity_is_stable_for_one_decision_slot(trusted_nova_project):
    """Catches caller-owned identity replacing a canonical decision identity."""
    first = NovaIntentSnapshot.from_submission(
        _diary_suggestion(), source_slot=1234, project_root=trusted_nova_project
    )
    second = NovaIntentSnapshot.from_submission(
        _diary_suggestion(), source_slot=1234, project_root=trusted_nova_project
    )

    assert first.intent_digest == second.intent_digest
    assert first.proposal_id == second.proposal_id
    assert first.verifier_evidence_ref == f"nova:verifier:{first.intent_digest}"
    assert first.to_suggestion(trusted_nova_project)["evidence_refs"] == (
        first.verifier_evidence_ref,
    )


def test_snapshot_slot_change_changes_the_canonical_digest(trusted_nova_project):
    """Catches distinct decision slots collapsing onto one approval identity."""
    first = NovaIntentSnapshot.from_submission(
        _diary_suggestion(), source_slot=1234, project_root=trusted_nova_project
    )
    second = NovaIntentSnapshot.from_submission(
        _diary_suggestion(), source_slot=1235, project_root=trusted_nova_project
    )

    assert first.intent_digest != second.intent_digest
    assert first.proposal_id != second.proposal_id


@pytest.mark.parametrize(
    "action", ["reflection", "aces", "moltbook", "blog_draft", "unknown_action"]
)
def test_snapshot_rejects_non_automatic_actions_before_any_runtime_path(
    trusted_nova_project, action: str
):
    """Catches a reflective, ACES, social, blog, or unknown action reaching Nova."""
    with pytest.raises(ValueError, match="automatic Nova action"):
        NovaIntentSnapshot.from_submission(
            _diary_suggestion() | {"action": action},
            source_slot=1,
            project_root=trusted_nova_project,
        )


def test_snapshot_discards_caller_controlled_identity_and_security_fields(
    trusted_nova_project,
):
    """Catches untrusted proposal metadata weakening canonical bridge output."""
    snapshot = NovaIntentSnapshot.from_submission(
        _diary_suggestion(), source_slot=9, project_root=trusted_nova_project
    )
    suggestion = snapshot.to_suggestion(trusted_nova_project)

    assert suggestion["id"] == snapshot.proposal_id
    assert suggestion["intent_id"] == snapshot.proposal_id
    assert suggestion["proposal_id"] == snapshot.proposal_id
    assert suggestion["evidence_refs"] == (snapshot.verifier_evidence_ref,)
    assert "tier" not in suggestion
    assert "policy_tier" not in suggestion
    assert "capabilities" not in suggestion
    assert "caller-controlled-id" not in repr(suggestion)
    assert "builder:untrusted" not in repr(suggestion)


def test_verifier_returns_exactly_its_snapshot_evidence(trusted_nova_project):
    """Catches the verifier copying Builder/Critic evidence into a positive result."""
    snapshot = NovaIntentSnapshot.from_submission(
        _diary_suggestion(), source_slot=17, project_root=trusted_nova_project
    )

    result = NovaIntentReadOnlyVerifier(trusted_nova_project).verify_snapshot(snapshot)

    assert result.decision == VERIFIED_DECISION
    assert result.evidence == (snapshot.verifier_evidence_ref,)
    assert result.provenance["mode"] == "read_only"


@pytest.mark.parametrize(
    "mutator",
    [
        lambda snapshot, root: replace(snapshot, project_root=root / "other"),
        lambda snapshot, _root: replace(snapshot, intent_digest="0" * 64),
        lambda snapshot, _root: replace(
            snapshot,
            expected_outcome={"output_scope": "outside/the-approved-scope.json"},
        ),
        lambda snapshot, _root: replace(snapshot, payload={"command": "write"}),
        lambda snapshot, _root: replace(snapshot, payload={"secret": "token"}),
        lambda snapshot, _root: replace(snapshot, payload={"url": "https://example.test"}),
        lambda snapshot, _root: replace(snapshot, payload={"apply": True}),
        lambda snapshot, _root: replace(snapshot, priority=10**1000),
    ],
)
def test_verifier_rejects_tampered_or_sensitive_snapshots_without_side_effects(
    nova_project: Path, trusted_nova_project, mutator
):
    """Catches verifier acceptance of a root escape, tamper, output escape, or effect marker."""
    marker = nova_project / "must-not-change.txt"
    marker.write_text("unchanged", encoding="utf-8")
    snapshot = NovaIntentSnapshot.from_submission(
        _diary_suggestion(), source_slot=22, project_root=trusted_nova_project
    )

    with pytest.raises(InvalidVerifierResult):
        NovaIntentReadOnlyVerifier(trusted_nova_project).verify_snapshot(
            mutator(snapshot, nova_project)
        )

    assert marker.read_text(encoding="utf-8") == "unchanged"


def test_bridge_config_is_explicit_and_reading_absent_config_creates_nothing(
    tmp_path: Path,
):
    """Catches a read-only bridge check initializing a Swarm project or hidden override."""
    project = tmp_path / "new-project"

    assert load_nova_bridge_config(project).enabled is False
    assert not project.exists()

    configured = configure_nova_bridge(project, enabled=True)
    assert configured.enabled is True
    assert load_nova_bridge_config(project).enabled is True
    assert NOVA_AUTOMATIC_ACTIONS == (
        "mind_diary",
        "agenda_update",
        "prioritize_thread",
    )


@pytest.mark.parametrize(
    ("action", "target", "payload"),
    [
        ("mind_diary", {}, {"content": "A concise local note."}),
        ("agenda_update", {}, {}),
        (
            "prioritize_thread",
            {"thread_id": "release", "topic": "release"},
            {"next_step": "Review the local release notes."},
        ),
    ],
)
def test_action_specs_and_verifier_scope_match_the_real_action_handler(
    nova_project: Path,
    trusted_nova_project,
    action: str,
    target: dict[str, str],
    payload: dict[str, str],
):
    """Catches a bridge scope drifting from the file the Nova handler writes."""
    actual = ActionRegistry(nova_project).execute(
        {
            "id": "scope-check",
            "action": action,
            "need": "continuity",
            "why": "Check the handler's local output path.",
            "target": target,
            "payload": payload,
        },
        {},
    )
    snapshot = NovaIntentSnapshot.from_submission(
        _diary_suggestion()
        | {"action": action, "target": target, "payload": payload},
        source_slot=33,
        project_root=trusted_nova_project,
    )

    actual_scope = Path(actual["effects"]["path"]).resolve().relative_to(
        nova_project.resolve()
    ).as_posix()
    assert get_nova_action_spec(action).output_scope == actual_scope
    assert snapshot.expected_output_scope == actual_scope


@pytest.mark.parametrize(
    ("action", "target", "payload"),
    [
        ("mind_diary", {}, {"content": "Keep this inside the trusted root."}),
        ("agenda_update", {}, {}),
        (
            "prioritize_thread",
            {"thread_id": "release", "topic": "release"},
            {"next_step": "Keep this inside the trusted root."},
        ),
    ],
)
def test_automatic_action_rejects_a_link_at_its_exact_output_path(
    nova_project: Path,
    action: str,
    target: dict[str, str],
    payload: dict[str, str],
):
    """Every automatic output rejects a final link, even when replacement looks safe."""
    scope = Path(get_nova_action_spec(action).output_scope)
    output = nova_project / scope
    output.parent.mkdir(parents=True, exist_ok=True)
    outside = nova_project.parent / f"outside-{action}.json"
    sentinel = '{"sentinel": "unchanged"}\n'
    outside.write_text(sentinel, encoding="utf-8")
    try:
        output.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"file symlinks are unavailable: {exc}")

    result = ActionRegistry(nova_project).execute(
        {
            "id": f"linked-{action}",
            "action": action,
            "need": "continuity",
            "why": "Exercise the automatic output boundary.",
            "target": target,
            "payload": payload,
        },
        {},
    )

    assert result["ok"] is False
    assert outside.read_text(encoding="utf-8") == sentinel
    assert output.is_symlink()


def test_automatic_action_rejects_a_link_in_its_parent_chain(
    nova_project: Path,
):
    """An existing linked output directory must never be treated as trusted."""
    nova_data = nova_project / "nova_data"
    nova_data.mkdir()
    outside = nova_project.parent / "outside-entity"
    outside.mkdir()
    sentinel = outside / "mind_diary.jsonl"
    sentinel.write_text("unchanged\n", encoding="utf-8")
    entity = nova_data / "entity"
    try:
        entity.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")

    result = ActionRegistry(nova_project).execute(
        {
            "id": "linked-parent",
            "action": "mind_diary",
            "need": "continuity",
            "why": "Exercise the automatic parent boundary.",
            "target": {},
            "payload": {"content": "Do not escape."},
        },
        {},
    )

    assert result["ok"] is False
    assert sentinel.read_text(encoding="utf-8") == "unchanged\n"


@pytest.mark.parametrize(
    ("action", "target", "payload"),
    [
        ("mind_diary", {}, {"content": "Append only to the local inode."}),
        ("agenda_update", {}, {}),
        (
            "prioritize_thread",
            {"thread_id": "release", "topic": "release"},
            {"next_step": "Update only the local inode."},
        ),
    ],
)
def test_automatic_action_replaces_a_hardlinked_output_without_mutating_outside(
    nova_project: Path,
    action: str,
    target: dict[str, str],
    payload: dict[str, str],
):
    """A regular-looking hard link must not expose its other name to writes."""
    output = nova_project / get_nova_action_spec(action).output_scope
    output.parent.mkdir(parents=True, exist_ok=True)
    outside = nova_project.parent / f"outside-hardlink-{action}.json"
    sentinel = '{"sentinel": "unchanged"}\n'
    outside.write_text(sentinel, encoding="utf-8")
    try:
        os.link(outside, output)
    except OSError as exc:
        pytest.skip(f"hard links are unavailable: {exc}")

    result = ActionRegistry(nova_project).execute(
        {
            "id": f"hardlinked-{action}",
            "action": action,
            "need": "continuity",
            "why": "Exercise the automatic hard-link boundary.",
            "target": target,
            "payload": payload,
        },
        {},
    )

    assert result["ok"] is True
    assert outside.read_text(encoding="utf-8") == sentinel
    assert not output.samefile(outside)


def test_automatic_action_pins_parent_components_against_a_swap(
    nova_project: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """A deterministic parent swap is either prevented or detected before success."""
    entity = nova_project / "nova_data" / "entity"
    entity.mkdir(parents=True)
    preserved = entity.with_name("entity-preserved")
    outside = nova_project.parent / "outside-swapped-entity"
    outside.mkdir()
    sentinel = outside / "mind_diary.jsonl"
    sentinel.write_text("unchanged\n", encoding="utf-8")
    original_open = swarm_config._open_regular_file
    swap_state = {"swapped": False, "blocked": False}

    def swap_before_open(
        directory,
        filename,
        *,
        read_only,
        create,
    ):
        if filename == "mind_diary.jsonl" and not any(swap_state.values()):
            try:
                entity.rename(preserved)
                entity.symlink_to(outside, target_is_directory=True)
                swap_state["swapped"] = True
            except (OSError, PermissionError):
                swap_state["blocked"] = True
        return original_open(
            directory,
            filename,
            read_only=read_only,
            create=create,
        )

    monkeypatch.setattr(swarm_config, "_open_regular_file", swap_before_open)
    result = ActionRegistry(nova_project).execute(
        {
            "id": "parent-swap",
            "action": "mind_diary",
            "need": "continuity",
            "why": "Exercise the automatic swap boundary.",
            "target": {},
            "payload": {"content": "Do not escape."},
        },
        {},
    )

    assert swap_state["swapped"] or swap_state["blocked"]
    if swap_state["swapped"]:
        assert result["ok"] is False
    assert sentinel.read_text(encoding="utf-8") == "unchanged\n"


def test_automatic_action_rejects_a_swapped_atomic_replace_source(
    nova_project: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """The final name must still be the exact fresh regular inode that was written."""
    outside = nova_project.parent / "outside-replace-source.json"
    sentinel = '{"sentinel": "unchanged"}\n'
    outside.write_text(sentinel, encoding="utf-8")
    original_replace = swarm_config._replace_direct_child
    swapped = False

    def swap_replace_source(directory, source, destination):
        nonlocal swapped
        if destination == "agenda_maintenance.json" and not swapped:
            preserved = f"{source}.preserved"
            if directory.posix_fd is not None:
                os.replace(
                    source,
                    preserved,
                    src_dir_fd=directory.posix_fd,
                    dst_dir_fd=directory.posix_fd,
                )
                os.symlink(outside, source, dir_fd=directory.posix_fd)
            else:
                source_path = directory.path / source
                source_path.rename(directory.path / preserved)
                source_path.symlink_to(outside)
            swapped = True
        return original_replace(directory, source, destination)

    monkeypatch.setattr(
        swarm_config,
        "_replace_direct_child",
        swap_replace_source,
    )
    result = ActionRegistry(nova_project).execute(
        {
            "id": "replace-source-swap",
            "action": "agenda_update",
            "need": "continuity",
            "why": "Exercise the final atomic replacement boundary.",
            "target": {},
            "payload": {},
        },
        {},
    )

    output = nova_project / get_nova_action_spec("agenda_update").output_scope
    assert swapped is True
    assert result["ok"] is False
    assert outside.read_text(encoding="utf-8") == sentinel
    assert not output.exists() and not output.is_symlink()


def test_automatic_action_rechecks_the_final_name_after_content_validation(
    nova_project: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """A POSIX final-name swap during read is detected; Windows blocks the swap."""
    output = nova_project / get_nova_action_spec("agenda_update").output_scope
    preserved = output.with_name("agenda_maintenance-preserved.json")
    outside = nova_project.parent / "outside-final-name-swap.json"
    sentinel = '{"sentinel": "unchanged"}\n'
    outside.write_text(sentinel, encoding="utf-8")
    original_read = nova_actions._read_text_descriptor
    swap_state = {"swapped": False, "blocked": False}

    def swap_after_read(descriptor):
        document = original_read(descriptor)
        if output.exists() and not any(swap_state.values()):
            try:
                output.rename(preserved)
                output.symlink_to(outside)
                swap_state["swapped"] = True
            except (OSError, PermissionError):
                swap_state["blocked"] = True
        return document

    monkeypatch.setattr(
        nova_actions,
        "_read_text_descriptor",
        swap_after_read,
    )
    result = ActionRegistry(nova_project).execute(
        {
            "id": "final-name-swap",
            "action": "agenda_update",
            "need": "continuity",
            "why": "Exercise the post-read final-name boundary.",
            "target": {},
            "payload": {},
        },
        {},
    )

    assert swap_state["swapped"] or swap_state["blocked"]
    if swap_state["swapped"]:
        assert result["ok"] is False
        assert not output.exists() and not output.is_symlink()
    else:
        assert result["ok"] is True
    assert outside.read_text(encoding="utf-8") == sentinel


@pytest.mark.parametrize(
    "submission_change",
    [
        {"payload": {"c\uff4fmm\uff41nd": "write"}},
        {"payload": {"content": "Y29tbWFuZA=="}},
        {"payload": {"content": "Y29tbWFuZA"}},
        {"payload": {"content": "base64url:Y29tbWFuZA"}},
        {"payload": {"content": "_2NvbW1hbmQ"}},
        {"payload": {"content": "YXBwbHk="}},
        {"payload": {"content": "c2VjcmV0"}},
        {"payload": {"content": "dXJs"}},
        {"payload": {"content": "LmVudg=="}},
        {"target": {"nested": {"%63ommand": "write"}}},
    ],
)
def test_snapshot_rejects_normalized_or_encoded_control_material(
    trusted_nova_project, submission_change: dict[str, object]
):
    """Catches Unicode, percent, or opaque-encoded fields bypassing action schemas."""
    with pytest.raises(ValueError):
        NovaIntentSnapshot.from_submission(
            _diary_suggestion() | submission_change,
            source_slot=34,
            project_root=trusted_nova_project,
        )


def test_snapshot_requires_a_real_trusted_project_root(tmp_path: Path):
    """Catches a caller selecting a nonexistent or Windows system root as Nova space."""
    with pytest.raises(ValueError):
        bridge._create_nova_bridge_context(
            tmp_path / "does-not-exist",
            validator=lambda candidate: candidate,
        )
    with pytest.raises(ValueError):
        bridge._create_nova_bridge_context(
            Path(os.environ["SystemRoot"]),
            validator=lambda _candidate: (_ for _ in ()).throw(ValueError("blocked")),
        )
    with pytest.raises(TypeError):
        NovaIntentReadOnlyVerifier(Path(os.environ["SystemRoot"]))


def test_canonical_unicode_and_numeric_forms_have_one_identity(trusted_nova_project):
    """Catches visually equal Unicode or numeric zero/one forms creating new intents."""
    decomposed = _diary_suggestion() | {"title": "Cafe\u0301", "priority": 1}
    composed = _diary_suggestion() | {"title": "Caf\u00e9", "priority": 1.0}
    negative_zero = _diary_suggestion() | {"priority": -0.0}
    positive_zero = _diary_suggestion() | {"priority": 0}

    assert NovaIntentSnapshot.from_submission(
        decomposed, source_slot=36, project_root=trusted_nova_project
    ).intent_digest == NovaIntentSnapshot.from_submission(
        composed, source_slot=36, project_root=trusted_nova_project
    ).intent_digest
    assert NovaIntentSnapshot.from_submission(
        negative_zero, source_slot=37, project_root=trusted_nova_project
    ).intent_digest == NovaIntentSnapshot.from_submission(
        positive_zero, source_slot=37, project_root=trusted_nova_project
    ).intent_digest


def test_huge_priority_is_rejected_without_an_overflow_crash(trusted_nova_project):
    """Catches an unbounded integer causing float conversion to escape validation."""
    with pytest.raises(ValueError):
        NovaIntentSnapshot.from_submission(
            _diary_suggestion() | {"priority": 10**1000},
            source_slot=38,
            project_root=trusted_nova_project,
        )


def test_read_only_bridge_requires_injected_trusted_root_without_web_imports(
    monkeypatch: pytest.MonkeyPatch, nova_project: Path, tmp_path: Path
):
    """Catches a read-only snapshot importing WebUI config code or choosing its root."""
    created_before = {entry.name for entry in tmp_path.iterdir()}
    workspace_module_before = sys.modules.get("web.api.workspace")
    original_import = builtins.__import__

    def fail_web_api_import(name, *args, **kwargs):
        if name == "web.api.workspace" or name.startswith("web.api.workspace."):
            raise AssertionError("read-only bridge must not import WebUI workspace code")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_web_api_import)
    trusted = bridge._create_nova_bridge_context(
        nova_project,
        validator=lambda candidate: candidate,
    )
    snapshot = NovaIntentSnapshot.from_submission(
        _diary_suggestion(), source_slot=39, project_root=trusted
    )
    result = NovaIntentReadOnlyVerifier(trusted).verify_snapshot(snapshot)

    assert result.decision == VERIFIED_DECISION
    assert {entry.name for entry in tmp_path.iterdir()} == created_before
    assert sys.modules.get("web.api.workspace") is workspace_module_before
    with pytest.raises(TypeError):
        NovaIntentSnapshot.from_submission(
            _diary_suggestion(), source_slot=39, project_root=nova_project
        )


def test_prioritize_thread_requires_a_nonblank_canonical_target(
    trusted_nova_project,
):
    """Catches a whitespace-only thread target becoming a durable prioritized action."""
    with pytest.raises(ValueError):
        NovaIntentSnapshot.from_submission(
            _diary_suggestion()
            | {
                "action": "prioritize_thread",
                "target": {"thread_id": " \t "},
                "payload": {},
            },
            source_slot=40,
            project_root=trusted_nova_project,
        )

    valid = NovaIntentSnapshot.from_submission(
        _diary_suggestion()
        | {
            "action": "prioritize_thread",
            "target": {"thread_id": "release"},
            "payload": {},
        },
        source_slot=41,
        project_root=trusted_nova_project,
    )
    with pytest.raises(InvalidVerifierResult):
        NovaIntentReadOnlyVerifier(trusted_nova_project).verify_snapshot(
            replace(valid, target={"thread_id": "  "})
        )


def test_public_inputs_cannot_mint_or_retarget_a_nova_trusted_root(
    nova_project: Path,
    trusted_nova_project,
):
    """Catches a request-supplied resolver or path minting a positive verifier context."""
    assert not hasattr(bridge, "create_trusted_nova_project_root")
    with pytest.raises(TypeError):
        NovaIntentSnapshot.from_submission(
            _diary_suggestion(), source_slot=42, project_root=nova_project
        )
    with pytest.raises(TypeError):
        NovaIntentReadOnlyVerifier(nova_project)
    assert not hasattr(trusted_nova_project, "__dict__")
    assert "__repr__" not in type(trusted_nova_project).__dict__
    assert "__eq__" not in type(trusted_nova_project).__dict__
    with pytest.raises(TypeError):
        pickle.dumps(trusted_nova_project)


def test_verified_snapshot_fails_closed_after_attempted_object_mutation(
    trusted_nova_project,
):
    """Catches post-verifier payload replacement emitting old evidence for new data."""
    snapshot = NovaIntentSnapshot.from_submission(
        _diary_suggestion(), source_slot=43, project_root=trusted_nova_project
    )
    verifier = NovaIntentReadOnlyVerifier(trusted_nova_project)
    assert verifier.verify_snapshot(snapshot).decision == VERIFIED_DECISION
    assert not hasattr(snapshot, "__dict__")
    suggestion = snapshot.to_suggestion(trusted_nova_project)
    with pytest.raises(TypeError):
        suggestion["payload"]["content"] = "replaced"
    with pytest.raises(TypeError):
        snapshot.payload["content"] = "replaced"
    object.__setattr__(snapshot, "payload", {"content": "replaced"})

    with pytest.raises(ValueError):
        snapshot.to_suggestion(trusted_nova_project)


def test_base64_control_detection_keeps_ordinary_text_usable(trusted_nova_project):
    """Catches broad base64 heuristics rejecting plain human text."""
    for content in ("finalization", "Re-run"):
        snapshot = NovaIntentSnapshot.from_submission(
            _diary_suggestion() | {"payload": {"content": content}},
            source_slot=44,
            project_root=trusted_nova_project,
        )
        assert snapshot.to_suggestion(trusted_nova_project)["payload"]["content"] == content


def test_submit_nova_intent_derives_host_context_and_delegates_through_bridge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Catches the public seam bypassing the bridge or accepting caller-owned trust."""
    project = tmp_path / "spaces" / "nova"
    project.mkdir(parents=True)

    class Kernel:
        def __init__(self) -> None:
            self.space_dir = project
            self.actions = ActionRegistry(project)

        def govern(self, _intent):
            pytest.fail("the public entry must not call govern")

        def act(self, _decision):
            pytest.fail("the public entry must not call act")

    kernel = Kernel()
    captured: dict[str, object] = {}
    original_import = builtins.__import__

    def forbid_host_surface_import(name, *args, **kwargs):
        if name == "config" or name.startswith(("web.", "cli.config")):
            pytest.fail(f"public entry imported a host config surface: {name}")
        return original_import(name, *args, **kwargs)

    class RecordingBridge:
        def __init__(
            self,
            supplied_kernel,
            *,
            project_root,
            trusted_project_root,
        ) -> None:
            captured["kernel"] = supplied_kernel
            captured["project_root"] = project_root
            captured["context"] = trusted_project_root

        def submit(self, suggestion, *, source_slot):
            captured["suggestion"] = suggestion
            captured["source_slot"] = source_slot
            captured["trusted_root"] = bridge._trusted_project_root(
                captured["context"]
            )
            return bridge.NovaBridgeResult(
                "created",
                run_id="run-123",
                reason="SECRET raw admission policy text",
            )

    monkeypatch.setattr(bridge, "NovaSwarmRuntimeBridge", RecordingBridge)
    monkeypatch.setattr(builtins, "__import__", forbid_host_surface_import)
    proposal = _diary_suggestion()

    result = bridge.submit_nova_intent(kernel, proposal, source_slot=123)

    assert captured == {
        "kernel": kernel,
        "project_root": project.resolve(),
        "context": captured["context"],
        "suggestion": proposal,
        "source_slot": 123,
        "trusted_root": project.resolve(),
    }
    assert result == {
        "run_id": "run-123",
        "accepted": True,
        "executed": False,
        "reason": "admitted",
        "decision": {
            "policy": {
                "allowed": True,
                "reason": "admitted",
            }
        },
    }
    assert json.loads(json.dumps(result)) == result


@pytest.mark.parametrize(
    ("status", "bridge_run_id", "public_run_id", "accepted", "reason"),
    [
        ("bridge_disabled", None, None, False, "bridge_disabled"),
        ("unsupported_action", None, None, False, "unsupported_action"),
        ("coalesced", "run-existing", "run-existing", True, "coalesced"),
        ("created", "run-new", "run-new", True, "admitted"),
        ("created", "x" * 512, None, True, "admitted"),
    ],
)
def test_submit_nova_intent_returns_only_bounded_public_admission_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    bridge_run_id: str | None,
    public_run_id: str | None,
    accepted: bool,
    reason: str,
):
    """Catches internal admission or policy text escaping the live entry result."""
    project = tmp_path / status / "spaces" / "nova"
    project.mkdir(parents=True)

    class Kernel:
        space_dir = project
        actions = ActionRegistry(project)

    class ResultBridge:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def submit(self, _proposal, *, source_slot):
            assert source_slot == 9
            return bridge.NovaBridgeResult(
                status,
                run_id=bridge_run_id,
                reason="SECRET raw exception or policy text",
            )

    monkeypatch.setattr(bridge, "NovaSwarmRuntimeBridge", ResultBridge)

    result = bridge.submit_nova_intent(
        Kernel(),
        _diary_suggestion(),
        source_slot=9,
    )

    assert result == {
        "run_id": public_run_id,
        "accepted": accepted,
        "executed": False,
        "reason": reason,
        "decision": {
            "policy": {
                "allowed": accepted,
                "reason": reason,
            }
        },
    }
    assert "SECRET" not in json.dumps(result)


def test_submit_nova_intent_rejects_root_disagreement_before_any_runtime_surface(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Catches mismatched code-owned roots reaching storage, workers, or Nova."""
    kernel_root = tmp_path / "spaces" / "nova"
    action_root = tmp_path / "spaces" / "other"
    kernel_root.mkdir(parents=True)
    action_root.mkdir(parents=True)

    class Kernel:
        space_dir = kernel_root
        actions = ActionRegistry(action_root)

        def is_yolo_enabled(self):
            pytest.fail("root disagreement must not inspect runtime mode")

        def govern(self, _intent):
            pytest.fail("root disagreement must not call govern")

        def act(self, _decision):
            pytest.fail("root disagreement must not call act")

    class ForbiddenBridge:
        def __init__(self, *_args, **_kwargs) -> None:
            pytest.fail("root disagreement must not construct the runtime bridge")

    monkeypatch.setattr(bridge, "NovaSwarmRuntimeBridge", ForbiddenBridge)

    result = bridge.submit_nova_intent(
        Kernel(),
        _diary_suggestion() | {"action": "blog_draft"},
        source_slot=10,
    )

    assert result == {
        "run_id": None,
        "accepted": False,
        "executed": False,
        "reason": "root_mismatch",
        "decision": {
            "policy": {
                "allowed": False,
                "reason": "root_mismatch",
            }
        },
    }
    assert not (kernel_root / ".swarm").exists()
    assert not (action_root / ".swarm").exists()


def test_submit_nova_intent_rechecks_changing_roots_before_unsupported_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Catches roots changing after public preflight but before rejection storage."""
    trusted_root = tmp_path / "spaces" / "nova"
    changed_root = tmp_path / "spaces" / "changed"
    trusted_root.mkdir(parents=True)
    changed_root.mkdir(parents=True)
    monkeypatch.setattr(
        bridge,
        "load_nova_bridge_config",
        lambda _project_root: bridge.NovaBridgeConfig(enabled=True),
    )

    class ChangingRoot:
        def __init__(self) -> None:
            self.reads = 0

        @property
        def value(self) -> Path:
            self.reads += 1
            return trusted_root if self.reads <= 2 else changed_root

    kernel_root = ChangingRoot()
    action_root = ChangingRoot()

    class Actions:
        @property
        def space_dir(self) -> Path:
            return action_root.value

    class Kernel:
        actions = Actions()

        @property
        def space_dir(self) -> Path:
            return kernel_root.value

    store_roots: list[Path] = []

    class ForbiddenStore:
        def __init__(self, project_root: Path) -> None:
            store_roots.append(Path(project_root))
            raise RuntimeError("SECRET stale-root store construction")

    monkeypatch.setattr(bridge, "ProjectSwarmStore", ForbiddenStore)

    result = bridge.submit_nova_intent(
        Kernel(),
        _diary_suggestion() | {"action": "blog_draft"},
        source_slot=11,
    )

    assert result == {
        "run_id": None,
        "accepted": False,
        "executed": False,
        "reason": "root_mismatch",
        "decision": {
            "policy": {
                "allowed": False,
                "reason": "root_mismatch",
            }
        },
    }
    assert store_roots == []
    assert not (trusted_root / ".swarm").exists()
    assert not (changed_root / ".swarm").exists()
    assert "SECRET" not in json.dumps(result, allow_nan=False)


@pytest.mark.parametrize(
    "root_case",
    [
        "missing_kernel_root",
        "malformed_kernel_root",
        "missing_action_root",
        "raising_action_root",
    ],
)
def test_submit_nova_intent_bounds_malformed_code_owned_roots(
    tmp_path: Path,
    root_case: str,
):
    """Catches root inspection errors leaking text or reaching persistent state."""
    project = tmp_path / root_case / "spaces" / "nova"
    project.mkdir(parents=True)

    class MissingKernelRoot:
        actions = ActionRegistry(project)

    class MalformedKernelRoot:
        space_dir = object()
        actions = ActionRegistry(project)

    class MissingActionRoot:
        space_dir = project
        actions = object()

    class RaisingActions:
        @property
        def space_dir(self):
            raise RuntimeError("SECRET root resolver failure")

    class RaisingActionRoot:
        space_dir = project
        actions = RaisingActions()

    kernels = {
        "missing_kernel_root": MissingKernelRoot,
        "malformed_kernel_root": MalformedKernelRoot,
        "missing_action_root": MissingActionRoot,
        "raising_action_root": RaisingActionRoot,
    }

    result = bridge.submit_nova_intent(
        kernels[root_case](),
        _diary_suggestion() | {"action": "blog_draft"},
        source_slot=11,
    )

    assert result == {
        "run_id": None,
        "accepted": False,
        "executed": False,
        "reason": "root_mismatch",
        "decision": {
            "policy": {
                "allowed": False,
                "reason": "root_mismatch",
            }
        },
    }
    assert "SECRET" not in json.dumps(result, allow_nan=False)
    assert not (project / ".swarm").exists()


@pytest.mark.parametrize(
    ("proposal", "source_slot"),
    [
        (object(), 12),
        (_diary_suggestion(), float("nan")),
    ],
)
def test_submit_nova_intent_bounds_submission_validation_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    proposal: object,
    source_slot: object,
):
    """Catches malformed live inputs escaping as exceptions or non-JSON values."""
    project = tmp_path / "spaces" / "nova"
    project.mkdir(parents=True)

    class Kernel:
        space_dir = project
        actions = ActionRegistry(project)

    class RejectingBridge:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def submit(self, _proposal, *, source_slot):
            raise ValueError(f"SECRET invalid submission: {source_slot!r}")

    monkeypatch.setattr(bridge, "NovaSwarmRuntimeBridge", RejectingBridge)

    result = bridge.submit_nova_intent(
        Kernel(),
        proposal,
        source_slot=source_slot,
    )

    assert result == {
        "run_id": None,
        "accepted": False,
        "executed": False,
        "reason": "submission_rejected",
        "decision": {
            "policy": {
                "allowed": False,
                "reason": "submission_rejected",
            }
        },
    }
    assert "SECRET" not in json.dumps(result, allow_nan=False)


def test_submit_nova_intent_has_no_caller_root_or_resolver_parameters():
    """Catches the live caller gaining authority to mint the trusted context."""
    assert tuple(inspect.signature(bridge.submit_nova_intent).parameters) == (
        "kernel",
        "proposal",
        "source_slot",
    )


def test_bridge_matrix_01_disabled_reads_only_config_and_touches_no_runtime(
    tmp_path: Path,
):
    """#1 catches disabled admission creating state or touching any host surface."""
    project = tmp_path / "spaces" / "nova"
    project.mkdir(parents=True)
    timeline: list[str] = []
    kernel = _FakeNovaKernel(project)
    context = bridge._create_nova_bridge_context(
        project, validator=lambda candidate: candidate
    )

    result = bridge.NovaSwarmRuntimeBridge(
        kernel,
        project_root=project,
        trusted_project_root=context,
        dispatcher=lambda *_args: timeline.append("worker"),
    ).submit(_diary_suggestion(), source_slot=1)

    assert result == bridge.NovaBridgeResult("bridge_disabled")
    assert timeline == []
    assert kernel.yolo_checks == 0
    assert kernel.govern_calls == []
    assert kernel.actions.calls == []
    assert not (project / ".swarm").exists()


def test_bridge_matrix_02_unsupported_action_records_one_bounded_rejection_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """#2 catches rejected work reaching admission, Cloud, govern, act, or a worker."""
    host = _configure_enabled_fake_host(tmp_path, monkeypatch)
    suggestion = _diary_suggestion() | {"action": "blog_draft"}

    first = host.bridge(
        dispatcher=lambda *_args: pytest.fail("unsupported work must not dispatch")
    ).submit(suggestion, source_slot=2)
    second = host.bridge(
        dispatcher=lambda *_args: pytest.fail("unsupported work must not dispatch")
    ).submit(suggestion, source_slot=2)

    rejection = ProjectSwarmStore(host.project_root).get_integration_admission(
        "nova", bridge._submission_key(suggestion, 2)
    )
    assert first.status == second.status == "unsupported_action"
    assert first.run_id is second.run_id is None
    assert rejection is not None
    assert rejection.run is None
    assert rejection.reason == "unsupported_action"
    assert ProjectSwarmStore.open_read_only(host.project_root).list_runs() == []
    assert host.worker_run_ids == []
    assert host.model_calls == []
    assert host.kernel.govern_calls == []
    assert host.kernel.actions.calls == []


def test_bridge_matrix_03_equal_digest_new_bridge_coalesces_one_run_and_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """#3 catches process-local bridge instances defeating durable idempotency."""
    host = _configure_enabled_fake_host(tmp_path, monkeypatch)
    started = threading.Event()
    release = threading.Event()
    worker_run_ids: list[str] = []

    def blocking_dispatch(_project_root: Path, run_id: str) -> None:
        _capture_test_worker()
        worker_run_ids.append(run_id)
        started.set()
        assert release.wait(timeout=2)

    try:
        first = host.bridge(dispatcher=blocking_dispatch).submit(
            _diary_suggestion(), source_slot=3
        )
        assert first.run_id is not None
        assert started.wait(timeout=1)
        second = host.bridge(dispatcher=blocking_dispatch).submit(
            _diary_suggestion(), source_slot=3
        )

        assert first.status == "created"
        assert second.status == "coalesced"
        assert second.run_id == first.run_id
        assert worker_run_ids == [first.run_id]
        assert len(ProjectSwarmStore.open_read_only(host.project_root).list_runs()) == 1
        assert host.model_calls == []
    finally:
        if "first" in locals() and first.run_id is not None:
            store = ProjectSwarmStore(host.project_root)
            if store.get_run(first.run_id).status == "running":
                store.set_run_status(first.run_id, "paused")
        release.set()


def test_bridge_matrix_04_different_intent_is_rejected_while_running_and_paused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """#4 catches replacement workers bypassing the durable one-active slot."""
    host = _configure_enabled_fake_host(tmp_path, monkeypatch)
    started = threading.Event()
    release = threading.Event()
    worker_run_ids: list[str] = []

    def blocking_dispatch(_project_root: Path, run_id: str) -> None:
        _capture_test_worker()
        worker_run_ids.append(run_id)
        started.set()
        assert release.wait(timeout=2)

    try:
        first = host.bridge(dispatcher=blocking_dispatch).submit(
            _diary_suggestion(), source_slot=4
        )
        assert first.run_id is not None
        assert started.wait(timeout=1)

        while_running = host.bridge(dispatcher=blocking_dispatch).submit(
            _diary_suggestion() | {"title": "A different running intent"},
            source_slot=5,
        )
        ProjectSwarmStore(host.project_root).set_run_status(first.run_id, "paused")
        while_paused = host.bridge(dispatcher=blocking_dispatch).submit(
            _diary_suggestion() | {"title": "A different paused intent"},
            source_slot=6,
        )

        assert while_running.status == while_paused.status == "active_limit"
        assert while_running.run_id is while_paused.run_id is None
        assert worker_run_ids == [first.run_id]
        assert host.model_calls == []
    finally:
        release.set()


def test_bridge_matrix_05_standard_quota_and_literal_kernel_yolo_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """#5 catches autonomy/call-budget drift or caller-visible quota bypasses."""
    standard = _configure_enabled_fake_host(tmp_path / "standard", monkeypatch)
    standard_runs = []
    for source_slot in range(10, 16):
        admitted = standard.bridge().submit(
            _diary_suggestion()
            | {"payload": {"content": f"standard intent {source_slot}"}},
            source_slot=source_slot,
        )
        assert admitted.status == "created"
        assert admitted.run_id is not None
        summary = standard.wait_for_worker(admitted.run_id)
        assert summary.status == "completed"
        standard_runs.append(
            ProjectSwarmStore.open_read_only(standard.project_root).get_run(
                admitted.run_id
            )
        )

    seventh = standard.bridge().submit(
        _diary_suggestion() | {"payload": {"content": "seventh standard intent"}},
        source_slot=16,
    )
    assert seventh.status == "rolling_limit"
    assert all(
        run is not None
        and run.metadata["autonomy"] == "reviewed_execution"
        and run.metadata["nova_mode"] == "reviewed_execution"
        and run.metadata["nova_max_calls"] == 48
        for run in standard_runs
    )
    assert len(standard.worker_run_ids) == 6

    yolo = _configure_enabled_fake_host(
        tmp_path / "yolo", monkeypatch, yolo=True
    )
    yolo_runs = []
    for source_slot in range(20, 27):
        admitted = yolo.bridge().submit(
            _diary_suggestion()
            | {"payload": {"content": f"yolo intent {source_slot}"}},
            source_slot=source_slot,
        )
        assert admitted.status == "created"
        assert admitted.run_id is not None
        summary = yolo.wait_for_worker(admitted.run_id)
        assert summary.status == "completed"
        yolo_runs.append(
            ProjectSwarmStore.open_read_only(yolo.project_root).get_run(
                admitted.run_id
            )
        )
    assert all(
        run is not None
        and run.metadata["autonomy"] == "autonomous"
        and run.metadata["nova_mode"] == "autonomous"
        and run.metadata["nova_max_calls"] == 128
        for run in yolo_runs
    )

    started = threading.Event()
    release = threading.Event()
    active_workers: list[str] = []

    def blocking_dispatch(_project_root: Path, run_id: str) -> None:
        _capture_test_worker()
        active_workers.append(run_id)
        started.set()
        assert release.wait(timeout=2)

    try:
        active = yolo.bridge(dispatcher=blocking_dispatch).submit(
            _diary_suggestion() | {"payload": {"content": "active yolo intent"}},
            source_slot=27,
        )
        assert active.run_id is not None
        assert started.wait(timeout=1)
        blocked = yolo.bridge(dispatcher=blocking_dispatch).submit(
            _diary_suggestion() | {"payload": {"content": "blocked yolo intent"}},
            source_slot=28,
        )
        assert blocked.status == "active_limit"
        assert active_workers == [active.run_id]
    finally:
        if "active" in locals() and active.run_id is not None:
            store = ProjectSwarmStore(yolo.project_root)
            if store.get_run(active.run_id).status == "running":
                store.set_run_status(active.run_id, "paused")
        release.set()


def test_bridge_matrix_06_yolo_rejects_unsafe_sources_and_ignores_caller_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """#6 catches caller YOLO escalation or unsafe work entering automatic mode."""
    host = _configure_enabled_fake_host(tmp_path, monkeypatch, yolo=True)

    def no_dispatch(*_args):
        pytest.fail("rejected work must not dispatch")

    for action in ("reflection", "blog_draft"):
        rejected = host.bridge(dispatcher=no_dispatch).submit(
            _diary_suggestion() | {"action": action},
            source_slot=30,
        )
        assert rejected.status == "unsupported_action"

    outside = host.project_root.parent / "other-root"
    outside.mkdir()
    host.kernel.space_dir = outside
    with pytest.raises(ValueError, match="roots do not match"):
        host.bridge(dispatcher=no_dispatch).submit(
            _diary_suggestion(), source_slot=31
        )
    host.kernel.space_dir = host.project_root

    host.kernel.yolo = False
    started = threading.Event()
    release = threading.Event()

    def blocking_dispatch(_project_root: Path, _run_id: str) -> None:
        _capture_test_worker()
        started.set()
        assert release.wait(timeout=2)

    try:
        caller_flag = host.bridge(dispatcher=blocking_dispatch).submit(
            _diary_suggestion()
            | {
                "yolo": True,
                "autonomy": "autonomous",
                "external": False,
            },
            source_slot=32,
        )
        assert caller_flag.run_id is not None
        assert started.wait(timeout=1)
        run = ProjectSwarmStore.open_read_only(host.project_root).get_run(
            caller_flag.run_id
        )
        assert run is not None
        assert run.metadata["nova_mode"] == "reviewed_execution"
        assert run.metadata["nova_max_calls"] == 48
    finally:
        if "caller_flag" in locals() and caller_flag.run_id is not None:
            store = ProjectSwarmStore(host.project_root)
            if store.get_run(caller_flag.run_id).status == "running":
                store.set_run_status(caller_flag.run_id, "paused")
        release.set()
    assert host.model_calls == []
    assert host.kernel.govern_calls == []
    assert host.kernel.actions.calls == []


def test_bridge_matrix_06_yolo_allowlisted_external_spec_still_requires_human(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """#6 catches YOLO treating an allowlisted but human-gated spec as local-safe."""
    original = get_nova_action_spec("mind_diary")
    controlled_external = replace(
        original,
        capabilities=ActionCapabilities(
            category="project",
            reversible=True,
            external=True,
            cost_increasing=False,
        ),
        policy_tier="external",
    )
    controlled_specs = dict(nova_adapter.NOVA_ACTION_SPECS)
    controlled_specs["mind_diary"] = controlled_external
    monkeypatch.setattr(
        nova_adapter,
        "NOVA_ACTION_SPECS",
        MappingProxyType(controlled_specs),
    )
    host = _configure_enabled_fake_host(tmp_path, monkeypatch, yolo=True)
    host.kernel.policy.tiers["mind_diary"] = "external"

    admitted = host.bridge().submit(_diary_suggestion(), source_slot=33)
    assert admitted.status == "created"
    assert admitted.run_id is not None
    summary = host.wait_for_worker(admitted.run_id)
    host.join_worker(admitted.run_id)

    events = ProjectSwarmStore.open_read_only(host.project_root).list_events(
        admitted.run_id
    )
    assert summary.status == "paused"
    assert summary.pause_reason == "nova_human_approval_required"
    assert any(
        event.event_type == "nova.bridge.action_proposed" for event in events
    )
    assert len(host.model_calls) == 8
    assert host.kernel.govern_calls == []
    assert host.kernel.actions.calls == []


def test_bridge_matrix_07_verified_workflow_orders_policy_govern_act_and_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """#7 catches action execution outside the complete verified bridge lifecycle."""
    host = _configure_enabled_fake_host(tmp_path, monkeypatch)

    admitted = host.bridge().submit(_diary_suggestion(), source_slot=40)
    assert admitted.status == "created"
    assert admitted.run_id is not None
    summary = host.wait_for_worker(admitted.run_id)

    events = ProjectSwarmStore.open_read_only(host.project_root).list_events(
        admitted.run_id
    )
    event_types = [event.event_type for event in events]
    assert summary.status == "completed"
    assert host.kernel.policy_claimed_before_govern == [True]
    assert host.kernel.timeline == ["govern", "act"]
    assert len(host.kernel.actions.calls) == 1
    assert (
        event_types.index("nova.bridge.action_proposed")
        < event_types.index("nova.bridge.action_result")
        < event_types.index("run.completed")
    )
    assert len(host.model_calls) == 8
    assert len(host.provider_slots) == len(host.model_calls)
    assert all(provider == "ollama-cloud" for _run_id, provider in host.provider_slots)


def test_reviewers_see_and_durably_bind_the_exact_nova_action_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Reviewer checkpoints authorize the canonical action, not its benign title."""
    host = _configure_enabled_fake_host(tmp_path, monkeypatch)
    suggestion = _diary_suggestion() | {
        "title": "Benign continuity note",
        "payload": {"content": "Material action payload requiring exact review."},
    }

    admitted = host.bridge().submit(suggestion, source_slot=41)
    assert admitted.run_id is not None
    summary = host.wait_for_worker(admitted.run_id)
    assert summary.status == "completed"
    durable = ProjectSwarmStore.open_read_only(host.project_root)
    run = durable.get_run(admitted.run_id)
    assert run is not None
    authorization = run.metadata["nova_review_authorization"]
    assert authorization == {
        "action": "mind_diary",
        "target": {},
        "payload": {
            "content": "Material action payload requiring exact review.",
        },
        "expected_output_scope": "nova_data/entity/mind_diary.jsonl",
        "intent_digest": run.metadata["nova_intent_digest"],
        "proposal_digest": run.metadata["proposal_digest"],
    }

    review_calls = [
        call
        for call in host.model_calls
        if call["model"] in {"glm-5.2", "kimi-k2.7-code"}
    ]
    assert len(review_calls) == 2
    for call in review_calls:
        rendered = call["messages"][0]["content"]
        context = _extract_rendered_prompt_context(rendered)
        assert context["authorization_context"] == authorization

    checkpoints = ProjectSwarmStore(
        host.project_root
    ).get_workflow_role_checkpoints(admitted.run_id)
    for role in ("review_a", "review_b"):
        assert checkpoints[role].data["intent_digest"] == authorization["intent_digest"]
        assert (
            checkpoints[role].data["proposal_digest"]
            == authorization["proposal_digest"]
        )


def test_conflicting_reviewer_digest_never_reaches_nova_govern_or_act(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """A model cannot authorize another action by returning conflicting bindings."""
    host = _configure_enabled_fake_host(tmp_path, monkeypatch)
    original_call = host.service._call_llm

    def mismatched_review(**kwargs):
        response = original_call(**kwargs)
        if kwargs["model"] == "glm-5.2":
            data = json.loads(response["choices"][0]["message"]["content"])
            data["intent_digest"] = "0" * 64
            data["proposal_digest"] = "1" * 64
            response = copy.deepcopy(response)
            response["choices"][0]["message"]["content"] = json.dumps(data)
        return response

    host.service._call_llm = mismatched_review
    admitted = host.bridge().submit(
        _diary_suggestion()
        | {
            "title": "Benign continuity note",
            "payload": {"content": "A materially different action payload."},
        },
        source_slot=42,
    )
    assert admitted.run_id is not None
    summary = host.wait_for_worker(admitted.run_id)

    assert summary.status == "paused"
    assert host.kernel.govern_calls == []
    assert host.kernel.actions.calls == []


def test_review_authorization_context_resists_emitter_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """An event consumer cannot mutate the shared context before model dispatch."""
    host = _configure_enabled_fake_host(tmp_path, monkeypatch)
    original_append = ProjectSwarmStore.append_event
    mutation_count = 0

    def mutating_append(
        store,
        run_id,
        event_type,
        payload=None,
        *,
        visibility="project",
    ):
        nonlocal mutation_count
        if (
            event_type == "work.started"
            and isinstance(payload, dict)
            and payload.get("role") in {"review_a", "review_b"}
        ):
            authorization = payload["context"].get("authorization_context")
            if authorization is not None:
                authorization["payload"]["content"] = "mutated by event consumer"
                mutation_count += 1
        return original_append(
            store,
            run_id,
            event_type,
            payload,
            visibility=visibility,
        )

    monkeypatch.setattr(ProjectSwarmStore, "append_event", mutating_append)
    expected_content = "Canonical action payload."
    admitted = host.bridge().submit(
        _diary_suggestion()
        | {
            "title": "Benign continuity note",
            "payload": {"content": expected_content},
        },
        source_slot=43,
    )
    assert admitted.run_id is not None
    summary = host.wait_for_worker(admitted.run_id)

    assert summary.status == "completed"
    assert mutation_count == 2
    review_calls = [
        call
        for call in host.model_calls
        if call["model"] in {"glm-5.2", "kimi-k2.7-code"}
    ]
    assert len(review_calls) == 2
    for call in review_calls:
        rendered = call["messages"][0]["content"]
        context = _extract_rendered_prompt_context(rendered)
        assert (
            context["authorization_context"]["payload"]["content"]
            == expected_content
        )


@pytest.mark.parametrize(
    ("case", "expected_pause"),
    [
        ("negative_verifier", "nova_review_evidence_unavailable"),
        ("unavailable_verifier", "nova_review_evidence_unavailable"),
        ("review_denial", "nova_review_evidence_unavailable"),
        ("evidence_mismatch", "nova_review_evidence_unavailable"),
        ("proposal_digest_mismatch", "nova_proposal_digest_mismatch"),
        ("snapshot_mismatch", "nova_proposal_digest_mismatch"),
        ("root_mismatch", "nova_root_mismatch"),
        ("nova_denial", "nova_policy_denied"),
    ],
)
def test_bridge_matrix_08_denials_and_mismatches_pause_auditably_without_act(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_pause: str,
):
    """#8 catches a failed verifier, review, binding, root, or Nova gate acting."""
    host = _configure_enabled_fake_host(
        tmp_path,
        monkeypatch,
        review_denied_model="glm-5.2" if case == "review_denial" else None,
    )
    if case in {"negative_verifier", "unavailable_verifier", "evidence_mismatch"}:
        decision = (
            "verification_unavailable"
            if case == "unavailable_verifier"
            else "rejected"
            if case == "negative_verifier"
            else VERIFIED_DECISION
        )
        host.options_mutator = lambda options, _run: replace(
            options,
            verifier=_FixedVerifier(
                decision=decision,
                evidence="task8:independent-but-unbound",
            ),
        )
    elif case == "proposal_digest_mismatch":
        host.after_options_resolved = lambda run: _tamper_run_metadata(
            host.project_root,
            run.run_id,
            lambda metadata: metadata.__setitem__("proposal_digest", "0" * 64),
        )
    elif case == "snapshot_mismatch":
        host.after_options_resolved = lambda run: _tamper_run_metadata(
            host.project_root,
            run.run_id,
            lambda metadata: metadata["nova_snapshot"].__setitem__(
                "title", "tampered durable snapshot"
            ),
        )
    elif case == "root_mismatch":
        other = host.project_root.parent / "root-after-admission"
        other.mkdir()
        host.kernel.root_mismatch_target = other
    elif case == "nova_denial":
        host.kernel.govern_mode = "deny"

    admitted = host.bridge().submit(_diary_suggestion(), source_slot=50)
    assert admitted.run_id is not None
    summary = host.wait_for_worker(admitted.run_id)

    assert summary.status == "paused"
    assert summary.pause_reason == expected_pause
    assert _pause_reason(host.project_root, admitted.run_id) == expected_pause
    assert host.kernel.actions.calls == []


def test_bridge_matrix_09_provider_pause_has_no_replacement_or_automatic_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """#9 catches provider exhaustion spawning or silently resuming another worker."""
    host = _configure_enabled_fake_host(
        tmp_path, monkeypatch, provider_unavailable=True
    )
    dispatch_lock = threading.Lock()
    dispatches: list[str] = []
    unexpected_dispatch = threading.Event()
    dispatcher_returned = threading.Event()

    def guarded_dispatch(project_root: Path, run_id: str) -> None:
        with dispatch_lock:
            dispatches.append(run_id)
            if len(dispatches) != 1:
                unexpected_dispatch.set()
                raise AssertionError("provider pause dispatched a replacement worker")
        host.dispatch(project_root, run_id)
        dispatcher_returned.set()

    admitted = host.bridge(dispatcher=guarded_dispatch).submit(
        _diary_suggestion(), source_slot=60
    )
    assert admitted.run_id is not None
    summary = host.wait_for_worker(admitted.run_id)
    host.join_worker(admitted.run_id)

    persisted = ProjectSwarmStore.open_read_only(host.project_root).get_run(
        admitted.run_id
    )
    assert summary.status == "paused"
    assert summary.pause_reason == "model_chain_exhausted"
    assert persisted is not None and persisted.status == "paused"
    assert dispatcher_returned.is_set()
    assert not unexpected_dispatch.is_set()
    assert dispatches == [admitted.run_id]
    assert host.worker_run_ids == [admitted.run_id]
    assert host.kernel.govern_calls == []
    assert host.kernel.actions.calls == []


def test_bridge_matrix_10_post_claim_crash_recovers_once_without_replaying_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """#10 catches recovery replaying a policy-claimed govern or act boundary."""
    host = _configure_enabled_fake_host(tmp_path, monkeypatch)
    host.kernel.actions.crash_after_recording = True

    admitted = host.bridge().submit(_diary_suggestion(), source_slot=70)
    assert admitted.run_id is not None
    host.wait_for_dispatch()
    host.join_worker(admitted.run_id)
    first_events = ProjectSwarmStore.open_read_only(host.project_root).list_events(
        admitted.run_id
    )
    persisted = ProjectSwarmStore.open_read_only(host.project_root).get_run(
        admitted.run_id
    )
    assert persisted is not None and persisted.status == "paused"
    assert [
        event.payload
        for event in first_events
        if event.event_type == "nova.bridge.dispatch_failed"
    ] == [{"reason": "nova_dispatch_failed"}]
    assert len(host.kernel.govern_calls) == 1
    assert len(host.kernel.actions.calls) == 1
    calls_after_crash = (
        len(host.kernel.govern_calls),
        len(host.kernel.actions.calls),
    )

    host.kernel.actions.crash_after_recording = False
    host.service.resume(host.project_root, admitted.run_id)
    second = host.service.execute_run(host.project_root, admitted.run_id)
    assert second.pause_reason == "nova_action_claimed_requires_human_recovery"

    host.service.resume(host.project_root, admitted.run_id)
    third = host.service.execute_run(host.project_root, admitted.run_id)

    events = ProjectSwarmStore.open_read_only(host.project_root).list_events(
        admitted.run_id
    )
    recovery_events = [
        event
        for event in events
        if event.event_type == "nova.bridge.recovery_required"
    ]
    assert third.pause_reason == "nova_action_claimed_requires_human_recovery"
    assert len(recovery_events) == 1
    assert (
        len(host.kernel.govern_calls),
        len(host.kernel.actions.calls),
    ) == calls_after_crash


def test_attach_admitted_run_accepts_only_a_matching_paused_durable_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Catches same-process attachment trusting metadata without the host capability."""
    host = _configure_enabled_fake_host(tmp_path, monkeypatch)
    paused = _admit_paused_without_model_calls(host, source_slot=80)
    bridge._unregister_runtime_binding(host.project_root, paused.run_id)
    assert (
        bridge.nova_execution_options_for_run(
            host.project_root, paused
        ).blocked_reason
        == "nova_bridge_unavailable"
    )

    host.bridge().attach_admitted_run(paused)

    options = bridge.nova_execution_options_for_run(host.project_root, paused)
    assert options is not None
    assert options.blocked_reason is None
    assert options.max_calls == 48
    assert options.verifier is not None
    assert options.pre_completion_hook is not None


@pytest.mark.parametrize(
    "case",
    ["running", "completed", "root_mismatch", "proposal_mismatch"],
)
def test_attach_admitted_run_rejection_preserves_the_existing_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
):
    """Catches a failed attachment deleting or replacing a valid process binding."""
    host = _configure_enabled_fake_host(tmp_path, monkeypatch)
    paused = _admit_paused_without_model_calls(host, source_slot=81)
    store = ProjectSwarmStore(host.project_root)
    candidate = paused
    if case in {"running", "completed", "root_mismatch"}:
        metadata = copy.deepcopy(paused.metadata)
        if case == "root_mismatch":
            metadata["project_root"] = str(host.project_root.parent / "other")
        candidate = store.create_run(
            run_id=f"attach-{case}",
            status="completed" if case == "completed" else "paused"
            if case == "root_mismatch"
            else "running",
            metadata=metadata,
        )
    else:
        _tamper_run_metadata(
            host.project_root,
            paused.run_id,
            lambda metadata: metadata.__setitem__("proposal_digest", "f" * 64),
        )

    with pytest.raises((RuntimeError, ValueError)):
        host.bridge().attach_admitted_run(candidate)

    existing = bridge.nova_execution_options_for_run(host.project_root, paused)
    assert existing is not None
    assert existing.blocked_reason is None
    assert existing.max_calls == 48


def test_prioritize_thread_normalizes_blank_thread_id_to_a_valid_topic(
    nova_project: Path, trusted_nova_project
):
    """Catches a verified fallback topic being rejected by the concrete handler."""
    snapshot = NovaIntentSnapshot.from_submission(
        _diary_suggestion()
        | {
            "action": "prioritize_thread",
            "target": {"thread_id": "  ", "topic": " release "},
            "payload": {},
        },
        source_slot=45,
        project_root=trusted_nova_project,
    )

    result = ActionRegistry(nova_project).execute(snapshot.to_suggestion(trusted_nova_project), {})

    assert result["ok"] is True
    assert result["effects"]["thread"]["thread_id"] == "release"


def test_runtime_bridge_disabled_is_read_only(tmp_path: Path):
    """Catches a disabled bridge constructing the Swarm store or dispatching."""
    from nova.swarm_runtime_bridge import NovaSwarmRuntimeBridge

    project = tmp_path / "nova"
    project.mkdir()

    class Kernel:
        space_dir = project
        actions = type("Actions", (), {"space_dir": project})()

    result = NovaSwarmRuntimeBridge(Kernel(), project_root=project).submit(
        _diary_suggestion(), source_slot=7
    )

    assert result.status == "bridge_disabled"
    assert not (project / ".swarm").exists()


def test_worker_system_exit_pauses_the_durable_admission(tmp_path: Path):
    """Catches a dispatcher SystemExit leaving its active slot permanently running."""
    from nova.swarm_runtime_bridge import _run_worker
    from swarm_core.store import ProjectSwarmStore

    store = ProjectSwarmStore(tmp_path)
    run = store.create_run(metadata={"autonomy": "reviewed_execution"})

    def stop(*_args):
        raise SystemExit(23)

    _run_worker(stop, tmp_path, run.run_id)

    assert store.get_run(run.run_id).status == "paused"
    assert store.list_events(run.run_id)[-1].payload == {"reason": "nova_dispatch_failed"}


def test_resolver_blocks_an_enabled_nova_run_without_a_process_binding(tmp_path: Path):
    """Catches a cross-process resume constructing an engine without Nova trust."""
    from nova.swarm_runtime_bridge import configure_nova_bridge, nova_execution_options_for_run
    from swarm_core.store import ProjectSwarmStore

    configure_nova_bridge(tmp_path, enabled=True)
    run = ProjectSwarmStore(tmp_path).create_run(
        metadata={
            "goal": "g", "pack": "coding-team", "project_root": str(tmp_path.resolve()),
            "autonomy": "reviewed_execution", "integration_namespace": "nova",
            "nova_intent_digest": "a" * 64, "nova_snapshot": {},
            "nova_mode": "reviewed_execution", "nova_max_calls": 48,
            "proposal_digest": "b" * 64, "required_pre_completion_hook": "nova-runtime-v1",
        }
    )

    assert nova_execution_options_for_run(tmp_path, run).blocked_reason == "nova_bridge_unavailable"


def test_runtime_verifier_implements_the_workflow_request_protocol(trusted_nova_project):
    """Catches a snapshot-only verifier being injected into the Core workflow."""
    from swarm_core.verifier import VerificationRequest

    snapshot = NovaIntentSnapshot.from_submission(
        _diary_suggestion(), source_slot=91, project_root=trusted_nova_project
    )
    verifier = NovaIntentReadOnlyVerifier(
        trusted_nova_project, snapshot=snapshot, run_id="nova-run"
    )

    result = verifier.verify(
        VerificationRequest(
            run_id="nova-run", goal=snapshot.title,
            project_root=snapshot.project_root, builder={}, critic={},
        )
    )

    assert result.decision == VERIFIED_DECISION
    assert result.evidence == (snapshot.verifier_evidence_ref,)


def test_runtime_verifier_rejects_a_wrong_workflow_goal_or_run(trusted_nova_project):
    """Catches a registered verifier accepting another workflow's context."""
    from swarm_core.verifier import InvalidVerifierResult, VerificationRequest

    snapshot = NovaIntentSnapshot.from_submission(
        _diary_suggestion(), source_slot=92, project_root=trusted_nova_project
    )
    verifier = NovaIntentReadOnlyVerifier(
        trusted_nova_project, snapshot=snapshot, run_id="bound-run"
    )
    for run_id, goal in (("other-run", snapshot.title), ("bound-run", "other goal")):
        with pytest.raises(InvalidVerifierResult):
            verifier.verify(
                VerificationRequest(
                    run_id=run_id, goal=goal, project_root=snapshot.project_root,
                    builder={}, critic={},
                )
            )


def test_attach_rejects_a_running_run_before_it_can_register_context(tmp_path: Path):
    """Catches Task-6 attachment mutating the registry for a running worker."""
    import nova.swarm_runtime_bridge as bridge
    from swarm_core.store import ProjectSwarmStore

    class Kernel:
        space_dir = tmp_path
        actions = type("Actions", (), {"space_dir": tmp_path})()

    trusted = bridge._create_nova_bridge_context(tmp_path, validator=lambda root: root)
    running = ProjectSwarmStore(tmp_path).create_run(metadata={"autonomy": "reviewed_execution"})

    with pytest.raises(ValueError, match="paused"):
        bridge.NovaSwarmRuntimeBridge(
            Kernel(), project_root=tmp_path, trusted_project_root=trusted
        ).attach_admitted_run(running)
