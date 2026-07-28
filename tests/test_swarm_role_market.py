from __future__ import annotations

import pytest

from swarm_core.learning import GoldenResult, ReputationLedger
from swarm_core.models import ModelRegistry
from swarm_core.role_market import RoleMarket
from swarm_core.router import ModelRouter
from swarm_core.store import ProjectSwarmStore


def test_role_market_scores_healthy_curated_candidates_with_local_reputation(
    tmp_path,
):
    """Catches a market hiding either static role quality or local capability reputation."""
    ledger = ReputationLedger(ProjectSwarmStore(tmp_path))
    ledger.record_outcome(
        "planner",
        "planning",
        GoldenResult("local-verifier:planner:1", score=0.5, safety_passed=True),
        source_kind="verifier",
    )
    market = RoleMarket(
        ModelRegistry(catalog={"deepseek-v4-pro", "kimi-k2.6"}),
        ledger,
    )

    assessment = market.assess(
        "planner",
        "planning",
        requirements={"planning", "structured-output"},
    )

    assert assessment.safety_locked is True
    assert assessment.prescribed_models == ("deepseek-v4-pro", "kimi-k2.6")
    assert assessment.recommended_models == ("deepseek-v4-pro", "kimi-k2.6")
    assert [candidate.model for candidate in assessment.candidates] == [
        "deepseek-v4-pro",
        "kimi-k2.6",
    ]
    assert [candidate.health for candidate in assessment.candidates] == [
        "healthy",
        "healthy",
    ]
    assert [candidate.role_quality for candidate in assessment.candidates] == [
        1.0,
        0.9,
    ]
    assert [candidate.reputation for candidate in assessment.candidates] == [
        0.5,
        0.5,
    ]
    assert [candidate.score for candidate in assessment.candidates] == pytest.approx(
        [0.9, 0.82]
    )
    assert all(candidate.eligible for candidate in assessment.candidates)


def test_role_market_exposes_an_unhealthy_candidate_without_recommending_it(
    tmp_path,
):
    """Catches unavailable Ollama candidates being silently selected or hidden."""
    market = RoleMarket(
        ModelRegistry(catalog={"deepseek-v4-pro"}),
        ReputationLedger(ProjectSwarmStore(tmp_path)),
    )

    assessment = market.assess(
        "planner",
        "planning",
        requirements={"planning", "structured-output"},
    )

    assert assessment.recommended_models == ("deepseek-v4-pro",)
    assert [candidate.reputation for candidate in assessment.candidates] == [
        None,
        None,
    ]
    assert [candidate.score for candidate in assessment.candidates] == [1.0, 0.9]
    unavailable = next(
        candidate
        for candidate in assessment.candidates
        if candidate.model == "kimi-k2.6"
    )
    assert unavailable.health == "unavailable"
    assert unavailable.eligible is False
    assert unavailable.ineligibility_reasons == ("unhealthy",)
    assert ModelRouter(market.registry).select(
        "planner", {"planning", "structured-output"}
    ).models == ("deepseek-v4-pro",)


@pytest.mark.parametrize(
    ("role", "expected_models"),
    [
        ("planner_challenger", ("kimi-k2.6",)),
        ("planner_arbitrator", ("deepseek-v4-pro", "kimi-k2.6")),
    ],
)
def test_role_market_transparently_maps_planner_subroles_to_planning_quality(
    tmp_path,
    role: str,
    expected_models: tuple[str, ...],
):
    """Catches route-only planning subroles disappearing from the market view."""
    ledger = ReputationLedger(ProjectSwarmStore(tmp_path))
    ledger.record_outcome(
        "planner",
        "planning",
        GoldenResult("local-verifier:planner-basis", score=0.6, safety_passed=True),
        source_kind="verifier",
    )
    market = RoleMarket(
        ModelRegistry(catalog={"deepseek-v4-pro", "kimi-k2.6"}),
        ledger,
    )

    assessment = market.assess(
        role,
        "planning",
        requirements={"planning", "structured-output"},
    )

    assert assessment.quality_basis == assessment.reputation_basis == "planner"
    assert assessment.prescribed_models == expected_models
    assert assessment.recommended_models == expected_models
    assert [candidate.model for candidate in assessment.candidates] == list(
        expected_models
    )
    assert [candidate.reputation for candidate in assessment.candidates] == [
        0.6
    ] * len(expected_models)


@pytest.mark.parametrize(
    ("role", "requirements", "expected"),
    [
        ("default", set(), ("deepseek-v4-flash", "deepseek-v4-pro")),
        ("review_a", {"review", "structured-output"}, ("glm-5.2",)),
        ("review_b", {"review", "structured-output"}, ("kimi-k2.7-code",)),
        ("vision", {"vision"}, ("qwen3.5", "gemma4:31b")),
    ],
)
def test_role_market_never_changes_safety_locked_router_sequences(
    tmp_path,
    role: str,
    requirements: set[str],
    expected: tuple[str, ...],
):
    """Catches reputation ranking replacing the mandatory route/fallback order."""
    registry = ModelRegistry(
        catalog={
            "deepseek-v4-flash",
            "deepseek-v4-pro",
            "kimi-k2.6",
            "glm-5.2",
            "kimi-k2.7-code",
            "qwen3.5",
            "gemma4:31b",
        }
    )
    market = RoleMarket(registry, ReputationLedger(ProjectSwarmStore(tmp_path)))

    assessment = market.assess(role, role, requirements=requirements)

    assert assessment.safety_locked is True
    assert assessment.prescribed_models == expected
    assert ModelRouter(registry).select(role, requirements).models == expected


@pytest.mark.parametrize(
    ("role", "requirements"),
    [
        ("default", set()),
        ("scout", {"structured-output"}),
    ],
)
def test_role_market_keeps_flash_primary_and_pro_fallback_for_scout_routes(
    tmp_path,
    role: str,
    requirements: set[str],
):
    """Catches a fallback route losing its inspectable Flash-first ranking."""
    registry = ModelRegistry(
        catalog={"deepseek-v4-flash", "deepseek-v4-pro"}
    )
    market = RoleMarket(registry, ReputationLedger(ProjectSwarmStore(tmp_path)))

    assessment = market.assess(role, role, requirements=requirements)

    assert assessment.prescribed_models == (
        "deepseek-v4-flash",
        "deepseek-v4-pro",
    )
    assert assessment.recommended_models == (
        "deepseek-v4-flash",
        "deepseek-v4-pro",
    )
    assert [
        (candidate.model, candidate.provider, candidate.role_quality, candidate.eligible)
        for candidate in assessment.candidates
    ] == [
        ("deepseek-v4-flash", "ollama-cloud", 1.0, True),
        ("deepseek-v4-pro", "ollama-cloud", 0.9, True),
    ]
