"""Causal post-hoc reward evaluation keyed by intent correlation IDs."""

from __future__ import annotations

from typing import Any

from nova.autobiography import AutobiographyStore


class OutcomeEvaluator:
    def __init__(self, bio: AutobiographyStore | None = None):
        self.bio = bio or AutobiographyStore()

    @staticmethod
    def score(expected: dict[str, Any], outcome: dict[str, Any]) -> tuple[float, str]:
        status = str(outcome.get("status") or "").lower()
        effects = outcome.get("effects") or {}
        if status not in {"succeeded", "done", "success"}:
            return 0.1, "failed"
        expected_effect = expected.get("effect")
        if expected_effect and effects.get("effect") == expected_effect:
            return 0.9, "expected_effect_observed"
        if expected_effect:
            return 0.55, "succeeded_without_expected_effect_marker"
        return 0.65, "succeeded"

    def evaluate_pending(self, limit: int = 50) -> dict[str, Any]:
        evaluations = []
        for outcome in self.bio.outcomes_without_reward(limit):
            event = self.bio.event_for_correlation(str(outcome.get("correlation_id") or ""), "action")
            expected = ((event or {}).get("payload") or {}).get("expected_outcome") or {}
            reward, reason = self.score(expected, outcome)
            self.bio.update_outcome_reward(str(outcome["id"]), reward)
            self.bio.record_entity_event({
                "type": "reward", "source": "outcome_evaluator",
                "title": f"Outcome review: {outcome.get('intent_id')}",
                "summary": reason, "why": "Compare the concrete expected effect with the correlated outcome.",
                "salience": reward, "correlation_id": str(outcome.get("correlation_id") or ""),
                "payload": {"outcome_id": outcome["id"], "expected": expected, "effects": outcome.get("effects") or {}, "reward": reward},
                "tags": ["reward", reason],
            })
            evaluations.append({"outcome_id": outcome["id"], "reward": reward, "reason": reason})
        return {"evaluated": len(evaluations), "evaluations": evaluations}
