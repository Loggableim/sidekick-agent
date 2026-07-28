from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path

import pytest

from nova.actions import ActionRegistry
from nova.swarm_adapter import get_nova_action_spec
from nova.swarm_runtime_bridge import (
    NOVA_AUTOMATIC_ACTIONS,
    NovaIntentReadOnlyVerifier,
    NovaIntentSnapshot,
    configure_nova_bridge,
    load_nova_bridge_config,
)
from swarm_core.verifier import InvalidVerifierResult, VERIFIED_DECISION


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


def test_snapshot_identity_is_stable_for_one_decision_slot(nova_project: Path):
    """Catches caller-owned identity replacing a canonical decision identity."""
    first = NovaIntentSnapshot.from_submission(
        _diary_suggestion(), source_slot=1234, project_root=nova_project
    )
    second = NovaIntentSnapshot.from_submission(
        _diary_suggestion(), source_slot=1234, project_root=nova_project
    )

    assert first.intent_digest == second.intent_digest
    assert first.proposal_id == second.proposal_id
    assert first.verifier_evidence_ref == f"nova:verifier:{first.intent_digest}"
    assert first.to_suggestion()["evidence_refs"] == [first.verifier_evidence_ref]


def test_snapshot_slot_change_changes_the_canonical_digest(nova_project: Path):
    """Catches distinct decision slots collapsing onto one approval identity."""
    first = NovaIntentSnapshot.from_submission(
        _diary_suggestion(), source_slot=1234, project_root=nova_project
    )
    second = NovaIntentSnapshot.from_submission(
        _diary_suggestion(), source_slot=1235, project_root=nova_project
    )

    assert first.intent_digest != second.intent_digest
    assert first.proposal_id != second.proposal_id


@pytest.mark.parametrize(
    "action", ["reflection", "aces", "moltbook", "blog_draft", "unknown_action"]
)
def test_snapshot_rejects_non_automatic_actions_before_any_runtime_path(
    nova_project: Path, action: str
):
    """Catches a reflective, ACES, social, blog, or unknown action reaching Nova."""
    with pytest.raises(ValueError, match="automatic Nova action"):
        NovaIntentSnapshot.from_submission(
            _diary_suggestion() | {"action": action},
            source_slot=1,
            project_root=nova_project,
        )


def test_snapshot_discards_caller_controlled_identity_and_security_fields(
    nova_project: Path,
):
    """Catches untrusted proposal metadata weakening canonical bridge output."""
    snapshot = NovaIntentSnapshot.from_submission(
        _diary_suggestion(), source_slot=9, project_root=nova_project
    )
    suggestion = snapshot.to_suggestion()

    assert suggestion["id"] == snapshot.proposal_id
    assert suggestion["intent_id"] == snapshot.proposal_id
    assert suggestion["proposal_id"] == snapshot.proposal_id
    assert suggestion["evidence_refs"] == [snapshot.verifier_evidence_ref]
    assert "tier" not in suggestion
    assert "policy_tier" not in suggestion
    assert "capabilities" not in suggestion
    assert "caller-controlled-id" not in repr(suggestion)
    assert "builder:untrusted" not in repr(suggestion)


def test_verifier_returns_exactly_its_snapshot_evidence(nova_project: Path):
    """Catches the verifier copying Builder/Critic evidence into a positive result."""
    snapshot = NovaIntentSnapshot.from_submission(
        _diary_suggestion(), source_slot=17, project_root=nova_project
    )

    result = NovaIntentReadOnlyVerifier(nova_project).verify(snapshot)

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
    nova_project: Path, mutator
):
    """Catches verifier acceptance of a root escape, tamper, output escape, or effect marker."""
    marker = nova_project / "must-not-change.txt"
    marker.write_text("unchanged", encoding="utf-8")
    snapshot = NovaIntentSnapshot.from_submission(
        _diary_suggestion(), source_slot=22, project_root=nova_project
    )

    with pytest.raises(InvalidVerifierResult):
        NovaIntentReadOnlyVerifier(nova_project).verify(mutator(snapshot, nova_project))

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
        project_root=nova_project,
    )

    actual_scope = Path(actual["effects"]["path"]).resolve().relative_to(
        nova_project.resolve()
    ).as_posix()
    assert get_nova_action_spec(action).output_scope == actual_scope
    assert snapshot.expected_output_scope == actual_scope


@pytest.mark.parametrize(
    "submission_change",
    [
        {"payload": {"c\uff4fmm\uff41nd": "write"}},
        {"payload": {"content": "Y29tbWFuZA=="}},
        {"target": {"nested": {"%63ommand": "write"}}},
    ],
)
def test_snapshot_rejects_normalized_or_encoded_control_material(
    nova_project: Path, submission_change: dict[str, object]
):
    """Catches Unicode, percent, or opaque-encoded fields bypassing action schemas."""
    with pytest.raises(ValueError):
        NovaIntentSnapshot.from_submission(
            _diary_suggestion() | submission_change,
            source_slot=34,
            project_root=nova_project,
        )


def test_snapshot_requires_a_real_trusted_project_root(tmp_path: Path):
    """Catches a caller selecting a nonexistent or Windows system root as Nova space."""
    suggestion = _diary_suggestion()

    with pytest.raises(ValueError):
        NovaIntentSnapshot.from_submission(
            suggestion, source_slot=35, project_root=tmp_path / "does-not-exist"
        )
    with pytest.raises(ValueError):
        NovaIntentSnapshot.from_submission(
            suggestion,
            source_slot=35,
            project_root=Path(os.environ["SystemRoot"]),
        )
    with pytest.raises(ValueError):
        NovaIntentReadOnlyVerifier(Path(os.environ["SystemRoot"]))


def test_canonical_unicode_and_numeric_forms_have_one_identity(nova_project: Path):
    """Catches visually equal Unicode or numeric zero/one forms creating new intents."""
    decomposed = _diary_suggestion() | {"title": "Cafe\u0301", "priority": 1}
    composed = _diary_suggestion() | {"title": "Caf\u00e9", "priority": 1.0}
    negative_zero = _diary_suggestion() | {"priority": -0.0}
    positive_zero = _diary_suggestion() | {"priority": 0}

    assert NovaIntentSnapshot.from_submission(
        decomposed, source_slot=36, project_root=nova_project
    ).intent_digest == NovaIntentSnapshot.from_submission(
        composed, source_slot=36, project_root=nova_project
    ).intent_digest
    assert NovaIntentSnapshot.from_submission(
        negative_zero, source_slot=37, project_root=nova_project
    ).intent_digest == NovaIntentSnapshot.from_submission(
        positive_zero, source_slot=37, project_root=nova_project
    ).intent_digest


def test_huge_priority_is_rejected_without_an_overflow_crash(nova_project: Path):
    """Catches an unbounded integer causing float conversion to escape validation."""
    with pytest.raises(ValueError):
        NovaIntentSnapshot.from_submission(
            _diary_suggestion() | {"priority": 10**1000},
            source_slot=38,
            project_root=nova_project,
        )
