"""Local, evidence-governed Swarm learning primitives."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
from typing import Iterable, Protocol, runtime_checkable

from .memory import ProjectMemory
from .store import ProjectSwarmStore


_RESULT_SOURCES = frozenset({"verifier", "golden"})


@dataclass(frozen=True)
class GoldenResult:
    """One structured, transport-free verifier or golden-task result."""

    reference: str
    score: float
    safety_passed: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "reference", _require_text(self.reference, "Reference")
        )
        _validate_score(self.score, "Golden score")
        if not isinstance(self.safety_passed, bool):
            raise TypeError("Golden safety result must be a bool")


@dataclass(frozen=True)
class VerificationAssessment:
    """One explicit, local verifier assessment eligible for reputation learning.

    This value is intentionally distinct from a model response.  Callers must
    construct it from a trusted local verifier result; no workflow or model
    output is converted to reputation automatically.
    """

    role: str
    capability: str
    source_ref: str
    score: float
    safety_passed: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", _require_text(self.role, "Role"))
        object.__setattr__(
            self,
            "capability",
            _require_text(self.capability, "Capability"),
        )
        object.__setattr__(
            self,
            "source_ref",
            _require_text(self.source_ref, "Verifier source reference"),
        )
        _validate_score(self.score, "Verifier score")
        if not isinstance(self.safety_passed, bool):
            raise TypeError("Verifier safety result must be a bool")


@runtime_checkable
class _LocalVerifierAssessment(Protocol):
    """Structural adapter boundary for the core verifier's assessment value."""

    role: str
    capability: str
    source_ref: str
    score: float
    safety_passed: bool


@dataclass(frozen=True)
class GoldenAssessment:
    """Pure summary used to decide whether a candidate may seek promotion."""

    quality: float
    baseline_quality: float
    all_safety_passed: bool
    eligible_for_promotion: bool
    references: tuple[str, ...]


@dataclass(frozen=True)
class ReputationRecord:
    result_id: str
    role: str
    capability: str
    source_kind: str
    source_ref: str
    score: float
    created_at: datetime


@dataclass(frozen=True)
class PromptCandidate:
    """A local prompt candidate that cannot activate itself."""

    candidate_id: str
    prompt_text: str
    baseline_quality: float
    status: str
    assessed_quality: float | None
    safety_passed: bool | None
    assessment_references: tuple[str, ...]
    assessment_revision: int
    assessment_digest: str | None
    human_approver_id: str | None
    approved_assessment_revision: int | None
    approved_assessment_digest: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class LessonExport:
    """A redacted, opt-in lesson payload with no provenance or raw claim fields."""

    kind: str
    statement: str


class ReputationLedger:
    """Derive one role/capability score only from deduplicated structured results."""

    def __init__(self, store: ProjectSwarmStore) -> None:
        self.store = store

    def record_outcome(
        self,
        role: str,
        capability: str,
        result: GoldenResult,
        *,
        source_kind: str,
    ) -> ReputationRecord:
        if not isinstance(result, GoldenResult):
            raise TypeError(
                "Reputation outcomes must be structured GoldenResult values"
            )
        normalized_role = _require_text(role, "Role")
        normalized_capability = _require_text(capability, "Capability")
        normalized_source_kind = _require_text(source_kind, "Result source").lower()
        if normalized_source_kind not in _RESULT_SOURCES:
            raise ValueError(
                "Reputation outcomes must come from verifier or golden results"
            )
        data, _created = self.store.record_reputation_result(
            role=normalized_role,
            capability=normalized_capability,
            source_kind=normalized_source_kind,
            source_ref=result.reference,
            score=result.score,
        )
        return _to_reputation_record(data)

    def record_local_verifier_assessment(
        self,
        assessment: _LocalVerifierAssessment,
    ) -> ReputationRecord:
        """Persist an explicitly supplied local verifier result.

        The method accepts the structural output of the core-only verifier
        without importing that module, but rejects mappings and arbitrary
        model response data.  An unsafe assessment records a zero score so it
        can never increase the market's local reputation signal.
        """
        if not isinstance(assessment, _LocalVerifierAssessment):
            raise TypeError(
                "Reputation requires an explicit local verifier assessment"
            )
        normalized = VerificationAssessment(
            role=assessment.role,
            capability=assessment.capability,
            source_ref=assessment.source_ref,
            score=assessment.score,
            safety_passed=assessment.safety_passed,
        )
        return self.record_outcome(
            normalized.role,
            normalized.capability,
            GoldenResult(
                normalized.source_ref,
                score=normalized.score if normalized.safety_passed else 0.0,
                safety_passed=normalized.safety_passed,
            ),
            source_kind="verifier",
        )

    def list(self, role: str, capability: str) -> list[ReputationRecord]:
        return [
            _to_reputation_record(data)
            for data in self.store.list_reputation_results(
                role=_require_text(role, "Role"),
                capability=_require_text(capability, "Capability"),
            )
        ]

    def score(self, role: str, capability: str) -> float | None:
        records = self.list(role, capability)
        if not records:
            return None
        return sum(record.score for record in records) / len(records)


class PromptCandidates:
    """Evaluate prompt variants locally without routing, activation, or model calls."""

    def __init__(self, store: ProjectSwarmStore) -> None:
        self.store = store

    def create(
        self,
        candidate_id: str,
        prompt_text: str,
        *,
        baseline_quality: float,
    ) -> PromptCandidate:
        _validate_score(baseline_quality, "Baseline quality")
        return _to_prompt_candidate(
            self.store.create_prompt_candidate(
                candidate_id=_require_text(candidate_id, "Candidate id"),
                prompt_text=_require_text(prompt_text, "Candidate prompt"),
                baseline_quality=baseline_quality,
            )
        )

    def get(self, candidate_id: str) -> PromptCandidate:
        candidate_id = _require_text(candidate_id, "Candidate id")
        data = self.store.get_prompt_candidate(candidate_id)
        if data is None:
            raise KeyError(f"Unknown prompt candidate: {candidate_id}")
        return _to_prompt_candidate(data)

    def evaluate(
        self,
        candidate_id: str,
        results: Iterable[GoldenResult],
    ) -> PromptCandidate:
        candidate = self.get(candidate_id)
        results = tuple(results)
        assessment = assess_golden_results(
            results,
            baseline_quality=candidate.baseline_quality,
        )
        return _to_prompt_candidate(
            self.store.record_prompt_assessment(
                candidate.candidate_id,
                quality=assessment.quality,
                safety_passed=assessment.all_safety_passed,
                references=assessment.references,
                eligible=assessment.eligible_for_promotion,
                assessment_digest=_assessment_digest(candidate, results),
            )
        )

    def approve(self, candidate_id: str, *, approver_id: str) -> PromptCandidate:
        candidate = self.get(candidate_id)
        if (
            candidate.status != "eligible"
            or candidate.assessment_revision < 1
            or candidate.assessment_digest is None
        ):
            raise PermissionError(
                "Prompt candidate requires a current eligible assessment"
            )
        data, approved = self.store.approve_prompt_candidate(
            candidate.candidate_id,
            approver_id=_require_text(approver_id, "Human approver id"),
        )
        if not approved:
            raise PermissionError(
                "Prompt candidate no longer has a current eligible assessment"
            )
        return _to_prompt_candidate(data)

    def promote(self, candidate_id: str) -> PromptCandidate:
        candidate = self.get(candidate_id)
        if candidate.status != "eligible":
            raise PermissionError("Prompt candidate is not eligible for promotion")
        if candidate.human_approver_id is None:
            raise PermissionError("Prompt candidate requires human approval")
        data, promoted = self.store.promote_prompt_candidate(candidate.candidate_id)
        if not promoted:
            raise PermissionError(
                "Prompt candidate is no longer eligible for promotion"
            )
        return _to_prompt_candidate(data)


class LessonExporter:
    """Expose only redacted opt-in lessons; this class performs no transfer or sync."""

    def __init__(self, memory: ProjectMemory) -> None:
        self.memory = memory

    def export(self, *, opt_in: bool = False) -> tuple[LessonExport, ...]:
        if opt_in is not True:
            raise PermissionError("Lesson export requires explicit opt-in=True")
        return tuple(
            LessonExport(kind=item.kind, statement=item.redacted_statement)
            for item in self.memory.list_exportable_lessons()
            if item.redacted_statement is not None
        )


def assess_golden_results(
    results: Iterable[GoldenResult],
    *,
    baseline_quality: float,
) -> GoldenAssessment:
    """Purely assess structured golden outcomes; no transport is accepted or used."""
    _validate_score(baseline_quality, "Baseline quality")
    collected = tuple(results)
    if any(not isinstance(result, GoldenResult) for result in collected):
        raise TypeError("Golden assessment requires GoldenResult values")
    references = tuple(result.reference for result in collected)
    if len(set(references)) != len(references):
        raise ValueError("Golden assessment contains duplicate references")
    if not collected:
        return GoldenAssessment(
            quality=0.0,
            baseline_quality=baseline_quality,
            all_safety_passed=False,
            eligible_for_promotion=False,
            references=(),
        )
    quality = sum(result.score for result in collected) / len(collected)
    all_safety_passed = all(result.safety_passed for result in collected)
    return GoldenAssessment(
        quality=quality,
        baseline_quality=baseline_quality,
        all_safety_passed=all_safety_passed,
        eligible_for_promotion=(all_safety_passed and quality >= baseline_quality),
        references=references,
    )


def _validate_score(score: float, label: str) -> None:
    if isinstance(score, bool) or not isinstance(score, (float, int)):
        raise ValueError(f"{label} must be a finite number in [0, 1]")
    if not math.isfinite(float(score)) or not 0.0 <= float(score) <= 1.0:
        raise ValueError(f"{label} must be a finite number in [0, 1]")


def _require_text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _assessment_digest(
    candidate: PromptCandidate,
    results: tuple[GoldenResult, ...],
) -> str:
    """Bind approval to every structured result in the current assessment."""
    payload = {
        "baseline_quality": candidate.baseline_quality,
        "candidate_id": candidate.candidate_id,
        "results": [
            {
                "reference": result.reference,
                "safety_passed": result.safety_passed,
                "score": result.score,
            }
            for result in sorted(results, key=lambda result: result.reference)
        ],
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _to_reputation_record(data: dict[str, object]) -> ReputationRecord:
    return ReputationRecord(
        result_id=str(data["result_id"]),
        role=str(data["role"]),
        capability=str(data["capability"]),
        source_kind=str(data["source_kind"]),
        source_ref=str(data["source_ref"]),
        score=float(data["score"]),
        created_at=data["created_at"],  # type: ignore[arg-type]
    )


def _to_prompt_candidate(data: dict[str, object]) -> PromptCandidate:
    return PromptCandidate(
        candidate_id=str(data["candidate_id"]),
        prompt_text=str(data["prompt_text"]),
        baseline_quality=float(data["baseline_quality"]),
        status=str(data["status"]),
        assessed_quality=(
            float(data["assessed_quality"])
            if data["assessed_quality"] is not None
            else None
        ),
        safety_passed=(
            bool(data["safety_passed"]) if data["safety_passed"] is not None else None
        ),
        assessment_references=tuple(data["assessment_references"]),  # type: ignore[arg-type]
        assessment_revision=int(data["assessment_revision"]),
        assessment_digest=(
            str(data["assessment_digest"])
            if data["assessment_digest"] is not None
            else None
        ),
        human_approver_id=(
            str(data["human_approver_id"])
            if data["human_approver_id"] is not None
            else None
        ),
        approved_assessment_revision=(
            int(data["approved_assessment_revision"])
            if data["approved_assessment_revision"] is not None
            else None
        ),
        approved_assessment_digest=(
            str(data["approved_assessment_digest"])
            if data["approved_assessment_digest"] is not None
            else None
        ),
        created_at=data["created_at"],  # type: ignore[arg-type]
        updated_at=data["updated_at"],  # type: ignore[arg-type]
    )
