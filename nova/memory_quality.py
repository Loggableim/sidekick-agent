"""Signal-quality gate for Nova's autobiographical and personality memory."""

from __future__ import annotations

import re
from hashlib import sha256
from typing import Any


AUTOMATION_MARKERS = (
    "[continuing toward your standing goal]",
    "continue working toward the active thread goal",
    "goal: lebe dein leben",
    "wakeagent",
)


def normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def content_fingerprint(value: str) -> str:
    return sha256(normalized_text(value).encode("utf-8")).hexdigest()


def assess_memory_quality(user_text: str, assistant_text: str = "", *, recent_fingerprints: set[str] | None = None) -> dict[str, Any]:
    combined = normalized_text(f"{user_text} {assistant_text}")
    fingerprint = content_fingerprint(combined)
    reasons: list[str] = []
    if not combined or len(combined) < 24:
        reasons.append("too_short")
    if combined in {"acknowledged", "verstanden", "ok", "okay", "done"}:
        reasons.append("acknowledgement_only")
    if any(marker in combined for marker in AUTOMATION_MARKERS):
        reasons.append("automation_boilerplate")
    if recent_fingerprints and fingerprint in recent_fingerprints:
        reasons.append("duplicate")
    # Repeated archive/digest wrappers are operational metadata, not lived evidence.
    if combined.count("session") + combined.count("archive") + combined.count("digest") >= 5:
        reasons.append("lifecycle_wrapper")
    high_quality = not reasons
    return {
        "high_quality": high_quality,
        "eligible_for_recall": high_quality,
        "eligible_for_personality": high_quality,
        "fingerprint": fingerprint,
        "reasons": reasons,
        "score": 1.0 if high_quality else 0.1,
    }
