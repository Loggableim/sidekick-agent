"""Extract conservative, explicit self-model evidence from lived turns."""

from __future__ import annotations

import re
from hashlib import sha256
from typing import Any


PREFERENCE_MARKERS = ("ich bevorzuge", "ich mag", "mir ist lieber", "i prefer", "i like")
OPINION_MARKERS = ("ich denke", "meiner ansicht", "für mich", "i think", "in my view")


def _sentences(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"(?<=[.!?])\s+|\n+", str(text or "")) if item.strip()]


def _key(kind: str, sentence: str) -> str:
    normalized = re.sub(r"[^a-z0-9äöüß]+", " ", sentence.lower()).strip()
    tokens = [token for token in normalized.split() if len(token) > 3 and token not in {"bevorzuge", "denke", "meiner", "ansicht", "prefer", "think"}]
    human = "_".join(tokens[:4])[:48]
    return human or sha256(normalized.encode("utf-8")).hexdigest()[:12]


def extract_self_evidence(user_text: str, assistant_text: str) -> list[dict[str, Any]]:
    """Only explicit first-person statements become evidence; no hidden inference."""
    results: list[dict[str, Any]] = []
    for sentence in _sentences(assistant_text):
        lowered = sentence.lower()
        if any(marker in lowered for marker in PREFERENCE_MARKERS):
            results.append({
                "path": f"preferences.learned.{_key('preference', sentence)}",
                "value": sentence,
                "confidence": 0.85,
                "reason": "Nova explicitly expressed a stable preference.",
            })
        elif any(marker in lowered for marker in OPINION_MARKERS):
            results.append({
                "path": f"opinions.{_key('opinion', sentence)}",
                "value": {"position": sentence, "confidence": 0.75},
                "confidence": 0.8,
                "reason": "Nova explicitly expressed an opinion.",
            })
    # The user's explicit preferences are relationship evidence, not Nova traits.
    for sentence in _sentences(user_text):
        lowered = sentence.lower()
        if any(marker in lowered for marker in PREFERENCE_MARKERS):
            results.append({
                "path": f"relationships.Cid.preference_evidence.{_key('cid_preference', sentence)}",
                "value": sentence,
                "confidence": 0.9,
                "reason": "Cid explicitly stated a preference.",
            })
    return results
