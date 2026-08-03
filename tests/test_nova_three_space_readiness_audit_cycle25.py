from pathlib import Path

from nova.live_test_gate import audit_three_space_readiness


def test_three_space_readiness_audit_is_bounded_and_read_only(tmp_path):
    roots = {name: tmp_path / name for name in ("nova", "finanz-junkie", "aquarium-zentrum")}
    before = {name: root.exists() for name, root in roots.items()}
    payload = audit_three_space_readiness(roots, environ={})
    assert payload["read_only"] is True
    assert payload["ready"] is False
    assert [item["target_space"] for item in payload["spaces"]] == ["nova", "finanz-junkie", "aquarium-zentrum"]
    assert all(item["reason"] == "test_space_not_authorized" for item in payload["spaces"])
    assert all(item["blocking_check"] == "space_binding" for item in payload["spaces"])
    assert {name: root.exists() for name, root in roots.items()} == before


def test_three_space_readiness_audit_reports_opt_in_and_listener_provider_trust_reasons(tmp_path):
    aquarium = tmp_path / "aquarium-zentrum"
    aquarium.mkdir()
    env = {
        "SIDEKICK_NOVA_LIVE_TEST_SPACE": "aquarium-zentrum",
        "SIDEKICK_NOVA_LIVE_TEST_ENABLED": "1",
        "SIDEKICK_NOVA_LIVE_TEST_SPACE_ID": "a" * 32,
        "SIDEKICK_NOVA_LIVE_TEST_ROOT": str(aquarium),
        "SIDEKICK_NOVA_LIVE_TEST_ROOT_FINGERPRINT": "0" * 64,
    }
    payload = audit_three_space_readiness(
        {"nova": tmp_path / "nova", "finanz-junkie": tmp_path / "finanz-junkie", "aquarium-zentrum": aquarium},
        listener_ready=lambda: False,
        provider_ready=lambda: True,
        verifier_ready=lambda _root: True,
        environ=env,
    )
    by_space = {item["target_space"]: item for item in payload["spaces"]}
    assert by_space["nova"]["reason"] == "test_space_not_authorized"
    assert by_space["finanz-junkie"]["reason"] == "test_space_not_authorized"
    assert by_space["aquarium-zentrum"]["reason"] == "test_root_fingerprint_mismatch"
    assert all(item["allowed"] is False for item in payload["spaces"])
    assert "SIDEKICK_NOVA_LIVE_TEST_ROOT" not in str(payload)
