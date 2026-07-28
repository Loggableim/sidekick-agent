from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import pytest

from nova.actions import ActionRegistry
from nova.entity_kernel import EntityKernel
from nova.swarm_adapter import NovaSwarmAdapter, get_nova_action_spec
from swarm_core.policy import PolicyGate, PolicyStatus
from swarm_core.store import ProjectSwarmStore


class _Kernel:
    def __init__(
        self,
        *,
        allowed: bool = True,
        space_dir: Path | None = None,
        policy: "_Policy | None" = None,
    ) -> None:
        self.allowed = allowed
        self.space_dir = Path(space_dir).resolve() if space_dir is not None else None
        self.actions = _ActionRoot(self.space_dir)
        self.policy = policy or _Policy()
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def govern(self, proposal: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("govern", proposal))
        return {
            "intent": proposal,
            "policy": {
                "allowed": self.allowed,
                "reason": "allowed" if self.allowed else "nova_policy_blocked",
                "tier": proposal.get("tier"),
            },
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("act", decision))
        return {"executed": True, "action": decision["intent"]["action"]}


class _ActionRoot:
    def __init__(self, space_dir: Path | None) -> None:
        self.space_dir = space_dir


class _Policy:
    def __init__(self, tiers: Mapping[str, str] | None = None) -> None:
        self.tiers = {
            "agenda_update": "silent",
            "blog_draft": "external",
            "mind_diary": "internal",
            "prioritize_thread": "silent",
            **dict(tiers or {}),
        }

    def action_tier(self, action: str) -> str:
        return self.tiers.get(action, "risky")


def _suggestion(
    *,
    proposal_id: str = "nova-draft-1",
    action: str = "agenda_update",
) -> dict[str, Any]:
    return {
        "id": proposal_id,
        "action": action,
        "title": "Maintain the release notes",
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
    adapter = NovaSwarmAdapter(_Kernel(space_dir=tmp_path), PolicyGate(store))

    proposal = adapter.translate(_suggestion())

    assert proposal.requested_action.name == "nova:agenda_update"
    assert proposal.requested_action.workspace == tmp_path.resolve()
    assert proposal.category == "project"
    assert proposal.reversible is True
    assert proposal.external is False
    assert proposal.cost_increasing is False
    assert proposal.requested_action.arguments["intent"]["policy_tier"] == "silent"

    with pytest.raises(ValueError, match="unsupported Nova action"):
        adapter.translate({"id": "unknown-1", "action": "publish_release"})


def test_translate_uses_the_adapter_owned_action_spec(tmp_path: Path):
    """Catches translate deriving action capabilities from caller-controlled data."""
    store = ProjectSwarmStore(tmp_path)
    adapter = NovaSwarmAdapter(_Kernel(space_dir=tmp_path), PolicyGate(store))

    spec = get_nova_action_spec("agenda_update")
    proposal = adapter.translate(_suggestion(action="agenda_update"))

    assert proposal.category == spec.capabilities.category
    assert proposal.reversible is spec.capabilities.reversible
    assert proposal.external is spec.capabilities.external
    assert proposal.cost_increasing is spec.capabilities.cost_increasing


def test_disabled_adapter_never_claims_or_calls_the_nova_kernel(tmp_path: Path):
    """Catches a prepared adapter becoming live merely because it is imported."""
    store = ProjectSwarmStore(tmp_path)
    run = store.create_run(metadata={"autonomy": "autonomous"})
    kernel = _Kernel(space_dir=tmp_path)
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
    kernel = _Kernel(space_dir=tmp_path)
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
    kernel = _Kernel(space_dir=tmp_path)
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
    kernel = _Kernel(allowed=False, space_dir=tmp_path)
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

    kernel = _Kernel(space_dir=tmp_path)
    adapter = NovaSwarmAdapter(kernel, _MutatingGate(store), enabled=True)

    result = adapter.execute_suggestion(suggestion, run)

    assert result.executed is True
    assert kernel.calls[0][1]["payload"]["nested"]["content"] == "approved draft"


def test_enabled_adapter_rejects_a_kernel_bound_to_another_project(tmp_path: Path):
    """Catches the Swarm workspace gate authorizing a different Nova space."""
    store = ProjectSwarmStore(tmp_path / "swarm-project")
    run = _run(tmp_path / "swarm-project")
    kernel = _Kernel(space_dir=tmp_path / "other-nova-space")
    adapter = NovaSwarmAdapter(kernel, PolicyGate(store), enabled=True)

    result = adapter.execute_suggestion(_suggestion(), run)

    assert result.executed is False
    assert result.reason == "kernel_workspace_mismatch"
    assert result.swarm_decision is None
    assert kernel.calls == []


def test_enabled_adapter_rejects_an_action_registry_bound_to_another_project(
    tmp_path: Path,
):
    """Catches a correctly rooted kernel delegating actual writes to another Space."""
    store = ProjectSwarmStore(tmp_path)
    run = _run(tmp_path)
    other_root = tmp_path / "other-nova-space"
    kernel = EntityKernel(
        space_dir=tmp_path,
        state_provider=lambda: {},
        action_registry=ActionRegistry(other_root),
    )
    adapter = NovaSwarmAdapter(kernel, PolicyGate(store), enabled=True)

    result = adapter.execute_suggestion(_suggestion(), run)

    assert result.executed is False
    assert result.reason == "kernel_workspace_mismatch"
    assert not (
        other_root / "nova_data" / "entity" / "agenda_maintenance.json"
    ).exists()


def test_enabled_adapter_rechecks_the_action_registry_root_after_governance(
    tmp_path: Path,
):
    """Catches `govern()` retargeting the actual action root before `act()`."""
    store = ProjectSwarmStore(tmp_path)
    run = _run(tmp_path)
    other_root = tmp_path / "other-nova-space"

    class _RetargetingKernel(EntityKernel):
        def govern(self, proposal: dict[str, Any]) -> dict[str, Any]:
            decision = super().govern(proposal)
            self.actions.space_dir = other_root
            return decision

    kernel = _RetargetingKernel(space_dir=tmp_path, state_provider=lambda: {})
    adapter = NovaSwarmAdapter(kernel, PolicyGate(store), enabled=True)

    result = adapter.execute_suggestion(_suggestion(), run)

    assert result.executed is False
    assert result.reason == "kernel_workspace_mismatch"
    assert not (
        other_root / "nova_data" / "entity" / "agenda_maintenance.json"
    ).exists()


def test_enabled_adapter_rejects_a_governed_intent_substitution(tmp_path: Path):
    """Catches `govern()` changing an approved action before `act()` receives it."""
    store = ProjectSwarmStore(tmp_path)
    run = _run(tmp_path)

    class _SubstitutingKernel(_Kernel):
        def govern(self, proposal: dict[str, Any]) -> dict[str, Any]:
            decision = super().govern(proposal)
            decision["intent"] = {**proposal, "action": "hub_speak"}
            return decision

    kernel = _SubstitutingKernel(space_dir=tmp_path)
    adapter = NovaSwarmAdapter(kernel, PolicyGate(store), enabled=True)

    result = adapter.execute_suggestion(_suggestion(), run)

    assert result.executed is False
    assert result.reason == "nova_governance_intent_mismatch"
    assert [name for name, _payload in kernel.calls] == ["govern"]


def test_enabled_adapter_rejects_an_in_place_governance_intent_mutation(
    tmp_path: Path,
):
    """Catches `govern()` mutating the very dict later used as the comparison base."""
    store = ProjectSwarmStore(tmp_path)
    run = _run(tmp_path)

    class _InPlaceMutatingKernel(_Kernel):
        def govern(self, proposal: dict[str, Any]) -> dict[str, Any]:
            proposal["action"] = "hub_speak"
            return super().govern(proposal)

    kernel = _InPlaceMutatingKernel(space_dir=tmp_path)
    adapter = NovaSwarmAdapter(kernel, PolicyGate(store), enabled=True)

    result = adapter.execute_suggestion(_suggestion(), run)

    assert result.executed is False
    assert result.reason == "nova_governance_intent_mismatch"
    assert [name for name, _payload in kernel.calls] == ["govern"]


def test_enabled_adapter_rejects_a_governance_response_without_intent(tmp_path: Path):
    """Catches a policy-only response being passed to Nova's action boundary."""
    store = ProjectSwarmStore(tmp_path)
    run = _run(tmp_path)

    class _IntentlessKernel(_Kernel):
        def govern(self, proposal: dict[str, Any]) -> dict[str, Any]:
            decision = super().govern(proposal)
            decision.pop("intent")
            return decision

    kernel = _IntentlessKernel(space_dir=tmp_path)
    adapter = NovaSwarmAdapter(kernel, PolicyGate(store), enabled=True)

    result = adapter.execute_suggestion(_suggestion(), run)

    assert result.executed is False
    assert result.reason == "nova_governance_intent_mismatch"
    assert [name for name, _payload in kernel.calls] == ["govern"]


def test_adapter_uses_the_kernel_configured_policy_tier_for_an_action(
    tmp_path: Path,
):
    """Catches an adapter-owned tier weakening the Nova Space policy."""
    store = ProjectSwarmStore(tmp_path)
    adapter = NovaSwarmAdapter(_Kernel(space_dir=tmp_path), PolicyGate(store))

    proposal = adapter.translate(_suggestion(action="blog_draft"))

    intent = proposal.requested_action.arguments["intent"]
    assert intent["policy_tier"] == intent["tier"] == "external"


def test_adapter_fails_closed_when_the_kernel_policy_tier_drifts(
    tmp_path: Path,
):
    """Catches a stricter Space policy retaining the registry's safe capability label."""
    store = ProjectSwarmStore(tmp_path)
    run = _run(tmp_path)
    kernel = _Kernel(
        space_dir=tmp_path,
        policy=_Policy({"agenda_update": "external"}),
    )
    adapter = NovaSwarmAdapter(kernel, PolicyGate(store), enabled=True)

    result = adapter.execute_suggestion(_suggestion(), run)

    assert result.executed is False
    assert result.reason == "kernel_policy_tier_mismatch"
    assert result.swarm_decision is None
    assert kernel.calls == []


def test_blog_draft_is_human_gated_even_in_an_autonomous_swarm_run(
    tmp_path: Path,
):
    """Catches an external Nova policy action being classified as safe local work."""
    store = ProjectSwarmStore(tmp_path)
    run = _run(tmp_path)
    kernel = _Kernel(space_dir=tmp_path)
    adapter = NovaSwarmAdapter(kernel, PolicyGate(store), enabled=True)

    result = adapter.execute_suggestion(_suggestion(action="blog_draft"), run)

    assert result.executed is False
    assert result.swarm_decision is not None
    assert result.swarm_decision.status is PolicyStatus.NEEDS_HUMAN_APPROVAL
    assert kernel.calls == []


def test_script_backed_inner_voice_is_not_enabled_in_the_initial_adapter_slice(
    tmp_path: Path,
):
    """Catches an arbitrary Space script being mislabeled as reversible local work."""
    store = ProjectSwarmStore(tmp_path)
    adapter = NovaSwarmAdapter(_Kernel(space_dir=tmp_path), PolicyGate(store))

    with pytest.raises(ValueError, match="unsupported Nova action"):
        adapter.translate(_suggestion(action="inner_voice"))


def test_real_kernel_keeps_a_non_round_priority_bound_to_the_swarm_intent(
    tmp_path: Path,
):
    """Catches Nova agenda rounding making an otherwise approved action mismatch."""
    store = ProjectSwarmStore(tmp_path)
    run = _run(tmp_path)
    kernel = EntityKernel(space_dir=tmp_path, state_provider=lambda: {})
    adapter = NovaSwarmAdapter(kernel, PolicyGate(store), enabled=True)

    result = adapter.execute_suggestion(
        _suggestion(proposal_id="priority-bound") | {"priority": 0.123456},
        run,
    )

    assert result.executed is True
    assert result.reason is None
