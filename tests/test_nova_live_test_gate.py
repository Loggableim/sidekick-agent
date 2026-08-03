from __future__ import annotations

from pathlib import Path
import hashlib

from nova.live_test_gate import evaluate_live_test_gate


def _env(root: Path, **extra: str) -> dict[str, str]:
    values = {
        "SIDEKICK_NOVA_LIVE_TEST_ENABLED": "1",
        "SIDEKICK_NOVA_LIVE_TEST_SPACE": "aquarium-zentrum",
        "SIDEKICK_NOVA_LIVE_TEST_ROOT": str(root),
        "SIDEKICK_NOVA_LIVE_TEST_SPACE_ID": "a" * 32,
        "SIDEKICK_NOVA_LIVE_TEST_ROOT_FINGERPRINT": hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest(),
    }
    values.update(extra)
    return values


def test_gate_is_inert_without_explicit_opt_in(tmp_path: Path) -> None:
    calls: list[str] = []
    result = evaluate_live_test_gate(
        "aquarium-zentrum", tmp_path,
        listener_ready=lambda: calls.append("listener") or True,
        provider_ready=lambda: calls.append("provider") or True,
        verifier_ready=lambda _root: calls.append("verifier") or True,
        environ={"SIDEKICK_NOVA_LIVE_TEST_SPACE": "aquarium-zentrum", "SIDEKICK_NOVA_LIVE_TEST_ROOT": str(tmp_path)},
    )
    assert result == type(result)(False, "explicit_opt_in_required", "aquarium-zentrum")
    assert calls == []


def test_gate_requires_exact_space_root_and_all_readiness_contracts(tmp_path: Path) -> None:
    calls: list[str] = []
    result = evaluate_live_test_gate(
        "aquarium-zentrum", tmp_path,
        listener_ready=lambda: calls.append("listener") or True,
        provider_ready=lambda: calls.append("provider") or True,
        verifier_ready=lambda _root: calls.append("verifier") or True,
        environ=_env(tmp_path),
    )
    assert result.allowed is True and result.reason == "authorized"
    assert calls == ["listener", "provider", "verifier"]
    mismatch = evaluate_live_test_gate(
        "finanzjunkie", tmp_path, environ=_env(tmp_path),
    )
    assert mismatch.reason == "test_space_not_authorized"


def test_gate_fails_closed_on_listener_provider_or_verifier(tmp_path: Path) -> None:
    for key, callback in (("listener", lambda: False), ("provider", lambda: False)):
        kwargs = {"listener_ready": lambda: True, "provider_ready": lambda: True, "verifier_ready": lambda _root: True}
        kwargs[f"{key}_ready"] = callback
        result = evaluate_live_test_gate("aquarium-zentrum", tmp_path, environ=_env(tmp_path), **kwargs)
        assert result.allowed is False
    result = evaluate_live_test_gate(
        "aquarium-zentrum", tmp_path, environ=_env(tmp_path),
        listener_ready=lambda: True, provider_ready=lambda: True, verifier_ready=lambda _root: False,
    )
    assert result.reason == "verifier_not_ready"


def test_gate_denials_expose_only_a_bounded_next_step_without_relaxing_gate(tmp_path: Path) -> None:
    result = evaluate_live_test_gate(
        "finanzjunkie",
        tmp_path,
        environ=_env(tmp_path),
    )
    assert result.allowed is False
    assert result.reason == "test_space_not_authorized"
    assert result.next_step_code == "authorize_test_space"
    # Unknown reasons remain actionable but never echo input details.
    assert type(result)(False, "provider_secret=redacted", "finanzjunkie").next_step_code == "inspect_live_test_gate"


def test_gate_public_dict_is_redacted_and_never_implies_start_permission(tmp_path: Path) -> None:
    result = evaluate_live_test_gate(
        "aquarium-zentrum", tmp_path, environ=_env(tmp_path),
        listener_ready=lambda: True, provider_ready=lambda: True,
        verifier_ready=lambda _root: True,
    )
    assert result.public_dict() == {
        "allowed": True,
        "reason": "authorized",
        "target_space": "aquarium-zentrum",
        "next_step_code": "none",
        "blocking_check": "none",
        "diagnostic_message": "Explicit live-test binding and all readiness checks passed.",
    }
    denied = evaluate_live_test_gate(
        "aquarium-zentrum", tmp_path,
        environ=_env(tmp_path, SIDEKICK_NOVA_LIVE_TEST_ENABLED="0"),
    )
    payload = denied.public_dict()
    assert payload["allowed"] is False
    assert payload["blocking_check"] == "explicit_opt_in"
    assert "SIDEKICK" not in repr(payload)
    assert str(tmp_path) not in repr(payload)


def test_gate_diagnostics_are_fixed_and_distinguish_runtime_preconditions(tmp_path: Path) -> None:
    """Health callers get actionable text without callback/provider details."""
    common = dict(environ=_env(tmp_path), listener_ready=lambda: True,
                  provider_ready=lambda: True, verifier_ready=lambda _root: True)
    cases = [
        ("explicit_opt_in_required", {"SIDEKICK_NOVA_LIVE_TEST_ENABLED": "0"},
         "Explicit live-test opt-in is not enabled."),
        ("listener_not_ready", {}, "The Sidekick listener is not ready for the live test."),
        ("provider_not_ready", {}, "The required Ollama Cloud provider chain is not ready."),
        ("verifier_not_ready", {}, "The bound deployment worker verifier is not ready."),
    ]
    for reason, env_overrides, expected in cases:
        kwargs = dict(common, environ=_env(tmp_path, **env_overrides))
        if reason == "listener_not_ready":
            kwargs["listener_ready"] = lambda: False
        elif reason == "provider_not_ready":
            kwargs["provider_ready"] = lambda: False
        elif reason == "verifier_not_ready":
            kwargs["verifier_ready"] = lambda _root: False
        result = evaluate_live_test_gate("aquarium-zentrum", tmp_path, **kwargs)
        assert result.reason == reason
        assert result.diagnostic_message == expected
        assert result.public_dict()["diagnostic_message"] == expected
        assert "SIDEKICK" not in result.diagnostic_message
        assert str(tmp_path) not in result.diagnostic_message


def test_gate_unknown_reason_uses_safe_fallback_diagnostic() -> None:
    result = type(evaluate_live_test_gate("", Path("."), environ={}))(False, "provider_secret=redacted", "finanzjunkie")
    assert result.diagnostic_message == "Live test gate is blocked; inspect the bounded readiness checks."


def test_gate_readiness_denials_have_distinct_safe_next_steps(tmp_path: Path) -> None:
    common = dict(
        environ=_env(tmp_path),
        listener_ready=lambda: True,
        provider_ready=lambda: True,
        verifier_ready=lambda _root: True,
    )
    cases = [
        ("explicit_opt_in_required", {"SIDEKICK_NOVA_LIVE_TEST_ENABLED": "0"}, "enable_explicit_live_test"),
        ("listener_not_ready", {}, "verify_listener_readiness"),
        ("provider_not_ready", {}, "verify_ollama_cloud_readiness"),
        ("verifier_not_ready", {}, "verify_deployment_worker"),
    ]
    for reason, env_overrides, next_step in cases:
        kwargs = dict(common)
        environment = _env(tmp_path, **env_overrides)
        if reason == "listener_not_ready":
            kwargs["listener_ready"] = lambda: False
        elif reason == "provider_not_ready":
            kwargs["provider_ready"] = lambda: False
        elif reason == "verifier_not_ready":
            kwargs["verifier_ready"] = lambda _root: False
        kwargs["environ"] = environment
        result = evaluate_live_test_gate("aquarium-zentrum", tmp_path, **kwargs)
        assert result.reason == reason
        assert result.next_step_code == next_step
        assert result.allowed is False

def test_gate_requires_persisted_space_identity_and_trusted_root_fingerprint(tmp_path: Path) -> None:
    missing_identity = _env(tmp_path)
    missing_identity.pop("SIDEKICK_NOVA_LIVE_TEST_SPACE_ID")
    result = evaluate_live_test_gate("aquarium-zentrum", tmp_path, environ=missing_identity)
    assert result.reason == "space_identity_required"
    assert result.next_step_code == "configure_test_space_identity"

    missing_fingerprint = _env(tmp_path)
    missing_fingerprint.pop("SIDEKICK_NOVA_LIVE_TEST_ROOT_FINGERPRINT")
    result = evaluate_live_test_gate("aquarium-zentrum", tmp_path, environ=missing_fingerprint)
    assert result.reason == "test_root_fingerprint_required"
    assert result.next_step_code == "configure_trusted_root_fingerprint"

    wrong_fingerprint = _env(tmp_path, SIDEKICK_NOVA_LIVE_TEST_ROOT_FINGERPRINT="b" * 64)
    result = evaluate_live_test_gate("aquarium-zentrum", tmp_path, environ=wrong_fingerprint)
    assert result.reason == "test_root_fingerprint_mismatch"


def test_provider_diagnostic_exception_is_redacted_and_fail_closed_without_side_effects(tmp_path: Path) -> None:
    """A provider probe failure cannot authorize or leak callback details."""
    calls: list[str] = []
    before = tuple(path.name for path in tmp_path.iterdir())

    def provider_probe() -> bool:
        calls.append("provider")
        raise RuntimeError("ollama_token=super-secret")

    result = evaluate_live_test_gate(
        "aquarium-zentrum",
        tmp_path,
        environ=_env(tmp_path),
        listener_ready=lambda: calls.append("listener") or True,
        provider_ready=provider_probe,
        verifier_ready=lambda _root: calls.append("verifier") or True,
    )

    assert result.allowed is False
    assert result.reason == "readiness_check_failed"
    assert result.blocking_check == "readiness_probe"
    assert result.diagnostic_message == "A readiness check failed; the live test remains fail-closed."
    assert "super-secret" not in repr(result.public_dict())
    assert calls == ["listener", "provider"]
    assert tuple(path.name for path in tmp_path.iterdir()) == before