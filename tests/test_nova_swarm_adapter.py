from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from nova.swarm_adapter import NovaSwarmAdapter
from swarm_core.policy import PolicyGate, PolicyStatus
from swarm_core.store import ProjectSwarmStore


class _Kernel:
    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def govern(self, proposal: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("govern", proposal))
        return {
            "intent": proposal,
            "policy": {
                "allowed": self.allowed,
                "reason": "allowed" if self.allowed else "nova_policy_blocked",
            },
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("act", decision))
        return {"executed": True, "action": decision["intent"]["action"]}


def _suggestion(*, proposal_id: str = "nova-draft-1") -> dict[str, Any]:
    return {
        "id": proposal_id,
        "action": "blog_draft",
        "title": "Draft the release notes",
        "why": "Verifier evidence supports a local draft.",
        "target": {"topic": "release"},
        "payload": {"content": "draft only"},
        "evidence_refs": ["evidence:verified"],
        # These caller-owned values must never downgrade the adapter registry.
        "category": "external",
        "reversible": False,
        "external": True,
        "cost_increasing": True,
        "policy_tier": "silent",
    }


def _run(tmp_path: Path, *, autonomy: str = "autonomous"):
    return ProjectSwarmStore(tmp_path).create_run(metadata={"autonomy": autonomy})


def test_translate_owns_capabilities_and_rejects_unknown_nova_actions(
    tmp_path: Path,
):
    """Catches a Nova suggestion relabeling a risky action as locally safe."""
    store = ProjectSwarmStore(tmp_path)
    adapter = NovaSwarmAdapter(_Kernel(), PolicyGate(store))

    proposal = adapter.translate(_suggestion())

    assert proposal.requested_action.name == "nova:blog_draft"
    assert proposal.requested_action.workspace == tmp_path.resolve()
    assert proposal.category == "project"
    assert proposal.reversible is True
    assert proposal.external is False
    assert proposal.cost_increasing is False
    assert proposal.requested_action.arguments["intent"]["policy_tier"] == "internal"

    with pytest.raises(ValueError, match="unsupported Nova action"):
        adapter.translate({"id": "unknown-1", "action": "publish_release"})


def test_disabled_adapter_never_claims_or_calls_the_nova_kernel(tmp_path: Path):
    """Catches a prepared adapter becoming live merely because it is imported."""
    store = ProjectSwarmStore(tmp_path)
    run = store.create_run(metadata={"autonomy": "autonomous"})
    kernel = _Kernel()
    disabled = NovaSwarmAdapter(kernel, PolicyGate(store))

    result = disabled.execute_suggestion(_suggestion(), run)

    assert result.executed is False
    assert result.reason == "adapter_disabled"
    assert kernel.calls == []

    enabled = NovaSwarmAdapter(kernel, PolicyGate(store), enabled=True)
    allowed = enabled.execute_suggestion(_suggestion(), run)
    assert allowed.executed is True


def test_swarm_authorization_happens_before_nova_govern_and_act(tmp_path: Path):
    """Catches an allowed Nova action bypassing the durable Swarm claim gate."""
    store = ProjectSwarmStore(tmp_path)
    run = _run(tmp_path)
    kernel = _Kernel()
    adapter = NovaSwarmAdapter(kernel, PolicyGate(store), enabled=True)

    result = adapter.execute_suggestion(_suggestion(), run)

    assert result.executed is True
    assert result.swarm_decision is not None
    assert result.swarm_decision.status is PolicyStatus.ALLOWED
    assert [name for name, _payload in kernel.calls] == ["govern", "act"]


def test_swarm_policy_block_never_calls_nova_or_a_legacy_fallback(tmp_path: Path):
    """Catches a Swarm policy block falling through to an old Nova action path."""
    store = ProjectSwarmStore(tmp_path)
    run = _run(tmp_path, autonomy="observe")
    kernel = _Kernel()
    adapter = NovaSwarmAdapter(kernel, PolicyGate(store), enabled=True)

    result = adapter.execute_suggestion(_suggestion(), run)

    assert result.executed is False
    assert result.swarm_decision is not None
    assert result.swarm_decision.status is PolicyStatus.BLOCKED
    assert result.reason == "observe_only"
    assert kernel.calls == []


def test_nova_governance_block_prevents_act_without_fallback(tmp_path: Path):
    """Catches calling EntityKernel.act after its own governance decision blocks."""
    store = ProjectSwarmStore(tmp_path)
    run = _run(tmp_path)
    kernel = _Kernel(allowed=False)
    adapter = NovaSwarmAdapter(kernel, PolicyGate(store), enabled=True)

    result = adapter.execute_suggestion(_suggestion(), run)

    assert result.executed is False
    assert result.reason == "nova_policy_blocked"
    assert [name for name, _payload in kernel.calls] == ["govern"]


def test_nova_kernel_receives_the_policy_bound_intent_snapshot(tmp_path: Path):
    """Catches a nested mutable suggestion changing after Swarm authorization."""
    store = ProjectSwarmStore(tmp_path)
    run = _run(tmp_path)
    suggestion = _suggestion(proposal_id="snapshot-bound")
    suggestion["payload"] = {"nested": {"content": "approved draft"}}

    class _MutatingGate(PolicyGate):
        def authorize_and_claim(self, proposal, run, capabilities):
            decision = super().authorize_and_claim(proposal, run, capabilities)
            suggestion["payload"]["nested"]["content"] = "not the approved draft"
            return decision

    kernel = _Kernel()
    adapter = NovaSwarmAdapter(kernel, _MutatingGate(store), enabled=True)

    result = adapter.execute_suggestion(suggestion, run)

    assert result.executed is True
    assert kernel.calls[0][1]["payload"]["nested"]["content"] == "approved draft"
