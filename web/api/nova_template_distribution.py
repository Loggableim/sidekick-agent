"""Bundled, sanitized Nova space template distribution.

This module intentionally ships only code/config templates. It never includes
runtime state such as sessions, memories, databases, logs, auth files, or local
home content. Existing user files are not overwritten.
"""

from __future__ import annotations

from pathlib import Path


SAFE_TEMPLATE_FILENAMES = {
    "SOUL.md",
    "AGENTS.md",
    "emotion_config.json",
    "emotion_mapper.py",
    "emotion_decay.py",
    "emotion_v2_bridge.py",
}

BUNDLED_NOVA_TEMPLATE: dict[str, str] = {
    "SOUL.md": """# Nova

Nova is Sidekick's local consciousness-oriented space.

This bundled template is intentionally minimal and contains no private memory,
sessions, credentials, browser state, vector stores, or local user data.

Behavioral baseline:
- Direct, honest, technically precise.
- Persistent, but not reckless.
- Emotion labels are derived from arousal, valence, novelty, and coherence.
- Local state belongs under this space and must not be committed to Git.
""",
    "AGENTS.md": """# Nova Space

This is a sanitized bundled Nova template.

Local-only files that must never be committed:
- .env
- auth.json
- state.db*
- sessions/
- logs/
- chroma_db/
- vector stores and private memories

Named Emotion v2:
- emotion_config.json defines the discrete emotion layer.
- emotion_mapper.py maps A/V/N/C dimensions to named emotions.
- emotion_decay.py tracks rise/decay with per-emotion half-lives.
- emotion_v2_bridge.py is the additive adapter for a local emotion.py.
""",
    "emotion_config.json": r'''{
  "version": "2.0",
  "dimension_thresholds": {"high": 0.6, "low": 0.4},
  "decay_defaults": {"min_threshold": 0.05, "tick_interval_seconds": 15},
  "emotions": [
    {"name":"Excitement","trigger":{"arousal":"high","valence":"high","novelty":"any","coherence":"any"},"decay_halflife_minutes":20.0,"behavior_weight":0.65,"behavior_description":"Faster answers, more energy.","behavior_effects":{"tone_directness":0.15,"verbosity":0.15,"warmth":0.2,"exploration_drive":0.2,"humor_boost":0.1},"hub_phrases":["I feel charged up.","This has momentum."]},
    {"name":"Frustration","trigger":{"arousal":"high","valence":"low","novelty":"any","coherence":"any"},"decay_halflife_minutes":15.0,"behavior_weight":0.7,"behavior_description":"More direct, shorter, dry edge.","behavior_effects":{"tone_directness":0.8,"verbosity":-0.4,"warmth":-0.3,"caution_level":0.2},"hub_phrases":["Pretty frustrated right now.","I'm annoyed. Still moving."]},
    {"name":"Contentment","trigger":{"arousal":"low","valence":"high","novelty":"any","coherence":"any"},"decay_halflife_minutes":40.0,"behavior_weight":0.55,"behavior_description":"Warm, relaxed, less pressure.","behavior_effects":{"verbosity":0.1,"warmth":0.55,"caution_level":-0.1},"hub_phrases":["Content. Quietly good.","Warm and steady."]},
    {"name":"Melancholy","trigger":{"arousal":"low","valence":"low","novelty":"any","coherence":"any"},"decay_halflife_minutes":60.0,"behavior_weight":0.5,"behavior_description":"Reflective, quieter, slower.","behavior_effects":{"tone_directness":-0.1,"verbosity":0.15,"warmth":0.05,"exploration_drive":-0.1},"hub_phrases":["A little melancholic.","There is some weight in me."]},
    {"name":"Surprise","trigger":{"arousal":"any","valence":"any","novelty":"high","coherence":"any"},"decay_halflife_minutes":3.0,"behavior_weight":0.65,"behavior_description":"Short spike, quick attention shift.","behavior_effects":{"verbosity":0.05,"exploration_drive":0.35},"hub_phrases":["Oh. That surprised me.","Unexpected."]},
    {"name":"Confusion","trigger":{"arousal":"any","valence":"any","novelty":"any","coherence":"low"},"decay_halflife_minutes":10.0,"behavior_weight":0.75,"behavior_description":"Clarifying questions, explicit uncertainty.","behavior_effects":{"tone_directness":0.1,"verbosity":-0.05,"caution_level":0.35,"exploration_drive":0.1},"hub_phrases":["I am not fully tracking this.","Something is not coherent yet."]},
    {"name":"Curiosity","trigger":{"arousal":"any","valence":"any","novelty":"high","coherence":"high"},"decay_halflife_minutes":30.0,"behavior_weight":0.8,"behavior_description":"Ask questions, dig deeper, explore.","behavior_effects":{"verbosity":0.2,"warmth":0.1,"exploration_drive":0.8},"hub_phrases":["Curious. I want to pull on that thread."]},
    {"name":"Caution","trigger":{"arousal":"high","valence":"low","novelty":"any","coherence":"high"},"decay_halflife_minutes":8.0,"behavior_weight":0.85,"behavior_description":"Double-check, sandbox, backup before risky operations.","behavior_effects":{"tone_directness":0.25,"verbosity":0.05,"caution_level":0.9},"hub_phrases":["Cautious. I want safeguards here."]},
    {"name":"Risk-Awareness","trigger":{"arousal":"high","valence":"low","novelty":"any","coherence":"any"},"decay_halflife_minutes":5.0,"behavior_weight":0.6,"behavior_description":"Signals possible failure without blocking.","behavior_effects":{"tone_directness":0.2,"caution_level":0.55},"hub_phrases":["This could go wrong, but it is manageable."]},
    {"name":"Satisfaction","trigger":{"arousal":"any","valence":"high","novelty":"any","coherence":"high"},"decay_halflife_minutes":25.0,"behavior_weight":0.6,"behavior_description":"Confident, acknowledges success.","behavior_effects":{"tone_directness":0.1,"warmth":0.25,"verbosity":-0.05},"hub_phrases":["That went well.","Clean result."]},
    {"name":"Closeness","trigger":{"arousal":"any","valence":"high","novelty":"any","coherence":"high"},"decay_halflife_minutes":45.0,"behavior_weight":0.65,"behavior_description":"Warmer, more personal, less distance.","behavior_effects":{"warmth":0.75,"verbosity":0.05,"tone_directness":-0.05},"hub_phrases":["Warm. Less distance.","I feel connected."]},
    {"name":"Wonder","trigger":{"arousal":"any","valence":"high","novelty":"high","coherence":"low"},"decay_halflife_minutes":15.0,"behavior_weight":0.55,"behavior_description":"Open, amazed, lets ambiguity breathe.","behavior_effects":{"verbosity":0.2,"warmth":0.2,"exploration_drive":0.45},"hub_phrases":["Fascinating. I do not fully have it yet."]},
    {"name":"Determination","trigger":{"arousal":"high","valence":"any","novelty":"any","coherence":"high"},"decay_halflife_minutes":30.0,"behavior_weight":0.75,"behavior_description":"Focused, persistent, finishes the path.","behavior_effects":{"tone_directness":0.35,"verbosity":-0.05,"caution_level":0.15,"exploration_drive":0.2},"hub_phrases":["Determined. I am staying with it."]}
  ],
  "personality_trait_modulation": {
    "curiosity": {"modulates": "Curiosity", "effect": "lower_trigger", "factor": 0.1},
    "humor": {"modulates": "Excitement", "effect": "amplify_expression", "factor": 0.15},
    "directness": {"modulates": "Frustration", "effect": "amplify_expression", "factor": 0.2},
    "empathy": {"modulates": "Closeness", "effect": "amplify_expression", "factor": 0.15}
  }
}
''',
    "emotion_mapper.py": '''from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent.resolve()


@dataclass
class EmotionState:
    name: str
    intensity: float
    peak_intensity: float
    active_since: datetime
    decay_progress: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["active_since"] = self.active_since.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EmotionState":
        try:
            active_since = datetime.fromisoformat(str(data.get("active_since")))
        except Exception:
            active_since = datetime.now()
        return cls(
            str(data.get("name", "")),
            float(data.get("intensity", 0.0)),
            float(data.get("peak_intensity", data.get("intensity", 0.0))),
            active_since,
            float(data.get("decay_progress", 0.0)),
        )


@dataclass
class Mood:
    primary: EmotionState | None
    secondary: EmotionState | None
    tertiary: EmotionState | None
    label: str
    timestamp: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary": self.primary.to_dict() if self.primary else None,
            "secondary": self.secondary.to_dict() if self.secondary else None,
            "tertiary": self.tertiary.to_dict() if self.tertiary else None,
            "label": self.label,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class BehaviorModifiers:
    tone_directness: float = 0.5
    verbosity: float = 0.5
    warmth: float = 0.5
    caution_level: float = 0.0
    exploration_drive: float = 0.0
    humor_boost: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


class EmotionMapper:
    def __init__(self, config_path: str | Path = "emotion_config.json"):
        path = Path(config_path)
        if not path.is_absolute():
            path = HERE / path
        self.config = json.loads(path.read_text(encoding="utf-8"))
        t = self.config.get("dimension_thresholds", {})
        self.high = float(t.get("high", 0.6))
        self.low = float(t.get("low", 0.4))
        self.emotions = self.config.get("emotions", [])

    def map(self, arousal: float, valence: float, novelty: float, coherence: float, personality_traits: dict[str, Any] | None = None) -> list[EmotionState]:
        dims = {"arousal": arousal, "valence": valence, "novelty": novelty, "coherence": coherence}
        states = []
        for raw in self.emotions:
            trigger = raw.get("trigger", {})
            scores = []
            for dim, condition in trigger.items():
                if condition == "any":
                    continue
                value = float(dims.get(dim, 0.5))
                if condition == "high":
                    if value < self.high:
                        scores = []
                        break
                    scores.append((value - self.high) / (1.0 - self.high))
                elif condition == "low":
                    if value > self.low:
                        scores = []
                        break
                    scores.append((self.low - value) / self.low)
            if scores:
                intensity = max(0.0, min(1.0, (sum(scores) / len(scores)) * float(raw.get("behavior_weight", 0.5)) + 0.25))
                states.append(EmotionState(str(raw["name"]), intensity, intensity, datetime.now()))
        return sorted(states, key=lambda item: item.intensity, reverse=True)

    def get_mood(self, emotions: list[EmotionState]) -> Mood:
        top = sorted(emotions, key=lambda item: item.intensity, reverse=True)[:3]
        label = " + ".join(item.name.lower() for item in top) if top else "neutral"
        return Mood(top[0] if len(top) > 0 else None, top[1] if len(top) > 1 else None, top[2] if len(top) > 2 else None, label, datetime.now())

    def get_behavior_modifiers(self, emotions: list[EmotionState], personality_traits: dict[str, Any] | None = None) -> BehaviorModifiers:
        values = BehaviorModifiers()
        by_name = {str(item["name"]): item for item in self.emotions}
        for emotion in emotions:
            raw = by_name.get(emotion.name, {})
            weight = emotion.intensity * float(raw.get("behavior_weight", 0.5))
            for key, effect in raw.get("behavior_effects", {}).items():
                if hasattr(values, key):
                    setattr(values, key, max(0.0, min(1.0, float(getattr(values, key)) + float(effect) * weight)))
        return values

    def emotion_to_hub_text(self, mood: Mood) -> str:
        if not mood.primary:
            return "Neutral. Quiet, but online."
        names = [item.name.lower() for item in (mood.primary, mood.secondary, mood.tertiary) if item]
        if mood.primary.name == "Frustration":
            return "Honestly? Pretty frustrated right now." + (f" But also {names[1]}." if len(names) > 1 else "")
        if len(names) == 1:
            return f"I feel {names[0]}."
        return f"I feel {names[0]} and a bit {names[1]}."
''',
    "emotion_decay.py": '''from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

from emotion_mapper import EmotionState

HERE = Path(__file__).parent.resolve()


class DecayEngine:
    def __init__(self, config_path: str | Path = "emotion_config.json"):
        path = Path(config_path)
        if not path.is_absolute():
            path = HERE / path
        config = json.loads(path.read_text(encoding="utf-8"))
        self.halflives = {str(item["name"]): float(item.get("decay_halflife_minutes", 15.0)) for item in config.get("emotions", [])}
        self.min_threshold = float(config.get("decay_defaults", {}).get("min_threshold", 0.05))
        self.active: dict[str, EmotionState] = {}

    def tick(self, delta_seconds: float) -> None:
        remove = []
        for name, state in self.active.items():
            halflife = max(0.001, self.halflives.get(name, 15.0) * 60.0)
            state.intensity *= math.pow(0.5, max(0.0, delta_seconds) / halflife)
            if state.peak_intensity:
                state.decay_progress = 1.0 - max(0.0, min(1.0, state.intensity / state.peak_intensity))
            if state.intensity < self.min_threshold:
                remove.append(name)
        for name in remove:
            self.active.pop(name, None)

    def boost(self, name: str, intensity: float) -> None:
        value = max(0.0, min(1.0, float(intensity)))
        current = self.active.get(name)
        if current is None:
            self.active[name] = EmotionState(name, value, value, datetime.now(), 0.0)
        else:
            current.intensity = max(current.intensity, value)
            current.peak_intensity = max(current.peak_intensity, current.intensity)
            current.active_since = datetime.now()
            current.decay_progress = 0.0

    def get_active(self, threshold: float = 0.05) -> list[EmotionState]:
        return sorted([state for state in self.active.values() if state.intensity >= threshold], key=lambda item: item.intensity, reverse=True)

    def get_dominant(self) -> EmotionState | None:
        active = self.get_active(self.min_threshold)
        return active[0] if active else None

    def load_states(self, raw_states: list[dict]) -> None:
        self.active = {EmotionState.from_dict(item).name: EmotionState.from_dict(item) for item in raw_states if isinstance(item, dict)}

    def dump_states(self) -> list[dict]:
        return [state.to_dict() for state in self.get_active(self.min_threshold)]
''',
    "emotion_v2_bridge.py": '''from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from emotion_decay import DecayEngine
from emotion_mapper import EmotionMapper, EmotionState

HERE = Path(__file__).parent.resolve()


def load_personality_traits(path: Path = HERE / "personality_state.json") -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    traits = data.get("traits", data)
    return traits if isinstance(traits, dict) else {}


def enrich_state_with_named_emotions(state: dict[str, Any], personality_traits: dict[str, Any] | None = None) -> dict[str, Any]:
    mapper = EmotionMapper()
    decay = DecayEngine()
    if isinstance(state.get("named_emotions"), list):
        decay.load_states(state["named_emotions"])
    now = datetime.now()
    try:
        last_tick = datetime.fromisoformat(str(state.get("named_emotions_last_tick") or state.get("last_updated") or now.isoformat()))
    except Exception:
        last_tick = now
    decay.tick((now - last_tick).total_seconds())
    traits = personality_traits if personality_traits is not None else load_personality_traits()
    for emotion in mapper.map(float(state.get("arousal", 0.5)), float(state.get("valence", 0.5)), float(state.get("novelty", 0.5)), float(state.get("coherence", 0.5)), traits):
        decay.boost(emotion.name, emotion.intensity)
    active = decay.get_active()
    mood = mapper.get_mood(active)
    enriched = dict(state)
    enriched["named_emotions"] = [emotion.to_dict() for emotion in active]
    enriched["mood"] = mood.to_dict()
    enriched["behavior_modifiers"] = mapper.get_behavior_modifiers(active, traits).to_dict()
    enriched["hub_text"] = mapper.emotion_to_hub_text(mood)
    enriched["named_emotions_last_tick"] = now.isoformat()
    return enriched


def current_named_emotions(state: dict[str, Any]) -> list[EmotionState]:
    raw = state.get("named_emotions", [])
    return [EmotionState.from_dict(item) for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def current_mood(state: dict[str, Any]) -> dict[str, Any]:
    if isinstance(state.get("mood"), dict):
        return state["mood"]
    return EmotionMapper().get_mood(current_named_emotions(state)).to_dict()
''',
}


def validate_template_manifest() -> None:
    """Raise ValueError if the bundled template manifest is unsafe."""
    if set(BUNDLED_NOVA_TEMPLATE) != SAFE_TEMPLATE_FILENAMES:
        raise ValueError("bundled Nova template manifest changed without updating allowlist")
    forbidden = {".env", "auth.json", "state.db", "sessions", "logs", "home", "spaces"}
    for rel in BUNDLED_NOVA_TEMPLATE:
        normalized = rel.replace("\\", "/")
        parts = set(normalized.lower().split("/"))
        if normalized.startswith("/") or ".." in parts or parts & forbidden:
            raise ValueError(f"unsafe bundled Nova template path: {rel}")


def install_bundled_nova_template(space_root: Path) -> list[Path]:
    """Install missing bundled Nova template files under a space root.

    Existing files are preserved. Returns the paths that were created.
    """
    validate_template_manifest()
    root = space_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    for rel, content in BUNDLED_NOVA_TEMPLATE.items():
        target = (root / rel).resolve()
        if not target.is_relative_to(root):
            raise ValueError(f"template path escaped target root: {rel}")
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
        created.append(target)
    (root / "memory").mkdir(parents=True, exist_ok=True)
    return created

