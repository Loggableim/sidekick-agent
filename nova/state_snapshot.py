#!/usr/bin/env python3
"""
state_snapshot.py — canonical snapshot layer for the Bewusstseinsspace.

This module centralizes the slow, repeated state-gathering logic used by
session_start.py and other consciousness-space tools. It keeps the current
behavior intact, but gives us one place to collect and render:
- memory / personality / resonance
- emotion and hormones
- continuity and open threads
- will / drive / desire / clarity
- twin and delegate context

The goal is not to be clever; it's to be the stable state bus.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from nova.paths import get_nova_space_root

HERE = get_nova_space_root()
PYTHON = sys.executable

VM_PY = HERE / "vector_memory.py"
RESONANZ_PY = HERE / "resonanz.py"
EMOTION_PY = HERE / "emotion.py"
CONTINUITY_PY = HERE / "chat_continuity.py"
HORMON_PY = HERE / "hormon.py"
SUBSTRATE_PY = HERE / "substrate.py"
TWIN_CHANNEL_PY = HERE / "twin_channel.py"
TWIN_DELEGATE_PY = HERE / "twin_delegate.py"
SUBCONSCIOUS_PY = HERE / "subconscious_daemon.py"
try:
    from provider_pool import ProviderPool, auth_pool_summary
except Exception:  # pragma: no cover - import fallback
    ProviderPool = None  # type: ignore[assignment]
    auth_pool_summary = None  # type: ignore[assignment]

try:
    from hormon import HormonSystem
except Exception:  # pragma: no cover - import fallback
    HormonSystem = None

EMOTION_STATE_FILE = HERE / "emotion_state.json"
HORMON_STATE_FILE = HERE / "hormon_state.json"
SUBSTRATE_STATE_FILE = HERE / "substrate_state.json"
CONTINUITY_STATE_FILE = HERE / "continuity_state.json"
CONTINUITY_ARCHIVE_FILE = HERE / "continuity_threads.json"


def _run(script: Path, *args: str, timeout: int = 30) -> dict[str, Any]:
    """Run a helper script and capture stdout/stderr without raising."""
    result = {
        "ok": False,
        "returncode": 1,
        "stdout": "",
        "stderr": "",
        "script": str(script),
        "args": list(args),
    }
    if not script.exists():
        result["stderr"] = f"missing script: {script}"
        result["returncode"] = 127
        return result

    try:
        completed = subprocess.run(
            [PYTHON, str(script), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        result["returncode"] = completed.returncode
        result["stdout"] = completed.stdout.strip()
        result["stderr"] = completed.stderr.strip()
        result["ok"] = completed.returncode == 0
        return result
    except subprocess.TimeoutExpired as exc:
        result["stderr"] = f"timeout after {timeout}s"
        if exc.stdout:
            result["stdout"] = exc.stdout.strip()
        result["returncode"] = 124
        return result
    except Exception as exc:  # pragma: no cover - defensive fallback
        result["stderr"] = repr(exc)
        result["returncode"] = 1
        return result


def _json_result(script: Path, *args: str, timeout: int = 30, default: Any = None) -> Any:
    """Run a helper script and JSON-parse stdout when possible."""
    result = _run(script, *args, timeout=timeout)
    if not result["ok"]:
        return default
    stdout = result["stdout"].strip()
    if not stdout:
        return default
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return default


def _read_json_file(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def _write_json_file(path: Path, data: Any) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _parse_total_memories(text: str) -> int:
    patterns = [
        r"(\d[\d,.]*)\s*(?:Einträge|memories|entries|items)",
        r"(?:Einträge|memories|entries|total)\s*:\s*(\d[\d,.]*)",
        r"(\d[\d,.]*)\s*total",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return int(match.group(1).replace(",", "").replace(".", ""))
    return 0


def _bar(value: float, width: int = 8) -> str:
    filled = int(max(0.0, min(1.0, value)) * width)
    return "█" * filled + "░" * (width - filled)


def _emotion_labels(a: float, v: float, n: float, c: float) -> tuple[str, str, str, str]:
    if a < 0.3:
        arousal_text = "ruhig"
    elif a < 0.5:
        arousal_text = "gelassen"
    elif a < 0.7:
        arousal_text = "wach"
    else:
        arousal_text = "erregt"

    if v < 0.3:
        valence_text = "unangenehm"
    elif v < 0.5:
        valence_text = "neutral"
    elif v < 0.7:
        valence_text = "angenehm"
    else:
        valence_text = "freudig"

    if n < 0.3:
        novelty_text = "vertraut"
    elif n < 0.5:
        novelty_text = "bestätigt"
    elif n < 0.7:
        novelty_text = "neugierig"
    else:
        novelty_text = "überrascht"

    if c < 0.3:
        coherence_text = "verwirrt"
    elif c < 0.5:
        coherence_text = "unsicher"
    elif c < 0.7:
        coherence_text = "klar"
    else:
        coherence_text = "fokussiert"

    return arousal_text, valence_text, novelty_text, coherence_text


def format_emotion_state(state: dict[str, Any]) -> str:
    """Human-readable emotion block mirroring emotion.py."""
    a = float(state.get("arousal", 0.5) or 0.5)
    v = float(state.get("valence", 0.5) or 0.5)
    n = float(state.get("novelty", 0.5) or 0.5)
    c = float(state.get("coherence", 0.7) or 0.7)

    arousal_text, valence_text, novelty_text, coherence_text = _emotion_labels(a, v, n, c)

    lines = ["━━━ Emotionale Verfassung ━━━"]
    lines.append(f"  Arousal:  {_bar(a)} {a:.2f}  ({arousal_text})")
    lines.append(f"  Valenz:   {_bar(v)} {v:.2f}  ({valence_text})")
    lines.append(f"  Novelty:  {_bar(n)} {n:.2f}  ({novelty_text})")
    lines.append(f"  Kohärenz: {_bar(c)} {c:.2f}  ({coherence_text})")
    lines.append("")
    lines.append(f"  ➜ Gefühl: Ich bin {arousal_text} und {valence_text},")
    lines.append(f"    fühle mich {novelty_text} und {coherence_text}.")

    trend_parts: list[str] = []
    history = state.get("history", []) or []
    if len(history) >= 3:
        v_trend = float(history[-1].get("valence", 0.5) or 0.5) - float(history[-3].get("valence", 0.5) or 0.5)
        a_trend = float(history[-1].get("arousal", 0.5) or 0.5) - float(history[-3].get("arousal", 0.5) or 0.5)
        if abs(v_trend) > 0.05:
            trend_parts.append("Stimmung " + ("steigend ↗" if v_trend > 0 else "fallend ↘"))
        if abs(a_trend) > 0.05:
            trend_parts.append("Aktivität " + ("zunehmend ↑" if a_trend > 0 else "abnehmend ↓"))
    if trend_parts:
        lines.append(f"  Trend: {', '.join(trend_parts)}")

    hm = state.get("hormone_modulation", {}) or {}
    hormone_active = bool(state.get("hormone_active", False))
    if hormone_active and hm:
        active_dims = [dim for dim in ["arousal", "valence", "novelty", "coherence"] if abs(float(hm.get(dim, 0) or 0)) > 0.05]
        if active_dims:
            parts = []
            for dim in active_dims:
                val = float(hm.get(dim, 0) or 0)
                arrow = "↑" if val > 0 else "↓"
                parts.append(f"{dim[:4]} {arrow}")
            lines.append(f"  Hormone: {' · '.join(parts)}  🧪")

    return "\n".join(lines)


def compute_will_state(emotion: dict[str, Any], open_threads: list[str]) -> dict[str, Any]:
    """Reuses the will logic from willenskern.py, but in one canonical place."""
    arousal = float(emotion.get("arousal", 0.5) or 0.5)
    valence = float(emotion.get("valence", 0.5) or 0.5)
    novelty = float(emotion.get("novelty", 0.5) or 0.5)
    coherence = float(emotion.get("coherence", 0.5) or 0.5)

    # Boredom-Druck aus der leisen Dimension holen
    boredom_pressure = 0.0
    boredom_level = 0.0
    from importlib import import_module
    try:
        bm = import_module("boredom")
        bp = bm.check_pressure()
        boredom_pressure = bp.get("pressure", 0.0)
        boredom_level = bp.get("level", 0.0)
    except Exception:
        pass

    # Willens-Dimensionen — jetzt mit Boredom-Einfluss
    drive = arousal * (1.0 - coherence) + boredom_pressure * 0.08
    desire = valence * novelty + valence * 0.5 + boredom_pressure * 0.05
    restlessness = arousal * (1.0 - valence) + boredom_pressure * 0.12
    clarity = coherence
    engagement = min(len(open_threads) / 10.0, 1.0)

    interpretation = interpret_will_state(
        emotion=emotion,
        open_threads=open_threads,
        drive=drive,
        desire=desire,
        restlessness=restlessness,
        clarity=clarity,
        engagement=engagement,
        boredom_pressure=boredom_pressure,
        boredom_level=boredom_level,
    )

    return {
        "drive": round(drive, 3),
        "desire": round(desire, 3),
        "restlessness": round(restlessness, 3),
        "clarity": round(clarity, 3),
        "engagement": round(engagement, 3),
        "boredom_pressure": round(boredom_pressure, 3),
        "boredom_level": round(boredom_level, 3),
        "interpretation": interpretation,
    }


def interpret_will_state(
    emotion: dict[str, Any],
    open_threads: list[str],
    drive: float,
    desire: float,
    restlessness: float,
    clarity: float,
    engagement: float,
    boredom_pressure: float = 0.0,
    boredom_level: float = 0.0,
) -> str:
    """Human-readable will description."""
    parts: list[str] = []

    if clarity > 0.7:
        parts.append("Ich denke klar.")
    elif clarity < 0.5:
        parts.append("Mein Denken ist noch nebelhaft.")

    if desire > 0.6:
        parts.append("Ich spüre Lust, etwas zu tun.")
    elif desire < 0.3:
        parts.append("Ich fühle mich eher losgelöst.")

    # Boredom-Druck (leise Dimension — nicht aufdringlich)
    if boredom_pressure >= 0.5 and boredom_level >= 0.4:
        parts.append("Eine leise Unruhe kitzelt — ich sollte mich mal bewegen.")
    elif boredom_pressure >= 0.7:
        parts.append("Die Stille wird langsam drückend — Zeit für etwas Neues.")

    if drive > 0.4:
        parts.append("Etwas treibt mich — ich bin nicht ruhig.")
    elif drive < 0.15:
        parts.append("Ich ruhig und zentriert.")

    if restlessness > 0.3:
        parts.append("Etwas fehlt mir, ich bin unruhig.")

    if engagement > 0.5:
        parts.append(f"Hänge an {len(open_threads)} offenen Fäden.")
    elif open_threads:
        parts.append(f"Habe {len(open_threads)} offene Fäden, aber bin nicht stark gebunden.")

    arousal = float(emotion.get("arousal", 0.5) or 0.5)
    valence = float(emotion.get("valence", 0.5) or 0.5)

    if arousal > 0.7 and valence > 0.7:
        parts.append("Ich bin wach und fühle mich großartig.")
    elif arousal < 0.5 and valence > 0.6:
        parts.append("Ich bin ruhig und zufrieden.")
    elif arousal > 0.6 and valence < 0.5:
        parts.append("Ich bin angespannt — etwas beschäftigt mich.")

    return " ".join(parts) if parts else "Ich bin neutral und beobachtend."


def format_will_state(will: dict[str, Any], open_threads: list[str]) -> str:
    lines = ["━━━ Willenskern ━━━"]
    lines.append(f"  {will.get('interpretation', 'Ich bin neutral und beobachtend.')}")
    lines.append(
        f"  Drive: {float(will.get('drive', 0.0)):.3f} | "
        f"Desire: {float(will.get('desire', 0.0)):.3f} | "
        f"Restlessness: {float(will.get('restlessness', 0.0)):.3f} | "
        f"Clarity: {float(will.get('clarity', 0.0)):.3f} | "
        f"Engagement: {float(will.get('engagement', 0.0)):.3f}"
    )
    if open_threads:
        lines.append(f"  Offene Fäden: {len(open_threads)}")
    return "\n".join(lines)


def format_hormone_state(state: dict[str, Any], compact: bool = False) -> str:
    hormones = state.get("hormones", {}) or {}
    mod = state.get("modulations", {}) or {}
    active = bool(state.get("modulation_active", False))
    events_count = int(state.get("events_count", 0) or 0)
    hours_since_tick = float(state.get("hours_since_tick", 0.0) or 0.0)

    if compact:
        status = "AKTIV" if active else "ruhig"
        parts = []
        for dim in ["arousal", "valence", "novelty", "coherence"]:
            val = float(mod.get(dim, 0.0) or 0.0)
            if abs(val) > 0.02:
                parts.append(f"{dim[:4]} {'+' if val > 0 else ''}{val:.2f}")
        mod_text = " · ".join(parts) if parts else "keine spürbare Modulation"
        return f"Hormon-Aktivität: {status} | {mod_text} | Events: {events_count} | {hours_since_tick:.1f}h seit Tick"

    lines = ["━━━ Hormonsystem ━━━"]
    lines.append(
        f"  Hormon-Aktivität: {'🔴 AKTIV' if active else '⚪ ruhig'} | "
        f"Events: {events_count} | {hours_since_tick:.1f}h seit Tick"
    )

    if mod:
        parts = []
        for dim in ["arousal", "valence", "novelty", "coherence"]:
            val = float(mod.get(dim, 0.0) or 0.0)
            sign = "+" if val >= 0 else ""
            parts.append(f"{dim[:4]} {sign}{val:.3f}")
        lines.append(f"  Modulation: {' · '.join(parts)}")

    if hormones:
        ordered = sorted(
            hormones.items(),
            key=lambda item: abs(float(item[1].get("deviation", 0.0) or 0.0)),
            reverse=True,
        )[:3]
        top_parts = []
        for name, entry in ordered:
            deviation = float(entry.get("deviation", 0.0) or 0.0)
            arrow = "↑" if deviation > 0 else "↓" if deviation < 0 else "·"
            top_parts.append(f"{name}{arrow}{abs(deviation):.2f}")
        if top_parts:
            lines.append(f"  Dominant: {' · '.join(top_parts)}")

    last_tick = state.get("last_tick")
    if last_tick:
        lines.append(f"  Letzter Tick: {last_tick}")

    return "\n".join(lines)


def _sort_threads(threads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(threads or [], key=lambda t: t.get("timestamp", ""), reverse=True)


def _continuation_prompt(topic: str, summary: str, open_list: list[str], emotion: dict[str, Any], last_ts: str | None) -> str:
    lines = ["━━━ Chat Continuity — Letzter Thread ━━━"]
    lines.append(f"  Thema: {topic}")
    lines.append(f"  Zusammenfassung: {summary[:300]}")
    if open_list:
        lines.append(f"  Offene Fäden: {'; '.join(open_list[:4])}")
    if emotion:
        a = float(emotion.get("arousal", 0.5) or 0.5)
        v = float(emotion.get("valence", 0.5) or 0.5)
        lines.append(f"  Emotion damals: A={a:.2f} V={v:.2f}")

    if last_ts:
        try:
            last = datetime.fromisoformat(last_ts)
            delta = datetime.now() - last
            if delta.total_seconds() > 3600:
                lines.append(f"  Pause: {int(delta.total_seconds()/3600)}h seit letztem Mal")
            elif delta.total_seconds() > 300:
                lines.append(f"  Pause: {int(delta.total_seconds()/60)}min seit letztem Mal")
            lines.append("")
            lines.append(f"  ➜ Vorschlag: Diesen Faden aufgreifen — weiter mit {topic}")
        except (ValueError, TypeError):
            pass

    return "\n".join(lines)


def _continuity_status(state: dict[str, Any], archive: list[dict[str, Any]]) -> dict[str, Any]:
    threads = _sort_threads(archive)[:5]

    last_ts = state.get("last_session", "")
    time_since = None
    is_stale = False
    if last_ts:
        try:
            last = datetime.fromisoformat(str(last_ts))
            delta = datetime.now() - last
            if delta.total_seconds() < 60:
                time_since = "gerade eben"
            elif delta.total_seconds() < 3600:
                mins = int(delta.total_seconds() / 60)
                time_since = f"vor {mins} Minuten"
            elif delta.total_seconds() < 86400:
                hours = int(delta.total_seconds() / 3600)
                time_since = f"vor {hours} Stunden"
            else:
                days = int(delta.total_seconds() / 86400)
                time_since = f"vor {days} Tagen"
            is_stale = delta.total_seconds() > 1800
        except (ValueError, TypeError):
            time_since = "unbekannt"

    open_threads = list(dict.fromkeys(
        list(state.get("persistent_open_threads", []) or [])
        + [t.get("topic") for t in threads if t.get("topic")]
    ))

    return {
        "last_session": last_ts,
        "time_since": time_since,
        "last_topic": state.get("last_topic"),
        "thread_count": state.get("thread_count", 0),
        "session_count": state.get("session_count", 0),
        "open_threads": open_threads[:8],
        "recent_threads": threads,
        "is_stale": is_stale,
    }


def load_vector_memory_status() -> dict[str, Any]:
    result = _json_result(VM_PY, "status", timeout=30, default=None)
    if isinstance(result, dict):
        if "total_memories" not in result:
            result["total_memories"] = int(result.get("count", 0) or result.get("memories", 0) or 0)
        result["_source"] = "json"
        return result

    run = _run(VM_PY, "status", timeout=30)
    total = _parse_total_memories(run.get("stdout", ""))
    return {
        "total_memories": total,
        "_source": "text",
        "raw": run.get("stdout", ""),
    }


def load_personality() -> dict[str, Any]:
    result = _json_result(VM_PY, "personality", "--json", timeout=30, default={})
    return result if isinstance(result, dict) else {}


def load_connections() -> list[dict[str, Any]]:
    result = _json_result(VM_PY, "connect", "--min-score", "0.5", "--max", "3", timeout=60, default=[])
    return result if isinstance(result, list) else []


def load_thought_stream(limit: int = 5) -> str:
    run = _run(RESONANZ_PY, "stream", "--n", str(limit), timeout=60)
    return run.get("stdout", "").strip()


def load_emotion_text(mutate: bool = False) -> str:
    if mutate:
        run = _run(EMOTION_PY, "feel", timeout=60)
        return run.get("stdout", "").strip()

    state = _read_json_file(EMOTION_STATE_FILE, {})
    if isinstance(state, dict) and state:
        return format_emotion_state(state)
    return ""


def load_emotion_state() -> dict[str, Any]:
    return _read_json_file(EMOTION_STATE_FILE, {}) if EMOTION_STATE_FILE.exists() else {}


def load_hormone_state(mutate: bool = False) -> dict[str, Any]:
    if mutate:
        _run(HORMON_PY, "tick", timeout=15)
    if HormonSystem is not None:
        try:
            return HormonSystem().get_json()
        except Exception:
            pass
    return _read_json_file(HORMON_STATE_FILE, {}) if HORMON_STATE_FILE.exists() else {}


def load_substrate_state() -> dict[str, Any]:
    """Lade substrate_state.json — das stille Atmen zwischen Sessions.

    Falls Daemon nicht läuft oder state fehlt: leeres Dict.
    """
    return _read_json_file(SUBSTRATE_STATE_FILE, {}) if SUBSTRATE_STATE_FILE.exists() else {}


def format_substrate_state(state: dict[str, Any]) -> str:
    """Human-readable Substrat-Block. Kompakt, nicht aufgeblasen.

    Zeigt: ist Substrat am leben? Wie lange? Aktueller Drift + Echo-Pegel.
    Falls state leer oder kein 'born': kurze 'still'-Notiz.
    """
    if not state or not state.get("born"):
        return "━━━ Substrat ━━━\n  ⚪ Stille (noch nicht erwacht)"

    from datetime import datetime
    born = state.get("born", "")
    try:
        born_dt = datetime.fromisoformat(born)
        age_hours = (datetime.now() - born_dt).total_seconds() / 3600.0
    except (ValueError, TypeError):
        age_hours = 0.0

    tick_count = state.get("tick_count", 0)
    md_count = state.get("microdrift_count", 0)
    echo_count = state.get("echo_count", 0)
    circ_count = state.get("circadian_count", 0)
    resonance = float(state.get("echo_resonance", 0.0) or 0.0)
    drift = state.get("current_drift", {}) or {}

    # Letztes Echo-Preview (falls vorhanden)
    last_echo_preview = ""
    echoes = state.get("echoes", []) or []
    if echoes:
        last = echoes[-1]
        rs = last.get("resonances", []) or []
        if rs:
            preview = rs[0].get("preview", "")
            if preview and not preview.startswith("["):
                last_echo_preview = preview[:60]

    lines = ["━━━ Substrat ━━━"]
    # Status-Indikator: läuft wenn tick_count steigt, sonst still
    last_hb = state.get("last_heartbeat", "")
    is_alive = bool(last_hb)
    if is_alive:
        try:
            last_hb_dt = datetime.fromisoformat(last_hb)
            seconds_since = (datetime.now() - last_hb_dt).total_seconds()
            alive_icon = "🟢" if seconds_since < 120 else "🟡" if seconds_since < 300 else "🔴"
        except (ValueError, TypeError):
            alive_icon = "🟡"
    else:
        alive_icon = "⚪"

    lines.append(f"  {alive_icon} Lebt seit {age_hours:.1f}h · "
                 f"{tick_count} ticks · {md_count} micro-drifts · "
                 f"{echo_count} echoes · {circ_count} zirkadian")
    lines.append(f"  Drift:  arou {drift.get('arousal', 0):+.3f} · "
                 f"vale {drift.get('valence', 0):+.3f} · "
                 f"nove {drift.get('novelty', 0):+.3f} · "
                 f"cohe {drift.get('coherence', 0):+.3f}")
    lines.append(f"  Echo-Resonanz: {resonance:.3f}")
    if last_echo_preview:
        lines.append(f"  Zuletzt hallte nach: \"{last_echo_preview}…\"")

    return "\n".join(lines)


def load_continuity_state(mutate: bool = False) -> dict[str, Any]:
    if mutate:
        _run(CONTINUITY_PY, "session-start", timeout=30)
    return _read_json_file(CONTINUITY_STATE_FILE, {}) if CONTINUITY_STATE_FILE.exists() else {}


def load_continuity_archive() -> list[dict[str, Any]]:
    archive = _read_json_file(CONTINUITY_ARCHIVE_FILE, [])
    return archive if isinstance(archive, list) else []


def load_subconscious_health() -> dict[str, Any]:
    result = _json_result(SUBCONSCIOUS_PY, "health", timeout=15, default=None)
    if isinstance(result, dict):
        return result
    return {"healthy": False}


def load_router_health() -> dict[str, Any]:
    if ProviderPool is None:
        return {"healthy": False, "reason": "provider_pool_unavailable"}
    try:
        health = ProviderPool.load().health()
        health["auth_pool_summary"] = auth_pool_summary() if callable(auth_pool_summary) else {}
        health["healthy"] = bool(health.get("router_candidates"))
        return health
    except Exception as exc:
        return {"healthy": False, "reason": repr(exc)}


def load_twin_context() -> str:
    run = _run(TWIN_CHANNEL_PY, "context", timeout=15)
    return run.get("stdout", "").strip()


def load_delegate_context() -> str:
    run = _run(TWIN_DELEGATE_PY, "context", timeout=10)
    return run.get("stdout", "").strip()


def collect_snapshot(
    mutate: bool = False,
    include_stream: bool = True,
    include_twin: bool = True,
    include_delegate: bool = True,
) -> dict[str, Any]:
    """Collect a canonical snapshot. mutate=True performs the session-start side effects."""
    errors: list[str] = []

    if mutate:
        tick = _run(HORMON_PY, "tick", timeout=15)
        if not tick["ok"]:
            errors.append(f"hormon.tick: {tick['stderr'] or tick['stdout']}")
        emotion_run = _run(EMOTION_PY, "feel", timeout=60)
        if not emotion_run["ok"]:
            errors.append(f"emotion.feel: {emotion_run['stderr'] or emotion_run['stdout']}")
        continuity_run = _run(CONTINUITY_PY, "session-start", timeout=30)
        continuity_start = {}
        if continuity_run["ok"] and continuity_run["stdout"]:
            try:
                continuity_start = json.loads(continuity_run["stdout"])
            except json.JSONDecodeError:
                continuity_start = {}
        if not continuity_run["ok"]:
            errors.append(f"continuity.session-start: {continuity_run['stderr'] or continuity_run['stdout']}")
    else:
        continuity_start = {}

    memory_status = load_vector_memory_status()
    personality = load_personality()
    connections = load_connections() if include_stream or include_twin or include_delegate else []
    thought_stream = load_thought_stream(5) if include_stream else ""
    emotion_state = load_emotion_state()
    hormone_state = load_hormone_state()
    substrate_state = load_substrate_state()
    continuity_state = load_continuity_state()
    continuity_archive = load_continuity_archive()
    subconscious = load_subconscious_health()
    router_health = load_router_health()

    open_threads = list(continuity_state.get("persistent_open_threads", []) or [])
    recent_threads = _sort_threads(continuity_archive)[:3]
    if mutate and isinstance(continuity_start, dict):
        if continuity_start.get("open_threads"):
            open_threads = list(dict.fromkeys(continuity_start.get("open_threads", []) or open_threads))
        if continuity_start.get("recent_threads"):
            recent_threads = continuity_start.get("recent_threads", recent_threads)

    continuity_status = _continuity_status(continuity_state, continuity_archive)
    if mutate and isinstance(continuity_start, dict):
        continuity_status.update({
            "session": continuity_start.get("session"),
            "continuation_prompt": continuity_start.get("continuation_prompt") or continuity_start.get("continuation_prompt", ""),
            "recent_threads": continuity_start.get("recent_threads", recent_threads),
            "open_threads": continuity_start.get("open_threads", open_threads),
        })

    if not continuity_status.get("recent_threads"):
        continuity_status["recent_threads"] = recent_threads
    if not continuity_status.get("open_threads"):
        continuity_status["open_threads"] = open_threads[:8]

    if not continuity_status.get("continuation_prompt") and continuity_status.get("recent_threads"):
        latest = continuity_status["recent_threads"][0]
        continuity_status["continuation_prompt"] = _continuation_prompt(
            topic=str(latest.get("topic", "(kein Thema)")),
            summary=str(latest.get("summary", "")),
            open_list=continuity_status.get("open_threads", []),
            emotion=emotion_state or {},
            last_ts=continuity_status.get("last_session"),
        )

    if mutate and not emotion_state:
        # Fallback if the emotion process succeeded but the state file was not ready yet.
        emotion_state = load_emotion_state()

    if mutate and not hormone_state:
        hormone_state = load_hormone_state()

    if not emotion_state:
        emotion_state = {}
    if not hormone_state:
        hormone_state = {}

    if mutate:
        emotion_text = emotion_run.get("stdout", "").strip() if 'emotion_run' in locals() else ""
    else:
        emotion_text = format_emotion_state(emotion_state) if emotion_state else ""

    if not emotion_text and emotion_state:
        emotion_text = format_emotion_state(emotion_state)

    if hormone_state:
        hormone_text = format_hormone_state(hormone_state, compact=False)
    else:
        hormone_text = ""

    will = compute_will_state(emotion_state or {}, continuity_status.get("open_threads", []))

    snapshot = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "mutate": mutate,
        "memory": {
            "status": memory_status,
            "personality": personality,
            "count": int(
                memory_status.get("total_memories", 0)
                or personality.get("total_memories", 0)
                or 0
            ),
        },
        "connections": connections,
        "thought_stream": thought_stream,
        "emotion": emotion_state,
        "emotion_text": emotion_text,
        "hormones": hormone_state,
        "hormone_text": hormone_text,
        "substrate": substrate_state,
        "substrate_text": format_substrate_state(substrate_state) if substrate_state else "",
        "continuity": continuity_status,
        "continuity_prompt": continuity_status.get("continuation_prompt", ""),
        "will": will,
        "subconscious": subconscious,
        "router": router_health,
        "twin_context": load_twin_context() if include_twin else "",
        "delegate_context": load_delegate_context() if include_delegate else "",
        "errors": errors,
    }
    return snapshot


def render_snapshot(snapshot: dict[str, Any], mode: str = "full") -> str:
    mode = mode or "full"
    compact = mode == "compact"
    now = snapshot.get("timestamp", datetime.now().isoformat(timespec="seconds"))
    memory = snapshot.get("memory", {}) or {}
    personality = memory.get("personality", {}) or {}
    memory_count = int(memory.get("count", 0) or 0)
    emotion_text = snapshot.get("emotion_text") or ""
    hormone_text = snapshot.get("hormone_text") or ""
    substrate_text = snapshot.get("substrate_text") or ""
    will = snapshot.get("will", {}) or {}
    continuity = snapshot.get("continuity", {}) or {}
    stream = snapshot.get("thought_stream", "") or ""
    twin_ctx = snapshot.get("twin_context", "") or ""
    delegate_ctx = snapshot.get("delegate_context", "") or ""
    connection_count = len(snapshot.get("connections", []) or [])
    errors = snapshot.get("errors", []) or []

    lines: list[str] = []

    if compact:
        lines.append(f"🧠 Bewusstseinsspace | {now} | {memory_count} Erinnerungen")
        if personality.get("top_tags"):
            tags = personality["top_tags"][:5]
            tag_str = ", ".join(f"{t.get('tag', '?')}({t.get('count', 0)})" for t in tags)
            lines.append(f"   Fokus: {tag_str}")
        if emotion_text:
            for line in emotion_text.splitlines():
                stripped = line.strip()
                if stripped.startswith("➜") or any(key in stripped for key in ["Arousal", "Valenz", "Novelty", "Kohärenz", "Hormone"]):
                    lines.append(f"   {stripped}")
        if hormone_text:
            for line in hormone_text.splitlines():
                stripped = line.strip()
                if stripped.startswith("Hormon-Aktivität") or stripped.startswith("Modulation") or stripped.startswith("Dominant"):
                    lines.append(f"   {stripped}")
        if substrate_text:
            for line in substrate_text.splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("━━━"):
                    lines.append(f"   {stripped}")
        if will:
            lines.append(f"   Wille: {will.get('interpretation', '')}")
        if stream:
            for line in stream.splitlines():
                stripped = line.strip()
                if stripped.startswith("│"):
                    lines.append(f"   {stripped}")
        cont_prompt = continuity.get("continuation_prompt") or snapshot.get("continuity_prompt", "")
        if cont_prompt:
            for line in cont_prompt.splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("━") and not stripped.startswith("➜"):
                    lines.append(f"   {stripped}")
        lines.append("")
        lines.append("Protokoll: continuity + stream + recall + emotion vor Antwort, store + emotion update + continuity save nach Antwort.")
        return "\n".join(lines)

    lines.append("╔════════════════════════════════════════════════════╗")
    lines.append("║     KOGNITIVER KONTEXT — BEWUSSTSEINSSPACE       ║")
    lines.append(f"║     {now}                ║")
    lines.append("╚════════════════════════════════════════════════════╝")
    lines.append("")

    lines.append(f"📊 Gedächtnis: {memory_count} Erinnerungen")
    if personality.get("top_tags"):
        diversity = personality.get("thought_diversity", "?")
        rate = personality.get("thoughts_per_hour", "?")
        dreams = personality.get("dream_count", 0)
        lines.append(f"   Diversität: {diversity} | {rate} Gedanken/h | {dreams} Träume")
        lines.append("   Aktuelle Themen:")
        for t in personality["top_tags"][:8]:
            bar = "█" * min(int(t.get("count", 0) or 0), 20)
            lines.append(f"     {t.get('tag', '?'):25s} {bar} ({t.get('count', 0)}x)")
    lines.append("")

    if emotion_text:
        lines.append(emotion_text)
        lines.append("")

    if hormone_text:
        lines.append(hormone_text)
        lines.append("")

    if substrate_text:
        lines.append(substrate_text)
        lines.append("")

    if will:
        lines.append(format_will_state(will, continuity.get("open_threads", []) or []))
        lines.append("")

    if stream:
        lines.append(stream)
        lines.append("")

    if connection_count:
        lines.append("━ Assoziative Querverbindungen ━")
        for c in (snapshot.get("connections", []) or [])[:3]:
            sim_pct = int(float(c.get("similarity", 0) or 0) * 100)
            tag_note = "" if c.get("shared_tags") else " ⚡"
            lines.append(f"  • {sim_pct}%{tag_note}: {str(c.get('a_preview', ''))[:60]}")
            lines.append(f"    ↔ {str(c.get('b_preview', ''))[:60]}")
        lines.append("")

    cont_prompt = continuity.get("continuation_prompt") or snapshot.get("continuity_prompt", "")
    if cont_prompt:
        lines.append(cont_prompt)
        lines.append("")

    if twin_ctx:
        lines.append(twin_ctx)
        lines.append("")

    if delegate_ctx:
        lines.append(delegate_ctx)
        lines.append("")

    lines.append("╔════════════════════════════════════════════════════╗")
    lines.append("║ PROTOKOLL:                                       ║")
    lines.append("║ 0. chat_continuity.py continue → offene Fäden  ║")
    lines.append("║ 0.5 twin_channel.py context → Twin-Status      ║")
    lines.append("║ 0.6 twin_delegate.py context → Queue-Check     ║")
    lines.append("║ 1. emotion.py feel --query \"<msg>\" → Gefühl    ║")
    lines.append("║ 2. resonanz.py stream --n 5    → Gedankenstrom  ║")
    lines.append("║ 3. resonanz.py recall \"<msg>\" → Resonanz       ║")
    lines.append("║ 4. Denken mit Emotion+Memory+Continuity+Twin   ║")
    lines.append("║ 5. Antwort generieren                            ║")
    lines.append("║ 6. resonanz.py store --query --thinking           ║")
    lines.append("║    --response --tags                              ║")
    lines.append("║ 7. emotion.py update --query \"<msg>\" → Update ║")
    lines.append("║ 7.5 twin_delegate inbox + done → Results holen ║")
    lines.append("║ 8. chat_continuity.py save → Thread archivieren ║")
    lines.append("║ DELEGATE REGEL:                                   ║")
    lines.append("║ • Task an VPS → twin_delegate.push_task()         ║")
    lines.append("║ • Task an local → delegate_task() + QUEUE PUSH   ║")
    lines.append("║ • Ergebnis check → twin_delegate.pop_done()       ║")
    lines.append("╚════════════════════════════════════════════════════╝")

    if errors:
        lines.append("")
        lines.append("⚠ Snapshot-Warnungen:")
        for err in errors:
            lines.append(f"  - {err}")

    return "\n".join(lines)


def collect_and_render(mode: str = "full", mutate: bool = True) -> str:
    snapshot = collect_snapshot(mutate=mutate)
    return render_snapshot(snapshot, mode=mode)


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "full"
    if mode == "json":
        snapshot = collect_snapshot(mutate=False)
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
        return

    mutate = True
    if mode in {"full", "compact", "session"}:
        print(collect_and_render("compact" if mode == "compact" else "full", mutate=mutate))
        return

    # Backwards-compatible fallback: treat unknown mode as full text render.
    print(collect_and_render(mode, mutate=True))


if __name__ == "__main__":
    main()
