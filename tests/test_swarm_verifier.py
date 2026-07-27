from __future__ import annotations

from pathlib import Path

import pytest

from swarm_core.verifier import (
    DefaultReadOnlyVerifier,
    VerificationRequest,
    VerificationResult,
    VerifierAssessment,
)


def _request(project_root: Path) -> VerificationRequest:
    return VerificationRequest(
        run_id="run-local-verifier",
        goal="Verify without a write boundary.",
        project_root=project_root,
        builder={
            "work": "builder output",
            "evidence": ["builder:untrusted"],
            "decision": "builder approves",
        },
        critic={
            "work": "critic output",
            "evidence": ["critic:untrusted"],
            "decision": "critic approves",
        },
    )


def test_default_read_only_verifier_mints_own_provenance_without_project_write(
    tmp_path: Path,
):
    """Catches the safe fallback touching a project or copying model evidence."""
    project = tmp_path / "project"
    project.mkdir()
    marker = project / "unchanged.txt"
    marker.write_text("do not write", encoding="utf-8")

    result = DefaultReadOnlyVerifier().verify(_request(project))

    assert marker.read_text(encoding="utf-8") == "do not write"
    assert result.evidence
    assert all(reference.startswith("verifier:local:") for reference in result.evidence)
    assert not set(result.evidence) & {"builder:untrusted", "critic:untrusted"}
    assert result.provenance["adapter"] == "default-read-only"
    assert result.provenance["mode"] == "read_only"
    assert result.provenance["operation"] == "no_project_io"
    assert result.provenance["verification_state"] == "unavailable"
    assert result.decision == "verification_unavailable"
    assert result.assessments == ()


def test_verification_assessment_must_reference_owned_verifier_evidence(
    tmp_path: Path,
):
    """Catches a role score laundering a Builder/Critic reference into learning."""
    with pytest.raises(ValueError, match="own verifier evidence"):
        VerificationResult(
            work="Read-only check completed.",
            evidence=("verifier:local:checked",),
            decision="verified",
            provenance={"adapter": "test", "mode": "read_only"},
            assessments=(
                VerifierAssessment(
                    role="builder",
                    capability="building",
                    score=1.0,
                    source_ref="builder:untrusted",
                    safety_passed=True,
                ),
            ),
        )
