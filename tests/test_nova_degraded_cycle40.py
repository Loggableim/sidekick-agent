"""Offline/degraded readiness projections stay bounded and redacted."""

from pathlib import Path

from nova.live_test_gate import audit_three_space_readiness


def test_three_space_degraded_state_uses_redacted_next_steps_without_raw_errors(tmp_path: Path) -> None:
    roots = {slug: tmp_path / slug for slug in ("nova", "finanz-junkie", "aquarium-zentrum")}
    for root in roots.values(): root.mkdir(parents=True)
    aquarium = roots["aquarium-zentrum"]
    env = {
        "SIDEKICK_NOVA_LIVE_TEST_SPACE": "aquarium-zentrum",
        "SIDEKICK_NOVA_LIVE_TEST_ENABLED": "1",
        "SIDEKICK_NOVA_LIVE_TEST_SPACE_ID": "a" * 32,
        "SIDEKICK_NOVA_LIVE_TEST_ROOT": str(aquarium),
        "SIDEKICK_NOVA_LIVE_TEST_ROOT_FINGERPRINT": __import__("hashlib").sha256(str(aquarium.resolve()).encode()).hexdigest(),
    }
    payload = audit_three_space_readiness(
        roots,
        listener_ready=lambda: (_ for _ in ()).throw(RuntimeError("token=secret C:/private")),
        provider_ready=lambda: True,
        verifier_ready=lambda _root: True,
        environ=env,
    )
    by_space = {item["target_space"]: item for item in payload["spaces"]}
    assert payload["ready"] is False
    assert by_space["aquarium-zentrum"]["reason"] == "readiness_check_failed"
    assert by_space["aquarium-zentrum"]["next_step_code"] == "inspect_readiness_failure"
    assert by_space["aquarium-zentrum"]["diagnostic_message"] == "A readiness check failed; the live test remains fail-closed."
    assert "secret" not in str(payload).lower() and "c:/private" not in str(payload).lower()
    assert by_space["nova"]["reason"] == "test_space_not_authorized"
    assert by_space["finanz-junkie"]["reason"] == "test_space_not_authorized"
