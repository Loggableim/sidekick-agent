"""Explicit opt-in gate for controlled Nova live-space smoke tests.

The gate is deliberately read-only. It never starts a worker, acquires a
lease, probes a provider, or opens a listener. A caller must perform those
operations only after :func:`evaluate_live_test_gate` returns ``allowed``.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import re
from typing import Callable


@dataclass(frozen=True, slots=True)
class LiveTestGateResult:
    """Redacted, deterministic result suitable for health/presence output."""

    allowed: bool
    reason: str
    target_space: str

    @property
    def next_step_code(self) -> str:
        """Return a fixed, safe operator action for a denied live gate.

        The gate intentionally remains read-only and fail-closed. This
        bounded code gives the host/UI a useful next step without exposing
        paths, provider details, or suggesting that the gate may be bypassed.
        """
        return _NEXT_STEP_BY_REASON.get(self.reason, "inspect_live_test_gate")

    @property
    def blocking_check(self) -> str:
        """Return the bounded readiness check that currently blocks startup."""
        if self.allowed:
            return "none"
        return _BLOCKING_CHECK_BY_REASON.get(self.reason, "gate")

    @property
    def diagnostic_message(self) -> str:
        """Return a fixed, redacted operator diagnosis.

        Health and presence surfaces need to explain *which* precondition is
        missing without echoing environment values, paths, provider errors,
        or callback exception text. Keep this mapping deliberately static;
        a readiness adapter must never be able to turn a secret into a
        diagnostic response.
        """
        return _DIAGNOSTIC_MESSAGE_BY_REASON.get(
            self.reason,
            "Live test gate is blocked; inspect the bounded readiness checks.",
        )

    def public_dict(self) -> dict[str, object]:
        """Serialize this read-only, redacted decision for health surfaces."""
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "target_space": self.target_space,
            "next_step_code": self.next_step_code,
            "blocking_check": self.blocking_check,
            "diagnostic_message": self.diagnostic_message,
        }


_NEXT_STEP_BY_REASON = {
    "authorized": "none",
    "test_space_not_authorized": "authorize_test_space",
    "explicit_opt_in_required": "enable_explicit_live_test",
    "space_identity_required": "configure_test_space_identity",
    "space_identity_invalid": "verify_test_space_identity",
    "test_root_not_authorized": "configure_test_root",
    "test_root_fingerprint_required": "configure_trusted_root_fingerprint",
    "test_root_fingerprint_mismatch": "verify_trusted_root_fingerprint",
    "test_root_invalid": "verify_test_root",
    "test_root_mismatch": "verify_test_root",
    "readiness_contract_missing": "configure_readiness_contract",
    "listener_not_ready": "verify_listener_readiness",
    "provider_not_ready": "verify_ollama_cloud_readiness",
    "verifier_not_ready": "verify_deployment_worker",
    "readiness_check_failed": "inspect_readiness_failure",
}

_BLOCKING_CHECK_BY_REASON = {
    "test_space_not_authorized": "space_binding",
    "explicit_opt_in_required": "explicit_opt_in",
    "space_identity_required": "space_identity",
    "space_identity_invalid": "space_identity",
    "test_root_not_authorized": "trusted_root",
    "test_root_fingerprint_required": "trusted_root_fingerprint",
    "test_root_fingerprint_mismatch": "trusted_root_fingerprint",
    "test_root_invalid": "trusted_root",
    "test_root_mismatch": "trusted_root",
    "readiness_contract_missing": "readiness_contract",
    "listener_not_ready": "listener",
    "provider_not_ready": "provider",
    "verifier_not_ready": "verifier",
    "readiness_check_failed": "readiness_probe",
}


_DIAGNOSTIC_MESSAGE_BY_REASON = {
    "authorized": "Explicit live-test binding and all readiness checks passed.",
    "test_space_not_authorized": "The requested Space is not the explicitly bound live-test Space.",
    "explicit_opt_in_required": "Explicit live-test opt-in is not enabled.",
    "space_identity_required": "The live-test Space identity is not persisted.",
    "space_identity_invalid": "The live-test Space identity has an invalid format.",
    "test_root_not_authorized": "No explicitly trusted live-test project root is configured.",
    "test_root_fingerprint_required": "The trusted project-root fingerprint is missing.",
    "test_root_fingerprint_mismatch": "The trusted project-root fingerprint does not match.",
    "test_root_invalid": "The configured live-test project root cannot be validated.",
    "test_root_mismatch": "The requested project root is not the explicitly trusted root.",
    "readiness_contract_missing": "Listener, Ollama Cloud, and deployment-verifier readiness checks are required.",
    "listener_not_ready": "The Sidekick listener is not ready for the live test.",
    "provider_not_ready": "The required Ollama Cloud provider chain is not ready.",
    "verifier_not_ready": "The bound deployment worker verifier is not ready.",
    "readiness_check_failed": "A readiness check failed; the live test remains fail-closed.",
}

_SPACE_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
_ROOT_FINGERPRINT_RE = re.compile(r"[0-9a-f]{64}\Z")

def evaluate_live_test_gate(
    target_space: str,
    project_root: Path,
    *,
    listener_ready: Callable[[], bool] | None = None,
    provider_ready: Callable[[], bool] | None = None,
    verifier_ready: Callable[[Path], bool] | None = None,
    environ: dict[str, str] | None = None,
) -> LiveTestGateResult:
    """Check an explicitly authorized test Space without side effects.

    The environment opt-in is intentionally separate from normal Space YOLO
    enrollment. Both ``SIDEKICK_NOVA_LIVE_TEST_ENABLED=1`` and an exact
    ``SIDEKICK_NOVA_LIVE_TEST_SPACE``/``..._ROOT`` binding are required.
    Readiness callbacks are supplied by the host and never invoked before the
    binding checks pass.
    """
    env = os.environ if environ is None else environ
    target = str(target_space or "").strip().lower()
    if not target or target != str(env.get("SIDEKICK_NOVA_LIVE_TEST_SPACE") or "").strip().lower():
        return LiveTestGateResult(False, "test_space_not_authorized", target)
    if str(env.get("SIDEKICK_NOVA_LIVE_TEST_ENABLED") or "").strip() != "1":
        return LiveTestGateResult(False, "explicit_opt_in_required", target)
    # Bind the explicit opt-in to the persisted Space identity as well as its slug.
    configured_space_id = str(env.get("SIDEKICK_NOVA_LIVE_TEST_SPACE_ID") or "").strip().lower()
    if not configured_space_id:
        return LiveTestGateResult(False, "space_identity_required", target)
    if _SPACE_ID_RE.fullmatch(configured_space_id) is None:
        return LiveTestGateResult(False, "space_identity_invalid", target)
    configured_root = str(env.get("SIDEKICK_NOVA_LIVE_TEST_ROOT") or "").strip()
    if not configured_root:
        return LiveTestGateResult(False, "test_root_not_authorized", target)
    configured_fingerprint = str(env.get("SIDEKICK_NOVA_LIVE_TEST_ROOT_FINGERPRINT") or "").strip().lower()
    if not configured_fingerprint:
        return LiveTestGateResult(False, "test_root_fingerprint_required", target)
    if _ROOT_FINGERPRINT_RE.fullmatch(configured_fingerprint) is None:
        return LiveTestGateResult(False, "test_root_fingerprint_mismatch", target)
    try:
        root = Path(project_root).expanduser().resolve()
        expected = Path(configured_root).expanduser().resolve()
    except (OSError, RuntimeError, TypeError, ValueError):
        return LiveTestGateResult(False, "test_root_invalid", target)
    if root != expected or not root.is_dir():
        return LiveTestGateResult(False, "test_root_mismatch", target)
    if sha256(str(root).encode("utf-8")).hexdigest() != configured_fingerprint:
        return LiveTestGateResult(False, "test_root_fingerprint_mismatch", target)
    if not callable(listener_ready) or not callable(provider_ready) or not callable(verifier_ready):
        return LiveTestGateResult(False, "readiness_contract_missing", target)
    try:
        if listener_ready() is not True:
            return LiveTestGateResult(False, "listener_not_ready", target)
        if provider_ready() is not True:
            return LiveTestGateResult(False, "provider_not_ready", target)
        if verifier_ready(root) is not True:
            return LiveTestGateResult(False, "verifier_not_ready", target)
    except Exception:
        # A readiness probe is advisory and must fail closed on every adapter
        # error without exposing provider details to the caller.
        return LiveTestGateResult(False, "readiness_check_failed", target)
    return LiveTestGateResult(True, "authorized", target)


_THREE_SPACE_TARGETS = ("nova", "finanz-junkie", "aquarium-zentrum")


def audit_three_space_readiness(
    project_roots: dict[str, Path],
    *,
    listener_ready: Callable[[], bool] | None = None,
    provider_ready: Callable[[], bool] | None = None,
    verifier_ready: Callable[[Path], bool] | None = None,
    environ: dict[str, str] | None = None,
) -> dict[str, object]:
    """Return a bounded, read-only readiness snapshot for the three Spaces.

    This is an audit only: it does not activate opt-in, start a listener,
    contact Ollama, acquire a lease, or mutate any Space/configuration. Each
    target receives its own redacted gate result so a missing trusted root or
    disabled opt-in cannot be hidden by another Space's status.
    """
    roots = project_roots if isinstance(project_roots, dict) else {}
    spaces: list[dict[str, object]] = []
    for target in _THREE_SPACE_TARGETS:
        raw_root = roots.get(target)
        root = Path(raw_root) if isinstance(raw_root, (str, Path)) else Path("")
        result = evaluate_live_test_gate(
            target,
            root,
            listener_ready=listener_ready,
            provider_ready=provider_ready,
            verifier_ready=verifier_ready,
            environ=environ,
        )
        spaces.append(result.public_dict())
    return {
        "read_only": True,
        "spaces": spaces,
        "ready": all(item["allowed"] is True for item in spaces),
        "blocking_checks": [
            {"space": item["target_space"], "check": item["blocking_check"], "reason": item["reason"]}
            for item in spaces
            if item["allowed"] is not True
        ],
    }


__all__ = ["LiveTestGateResult", "evaluate_live_test_gate", "audit_three_space_readiness"]

