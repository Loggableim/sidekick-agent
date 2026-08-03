"""Full three-Space readiness-chain audit remains red/fail-closed."""

from __future__ import annotations

import hashlib
from pathlib import Path

from nova.live_test_gate import audit_three_space_readiness


def test_three_space_readiness_audit_reports_trust_provider_and_paused_status(tmp_path: Path) -> None:
    roots = {slug: tmp_path / slug for slug in ("nova", "finanz-junkie", "aquarium-zentrum")}
    for root in roots.values(): root.mkdir(parents=True)
    aquarium = roots["aquarium-zentrum"]
    env = {
        "SIDEKICK_NOVA_LIVE_TEST_SPACE": "aquarium-zentrum",
        "SIDEKICK_NOVA_LIVE_TEST_ENABLED": "1",
        "SIDEKICK_NOVA_LIVE_TEST_SPACE_ID": "a" * 32,
        "SIDEKICK_NOVA_LIVE_TEST_ROOT": str(aquarium),
        "SIDEKICK_NOVA_LIVE_TEST_ROOT_FINGERPRINT": hashlib.sha256(str(aquarium.resolve()).encode()).hexdigest(),
    }
    calls: list[str] = []
    first = audit_three_space_readiness(
        roots,
        listener_ready=lambda: calls.append("listener") or True,
        provider_ready=lambda: calls.append("provider") or False,
        verifier_ready=lambda _root: calls.append("verifier") or True,
        environ=env,
    )
    by_space = {item["target_space"]: item for item in first["spaces"]}
    assert first["read_only"] is True and first["ready"] is False
    assert by_space["nova"]["reason"] == "test_space_not_authorized"
    assert by_space["finanz-junkie"]["reason"] == "test_space_not_authorized"
    assert by_space["aquarium-zentrum"]["reason"] == "provider_not_ready"
    assert by_space["aquarium-zentrum"]["blocking_check"] == "provider"
    assert calls == ["listener", "provider"]

    calls.clear()
    second = audit_three_space_readiness(
        roots,
        listener_ready=lambda: calls.append("listener") or False,
        provider_ready=lambda: calls.append("provider") or True,
        verifier_ready=lambda _root: calls.append("verifier") or True,
        environ=env,
    )
    by_space = {item["target_space"]: item for item in second["spaces"]}
    assert by_space["aquarium-zentrum"]["reason"] == "listener_not_ready"
    assert by_space["aquarium-zentrum"]["next_step_code"] == "verify_listener_readiness"
    assert calls == ["listener"]
