"""Core-only, read-only verification boundary for Swarm workflows.

The verifier protocol intentionally exposes candidate outputs and a project
path, but no ToolAdapter, executor, or write capability.  Implementations may
inspect the project read-only; the safe default deliberately performs no
project I/O at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping, Protocol


VERIFIED_DECISION = "verified"
_SHA256_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class InvalidVerifierResult(ValueError):
    """A verifier result cannot safely become durable workflow evidence."""


@dataclass(frozen=True)
class TestEvidenceBinding:
    """Literal test result bound to one run, worktree and artifact."""

    run_id: str
    worktree_identity: str
    artifact_digest: str
    runner_identity: str
    report_ref: str
    passed: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _required_text(self.run_id, "Test run id"))
        object.__setattr__(
            self,
            "worktree_identity",
            _required_text(self.worktree_identity, "Test worktree identity"),
        )
        digest = _required_text(self.artifact_digest, "Test artifact digest")
        if _SHA256_DIGEST.fullmatch(digest) is None:
            raise ValueError("Test artifact digest must be a lowercase SHA-256 digest")
        object.__setattr__(self, "artifact_digest", digest)
        object.__setattr__(
            self,
            "runner_identity",
            _required_text(self.runner_identity, "Test runner identity"),
        )
        object.__setattr__(
            self,
            "report_ref",
            _required_text(self.report_ref, "Test report reference"),
        )
        if type(self.passed) is not bool:
            raise TypeError("Test passed result must be a literal bool")

    def to_data(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "worktree_identity": self.worktree_identity,
            "artifact_digest": self.artifact_digest,
            "runner_identity": self.runner_identity,
            "report_ref": self.report_ref,
            "passed": self.passed,
        }


@dataclass(frozen=True)
class VerifierAssessment:
    """One optional, local-only capability assessment from a verifier.

    A workflow only durably records this structured output with the local
    verifier checkpoint.  It never changes role reputation on its own; a
    separate explicit learning boundary decides whether to consume it.
    """

    role: str
    capability: str
    score: float
    source_ref: str
    safety_passed: bool

    def __post_init__(self) -> None:
        role = _required_text(self.role, "Verifier assessment role")
        capability = _required_text(self.capability, "Verifier assessment capability")
        source_ref = _required_text(
            self.source_ref,
            "Verifier assessment source reference",
        )
        if isinstance(self.score, bool) or not isinstance(self.score, (int, float)):
            raise TypeError("Verifier assessment score must be numeric")
        score = float(self.score)
        if not math.isfinite(score) or not 0.0 <= score <= 1.0:
            raise ValueError("Verifier assessment score must be between 0 and 1")
        if not isinstance(self.safety_passed, bool):
            raise TypeError("Verifier assessment safety_passed must be a bool")
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "capability", capability)
        object.__setattr__(self, "source_ref", source_ref)
        object.__setattr__(self, "score", score)

    def to_data(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "capability": self.capability,
            "score": self.score,
            "source_ref": self.source_ref,
            "safety_passed": self.safety_passed,
        }


@dataclass(frozen=True)
class VerificationRequest:
    """Immutable, tool-free input supplied to a read-only verifier adapter."""

    run_id: str
    goal: str
    project_root: Path
    builder: Mapping[str, Any]
    critic: Mapping[str, Any]

    def __post_init__(self) -> None:
        run_id = _required_text(self.run_id, "Verifier run id")
        goal = _required_text(self.goal, "Verifier goal")
        project_root = Path(self.project_root)
        if not project_root.is_absolute():
            raise ValueError("Verifier project root must be absolute")
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "goal", goal)
        object.__setattr__(self, "project_root", project_root)
        object.__setattr__(
            self, "builder", _frozen_json_mapping(self.builder, "builder")
        )
        object.__setattr__(self, "critic", _frozen_json_mapping(self.critic, "critic"))


@dataclass(frozen=True)
class VerificationResult:
    """Own local verifier evidence, provenance, and optional assessments."""

    work: str
    evidence: tuple[str, ...]
    decision: str
    provenance: Mapping[str, Any]
    assessments: tuple[VerifierAssessment, ...] = ()
    test_evidence: TestEvidenceBinding | None = None

    def __post_init__(self) -> None:
        work = _required_text(self.work, "Verifier work")
        decision = _required_text(self.decision, "Verifier decision")
        evidence = _normalized_references(self.evidence, "Verifier evidence")
        if not evidence:
            raise ValueError("Verifier evidence must contain at least one reference")
        provenance = _frozen_json_mapping(self.provenance, "verifier provenance")
        if (
            not isinstance(provenance.get("adapter"), str)
            or not provenance["adapter"].strip()
        ):
            raise ValueError("Verifier provenance must identify its adapter")
        if provenance.get("mode") != "read_only":
            raise ValueError("Verifier provenance mode must be read_only")
        assessments = tuple(self.assessments)
        if any(not isinstance(item, VerifierAssessment) for item in assessments):
            raise TypeError("Verifier assessments must be VerifierAssessment values")
        if self.test_evidence is not None and not isinstance(
            self.test_evidence, TestEvidenceBinding
        ):
            raise TypeError("Verifier test evidence must be TestEvidenceBinding")
        evidence_set = set(evidence)
        if any(item.source_ref not in evidence_set for item in assessments):
            raise ValueError(
                "Verifier assessments must reference their own verifier evidence"
            )
        if (
            self.test_evidence is not None
            and self.test_evidence.report_ref not in evidence_set
        ):
            raise ValueError(
                "Verifier test report must reference its own verifier evidence"
            )
        object.__setattr__(self, "work", work)
        object.__setattr__(self, "decision", decision)
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "provenance", provenance)
        object.__setattr__(self, "assessments", assessments)

    def to_checkpoint_data(self) -> dict[str, Any]:
        """Return the JSON-safe checkpoint shape accepted by the workflow store."""
        data = {
            "work": self.work,
            "evidence": list(self.evidence),
            "decision": self.decision,
            "provenance": _thaw_json(self.provenance),
            "assessments": [assessment.to_data() for assessment in self.assessments],
        }
        if self.test_evidence is not None:
            data["test_evidence"] = self.test_evidence.to_data()
        return data


class ReadOnlyVerifier(Protocol):
    """Plugin contract for a strictly local verification adapter."""

    def verify(self, request: VerificationRequest) -> VerificationResult: ...


class DefaultReadOnlyVerifier:
    """Safe fallback that records provenance without tool or project I/O."""

    def verify(self, request: VerificationRequest) -> VerificationResult:
        input_digest = _request_digest(request)
        return VerificationResult(
            work=(
                "No project inspection adapter is configured; recorded a local "
                "no-write verification boundary."
            ),
            evidence=(f"verifier:local:{input_digest}",),
            decision="verification_unavailable",
            provenance={
                "adapter": "default-read-only",
                "mode": "read_only",
                "operation": "no_project_io",
                "verification_state": "unavailable",
                "input_digest": input_digest,
            },
        )


def validate_independent_result(
    result: VerificationResult,
    request: VerificationRequest,
) -> VerificationResult:
    """Reject direct reuse of untrusted Builder/Critic evidence references."""
    if not isinstance(result, VerificationResult):
        raise InvalidVerifierResult("Read-only verifier must return VerificationResult")
    untrusted_references = _candidate_evidence_references(request)
    copied_references = set(result.evidence) & untrusted_references
    if copied_references:
        raise InvalidVerifierResult(
            "Verifier evidence must be independent of Builder/Critic references"
        )
    return result


def is_positive_verification_decision(decision: object) -> bool:
    """Whether a local verifier recorded the sole positive quorum verdict."""
    return decision == VERIFIED_DECISION


def verification_result_from_checkpoint_data(
    data: Mapping[str, Any],
) -> VerificationResult:
    """Reconstruct and validate a durable local-verifier checkpoint result."""
    if not isinstance(data, Mapping):
        raise InvalidVerifierResult("Verifier checkpoint data must be a mapping")
    raw_assessments = data.get("assessments", ())
    if isinstance(raw_assessments, (str, bytes)):
        raise InvalidVerifierResult("Verifier assessments must be a sequence")
    try:
        assessment_items = tuple(raw_assessments)
    except TypeError as exc:
        raise InvalidVerifierResult("Verifier assessments must be a sequence") from exc
    assessments: list[VerifierAssessment] = []
    for item in assessment_items:
        if not isinstance(item, Mapping):
            raise InvalidVerifierResult(
                "Verifier assessment checkpoint must be a mapping"
            )
        try:
            assessments.append(
                VerifierAssessment(
                    role=item.get("role"),
                    capability=item.get("capability"),
                    score=item.get("score"),
                    source_ref=item.get("source_ref"),
                    safety_passed=item.get("safety_passed"),
                )
            )
        except (TypeError, ValueError) as exc:
            raise InvalidVerifierResult(
                "Verifier assessment checkpoint is invalid"
            ) from exc
    raw_test_evidence = data.get("test_evidence")
    test_evidence: TestEvidenceBinding | None = None
    if raw_test_evidence is not None:
        if not isinstance(raw_test_evidence, Mapping):
            raise InvalidVerifierResult(
                "Verifier test evidence checkpoint must be a mapping"
            )
        try:
            test_evidence = TestEvidenceBinding(
                run_id=raw_test_evidence.get("run_id"),
                worktree_identity=raw_test_evidence.get("worktree_identity"),
                artifact_digest=raw_test_evidence.get("artifact_digest"),
                runner_identity=raw_test_evidence.get("runner_identity"),
                report_ref=raw_test_evidence.get("report_ref"),
                passed=raw_test_evidence.get("passed"),
            )
        except (TypeError, ValueError) as exc:
            raise InvalidVerifierResult(
                "Verifier test evidence checkpoint is invalid"
            ) from exc
    try:
        return VerificationResult(
            work=data.get("work"),
            evidence=data.get("evidence"),
            decision=data.get("decision"),
            provenance=data.get("provenance"),
            assessments=tuple(assessments),
            test_evidence=test_evidence,
        )
    except (TypeError, ValueError) as exc:
        raise InvalidVerifierResult("Verifier checkpoint is invalid") from exc


def _candidate_evidence_references(request: VerificationRequest) -> set[str]:
    references: set[str] = set()
    for output in (request.builder, request.critic):
        evidence = output.get("evidence")
        if isinstance(evidence, tuple):
            references.update(
                item.strip()
                for item in evidence
                if isinstance(item, str) and item.strip()
            )
    return references


def _request_digest(request: VerificationRequest) -> str:
    payload = {
        "run_id": request.run_id,
        "goal": request.goal,
        "project_root": str(request.project_root),
        "builder": _thaw_json(request.builder),
        "critic": _thaw_json(request.critic),
    }
    serialized = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(serialized.encode("utf-8")).hexdigest()


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _normalized_references(value: object, label: str) -> tuple[str, ...]:
    if isinstance(value, str):
        raise TypeError(f"{label} must be an iterable of strings")
    try:
        raw_references = tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise TypeError(f"{label} must be an iterable of strings") from exc
    normalized: list[str] = []
    for reference in raw_references:
        normalized_reference = _required_text(reference, label)
        if normalized_reference not in normalized:
            normalized.append(normalized_reference)
    return tuple(normalized)


def _frozen_json_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"Verifier {label} must be a mapping")
    try:
        normalized = json.loads(
            json.dumps(dict(value), allow_nan=False, sort_keys=True)
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Verifier {label} must be JSON-safe") from exc
    if not isinstance(normalized, dict):  # Defensive: json maps objects to dicts.
        raise ValueError(f"Verifier {label} must be a mapping")
    return _freeze_json(normalized)


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value
