"""
Evey Tools — Backend API für die 8787 WebUI.

Stellt alle Tool-Gruppen als Python-Funktionen bereit.
Wird von api/routes.py importiert.
"""
import hashlib
import json
import math
import os
import re
import time
from datetime import datetime
from pathlib import Path


def get_hermes_home() -> Path:
    val = (os.environ.get("SIDEKICK_HOME") or os.environ.get("HERMES_HOME") or "").strip()
    return Path(val) if val else Path.home() / ".hermes"


HERMES_HOME = get_hermes_home()
EVEY_DIR = HERMES_HOME / "workspace" / "evey"
DATA_DIR = EVEY_DIR / "data"
LOG_DIR = EVEY_DIR / "logs"
CACHE_DIR = EVEY_DIR / "cache"

for d in [EVEY_DIR, DATA_DIR, LOG_DIR, CACHE_DIR]:
    d.mkdir(parents=True, exist_ok=True)


# ── Helpers ──────────────────────────────────────────────────────────

def _rj(path: Path, default=None):
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception:
        pass
    return default if default is not None else {}


def _wj(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str))


def _aj(path: Path, entry: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _rj_lines(path: Path, limit: int = 200):
    if not path.exists():
        return []
    lines = path.read_text().strip().split("\n")
    result = []
    for line in lines[-limit:]:
        if line.strip():
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return result


def _boot_time() -> float:
    try:
        if os.name == "nt":
            import ctypes
            return time.time() - ctypes.windll.kernel32.GetTickCount64() / 1000
        import psutil
        return psutil.boot_time()
    except Exception:
        return time.time() - 86400


def _memory_pct() -> float:
    try:
        if os.name == "nt":
            import ctypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            mem = MEMORYSTATUSEX()
            mem.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem))
            return mem.dwMemoryLoad
        import psutil
        return psutil.virtual_memory().percent
    except Exception:
        return 0


# ═══════════════════════════════════════════════════════════════════════
# 1. STATUS / HEALTH
# ═══════════════════════════════════════════════════════════════════════

def get_status() -> dict:
    import platform as _p
    result = {
        "system": {
            "hostname": _p.node(),
            "platform": _p.system(),
            "platform_release": _p.release(),
            "uptime": int(time.time() - _boot_time()),
            "memory_pct": _memory_pct(),
            "cpus": os.cpu_count() or 0,
            "loadavg": _loadavg(),
        },
        "process": {
            "pid": os.getpid(),
            "uptime": int(time.time() - _proc_start),
            "version": _p.python_version(),
            "python": _p.python_version(),
            "memory_rss": _proc_rss(),
            "arch": _p.machine(),
        },
        "evey": {
            "learnings": len(_rj_lines(DATA_DIR / "learnings.jsonl")),
            "delegation_scores": len(_rj_lines(DATA_DIR / "delegation-scores.jsonl")),
        },
        "watchdog": _get_watchdog_status(),
    }
    return result


_proc_start = time.time()


def _proc_rss() -> int:
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss // (1024 * 1024)
    except Exception:
        return 0


def _loadavg():
    try:
        if os.name != "nt":
            import os as _os
            return [round(x, 2) for x in _os.getloadavg()]
    except Exception:
        pass
    return [0, 0, 0]


# ═══════════════════════════════════════════════════════════════════════
# 2. TELEMETRY
# ═══════════════════════════════════════════════════════════════════════

TELEMETRY_FILE = LOG_DIR / "events.jsonl"
MAX_TELEMETRY_SIZE = 10 * 1024 * 1024


def emit_telemetry(type_name: str, data: dict = None):
    event = {"ts": datetime.now().isoformat(), "type": type_name, **(data or {})}
    _aj(TELEMETRY_FILE, event)
    try:
        if TELEMETRY_FILE.stat().st_size > MAX_TELEMETRY_SIZE:
            rotated = TELEMETRY_FILE.with_name(f"events.{int(time.time())}.jsonl")
            TELEMETRY_FILE.rename(rotated)
            for f in sorted(LOG_DIR.glob("events.*.jsonl"))[:-5]:
                f.unlink()
    except Exception:
        pass


def query_telemetry(query_type: str = "session_metrics", limit: int = 20) -> dict:
    events = _rj_lines(TELEMETRY_FILE, 500)
    if query_type == "recent_events":
        return {"status": "ok", "count": len(events), "events": events[-limit:]}
    elif query_type == "recent_errors":
        errors = [e for e in events if e.get("type") == "error" or e.get("error")]
        return {"status": "ok", "count": len(errors), "errors": errors[-limit:]}
    elif query_type == "delegation_stats":
        dels = [e for e in events if e.get("type") == "delegation"]
        if not dels:
            return {"models": {}}
        models = {}
        for d in dels:
            m = d.get("model", "unknown")
            if m not in models:
                models[m] = {"calls": 0, "successes": 0, "total_tokens": 0}
            models[m]["calls"] += 1
            if d.get("success"):
                models[m]["successes"] += 1
            models[m]["total_tokens"] += d.get("tokens", 0)
        for m in models:
            c = models[m]["calls"]
            models[m]["success_rate"] = f"{round(models[m]['successes']/c*100)}%" if c else "0%"
        best = max(models.items(), key=lambda x: x[1]["successes"] / max(x[1]["calls"], 1)) if models else None
        rec = f"{best[0]} is most reliable" if best else "Not enough data"
        return {"models": models, "total_delegations": len(dels), "recommendation": rec}
    elif query_type == "tool_stats":
        calls = [e for e in events if e.get("type") == "tool_call"]
        if not calls:
            return {"tools": {}}
        tools = {}
        for tc in calls:
            t = tc.get("tool", "unknown")
            if t not in tools:
                tools[t] = {"calls": 0, "errors": 0}
            tools[t]["calls"] += 1
            if not tc.get("success"):
                tools[t]["errors"] += 1
        for t in tools:
            c = tools[t]["calls"]
            tools[t]["error_rate"] = f"{round(tools[t]['errors']/c*100)}%" if c else "0%"
        return {"tools": tools, "total_calls": len(calls)}
    else:
        return {
            "metrics": {
                "total_events": len(events),
                "tool_calls": sum(1 for e in events if e.get("type") == "tool_call"),
                "delegations": sum(1 for e in events if e.get("type") == "delegation"),
                "errors": sum(1 for e in events if e.get("type") == "error" or e.get("error")),
            },
        }


# ═══════════════════════════════════════════════════════════════════════
# 3. LEARNER
# ═══════════════════════════════════════════════════════════════════════

LEARNINGS_FILE = DATA_DIR / "learnings.jsonl"
MAX_LEARNINGS = 500


def learn_from_interaction(body: dict) -> dict:
    task = body.get("task", "")
    if not task:
        return {"status": "error", "error": "Task required"}
    entry = {
        "timestamp": time.time(),
        "date": datetime.now().isoformat(),
        "task": str(task)[:500],
        "model_or_tool": str(body.get("model_or_tool", ""))[:500],
        "quality_score": max(1, min(10, int(body.get("quality_score", 5)))),
        "what_worked": str(body.get("what_worked", ""))[:500],
        "what_failed": str(body.get("what_failed", ""))[:500],
        "do_differently": str(body.get("do_differently", ""))[:500],
        "tags": (body.get("tags", []) or [])[:10],
    }
    _aj(LEARNINGS_FILE, entry)
    all_l = _rj_lines(LEARNINGS_FILE, MAX_LEARNINGS + 10)
    if len(all_l) > MAX_LEARNINGS:
        keep = all_l[-MAX_LEARNINGS:]
        LEARNINGS_FILE.write_text("\n".join(json.dumps(e, default=str) for e in keep) + "\n")
    total = len(_rj_lines(LEARNINGS_FILE))
    return {"status": "learned", "quality_score": entry["quality_score"], "task_preview": task[:100], "total_learnings": total}


def apply_learnings(body: dict) -> dict:
    task_desc = body.get("task_description", "")
    if not task_desc:
        return {"status": "error", "error": "task_description required"}
    learnings = _rj_lines(LEARNINGS_FILE, 200)
    if not learnings:
        return {"status": "no_learnings", "message": "No past learnings", "applicable_lessons": []}
    stop_words = {"the", "a", "an", "is", "to", "and", "of", "in", "for", "with", "on", "at", "by"}
    query_words = [w for w in task_desc.lower().split() if len(w) > 2 and w not in stop_words]
    model_or_tool = (body.get("model_or_tool") or "").lower()
    scored = []
    for l in learnings:
        score = 0.0
        search_text = " ".join([
            str(l.get("task", "")), str(l.get("what_worked", "")),
            str(l.get("what_failed", "")), str(l.get("do_differently", "")),
            " ".join(l.get("tags", [])),
        ]).lower()
        if query_words:
            matches = sum(1 for w in query_words if w in search_text)
            score += (matches / len(query_words)) * 10
        if model_or_tool and l.get("model_or_tool") and model_or_tool in l["model_or_tool"].lower():
            score += 5
        age = (time.time() - (l.get("timestamp") or 0)) / 86400
        if age < 1: score += 3
        elif age < 7: score += 2
        elif age < 30: score += 1
        q = l.get("quality_score", 5)
        if q >= 9 or q <= 2: score += 2
        elif q >= 8 or q <= 3: score += 1
        if score > 0:
            scored.append((score, l))
    scored.sort(key=lambda x: -x[0])
    top = scored[:min(body.get("max_results", 5), 20)]
    if not top:
        return {"status": "no_matches", "total_learnings": len(learnings), "applicable_lessons": []}
    lessons = []
    for score, l in top:
        lesson = {
            "relevance_score": round(score, 1),
            "task": l.get("task", ""),
            "model_or_tool": l.get("model_or_tool", ""),
            "quality_score": l.get("quality_score", 0),
            "date": l.get("date", ""),
            "advice": (l.get("do_differently") or
                       (f"Avoid: {l['what_failed']}" if l.get("what_failed") else None) or
                       (f"Repeat: {l['what_worked']}" if l.get("what_worked") else None)),
        }
        lessons.append(lesson)
    return {"status": "found", "total_learnings": len(learnings), "matches": len(top), "applicable_lessons": lessons}


def list_learnings() -> dict:
    learnings = _rj_lines(LEARNINGS_FILE, 200)
    return {"status": "ok", "count": len(learnings), "learnings": learnings}


# ═══════════════════════════════════════════════════════════════════════
# 4. VALIDATE
# ═══════════════════════════════════════════════════════════════════════

HALLUCINATION_PATTERNS = [
    (r"(?i)as of my (?:last |knowledge )?cut\.?off", "knowledge cutoff reference"),
    (r"(?i)I (?:don't|cannot|can't) (?:access|browse|search)", "capability denial"),
    (r"(?:January|February|March|April|May) 20[0-9]{2}", "specific date — verify"),
    (r"version \d+\.\d+\.\d+", "specific version — verify"),
    (r"(?i)according to (?:the|a) (?:official|latest)", "vague authority"),
    (r"(?i)it is (?:widely|generally|commonly) (?:known|accepted|believed)", "weasel words"),
]


def validate_output(body: dict) -> dict:
    task = body.get("task", "")
    result = body.get("result", "")
    if not task or not result:
        return {"error": "task and result required"}
    model_used = body.get("model_used", "unknown")
    flags = []
    for pattern, desc in HALLUCINATION_PATTERNS:
        if re.search(pattern, result):
            flags.append(desc)
    score = 7
    if len(result) < 50: score -= 2
    elif len(result) > 5000: score += 1
    task_words = set(re.findall(r"\b\w{4,}\b", task.lower()))
    if task_words:
        overlap = sum(1 for w in task_words if w in result.lower())
        ratio = overlap / len(task_words)
        if ratio >= 0.6: score += 1
        elif ratio < 0.2: score -= 2
    score -= len(flags)
    score = max(0, min(10, score))
    rec = "TRUST" if score >= 7 else ("CAUTION" if score >= 4 else "REJECT")
    return {"score": score, "recommendation": rec, "pattern_flags": flags, "model_used": model_used, "length": len(result)}


# ═══════════════════════════════════════════════════════════════════════
# 5. DELEGATION SCORE
# ═══════════════════════════════════════════════════════════════════════

DEL_SCORES_FILE = DATA_DIR / "delegation-scores.jsonl"
VALID_TYPES = {"code", "research", "analysis", "creative", "summary"}


def delegation_log(body: dict) -> dict:
    model = body.get("model", "").strip()
    task_type = (body.get("task_type") or "").strip().lower()
    score = body.get("score")
    if not model: return {"status": "error", "error": "model required"}
    if task_type not in VALID_TYPES: return {"status": "error", "error": f"task_type must be: {', '.join(sorted(VALID_TYPES))}"}
    if score is None: return {"status": "error", "error": "score required"}
    score = max(0, min(10, int(score)))
    tokens = max(0, int(body.get("tokens_used", 0)))
    entry = {"timestamp": datetime.now().isoformat(), "model": model, "task_type": task_type, "score": score, "tokens_used": tokens}
    _aj(DEL_SCORES_FILE, entry)
    return {"status": "logged", "entry": entry}


def delegation_stats(period: str = "all") -> dict:
    entries = _rj_lines(DEL_SCORES_FILE, 1000)
    if not entries: return {"status": "no_data"}
    now_dt = datetime.now()
    if period == "today":
        cutoff = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        entries = [e for e in entries if datetime.fromisoformat(e.get("timestamp", "2000")) >= cutoff]
    elif period == "week":
        cutoff = time.time() - 7 * 86400
        entries = [e for e in entries if (e.get("timestamp", 0) if isinstance(e.get("timestamp"), (int, float)) else time.time()) >= cutoff]
    if not entries: return {"status": "no_data", "period": period}
    models = {}
    for e in entries:
        m = e.get("model", "unknown"); sc = e.get("score", 0)
        if m not in models: models[m] = {"scores": [], "tokens": []}
        models[m]["scores"].append(sc); models[m]["tokens"].append(e.get("tokens_used", 0))
    result = {}
    for m, d in models.items():
        avg_s = round(sum(d["scores"]) / len(d["scores"]), 1)
        success_c = sum(1 for s in d["scores"] if s >= 7)
        result[m] = {"avg_score": avg_s, "calls": len(d["scores"]), "success_rate": f"{round(100 * success_c / len(d['scores']))}%", "avg_tokens": round(sum(d["tokens"]) / len(d["tokens"])) if d["tokens"] else 0}
    best = max(result.items(), key=lambda x: x[1]["avg_score"]) if result else None
    rec = f"{best[0]} is most reliable (avg {best[1]['avg_score']}, {best[1]['calls']} calls)" if best else "No data"
    return {"models": result, "total_entries": len(entries), "recommendation": rec}


# ═══════════════════════════════════════════════════════════════════════
# 6. MEMORY SCORE & DECAY
# ═══════════════════════════════════════════════════════════════════════

MEM_SCORES_FILE = DATA_DIR / "memory-scores.json"


def memory_score(body: dict) -> dict:
    action = body.get("action", "rank")
    key = body.get("memory_key", "")
    scores = _rj(MEM_SCORES_FILE, {})
    now = time.time()
    if action == "rank":
        ranked = []
        for k, d in scores.items():
            decayed = d.get("importance", 1) * math.exp(-0.693 * (now - d.get("last_accessed", now)) / 86400 / 14)
            ranked.append({"key": k, "importance": d.get("importance", 1), "decayed_score": round(decayed, 3), "accesses": d.get("accesses", 0)})
        ranked.sort(key=lambda x: -x["decayed_score"])
        return {"status": "ok", "memories": ranked, "count": len(ranked)}
    if not key: return {"error": "memory_key required"}
    if key not in scores: scores[key] = {"importance": 1.0, "accesses": 0, "last_accessed": now, "created": now}
    if action == "boost":
        scores[key]["importance"] = min(scores[key].get("importance", 1) + 0.5, 5.0)
        scores[key]["last_accessed"] = now; _wj(MEM_SCORES_FILE, scores)
        return {"status": "boosted", "key": key, "new_importance": scores[key]["importance"]}
    if action == "access":
        scores[key]["accesses"] = scores[key].get("accesses", 0) + 1
        scores[key]["last_accessed"] = now; _wj(MEM_SCORES_FILE, scores)
        return {"status": "accessed", "key": key, "accesses": scores[key]["accesses"]}
    return {"error": f"Invalid action: {action}"}


def memory_decay(threshold: float = 0.1) -> dict:
    scores = _rj(MEM_SCORES_FILE, {})
    now = time.time()
    flagged, healthy = [], []
    for k, d in scores.items():
        decayed = d.get("importance", 1) * math.exp(-0.693 * (now - d.get("last_accessed", now)) / 86400 / 14)
        e = {"key": k, "score": round(decayed, 3), "days_since_access": round((now - d.get("last_accessed", now)) / 86400)}
        if decayed < threshold: flagged.append(e)
        else: healthy.append(e)
    return {"healthy_count": len(healthy), "flagged_for_removal": flagged, "suggestion": f"{len(flagged)} below threshold" if flagged else "All healthy"}


# ═══════════════════════════════════════════════════════════════════════
# 7. HABITS
# ═══════════════════════════════════════════════════════════════════════

HABITS_FILE = DATA_DIR / "habits.json"


def habits_log(body: dict) -> dict:
    data = _rj(HABITS_FILE, {"interactions": [], "hour_counts": {}, "topic_counts": {}, "total_interactions": 0, "first_seen": "", "last_seen": ""})
    now_dt = datetime.now(); hour = str(now_dt.hour); topic = body.get("topic", "general")
    entry = {"timestamp": now_dt.isoformat(), "hour": now_dt.hour, "day_of_week": now_dt.strftime("%A"), "topic": topic, "v_message_length": int(body.get("v_message_length", 0)), "v_mood": body.get("v_mood", ""), "response_was_good": body.get("response_was_good", True)}
    data["hour_counts"][hour] = data["hour_counts"].get(hour, 0) + 1
    data["topic_counts"][topic] = data["topic_counts"].get(topic, 0) + 1
    data["total_interactions"] = data.get("total_interactions", 0) + 1
    if not data.get("first_seen"): data["first_seen"] = now_dt.isoformat()
    data["last_seen"] = now_dt.isoformat()
    data["interactions"].append(entry)
    if len(data["interactions"]) > 200: data["interactions"] = data["interactions"][-200:]
    _wj(HABITS_FILE, data)
    return {"status": "logged", "total_interactions": data["total_interactions"]}


def habits_insights() -> dict:
    data = _rj(HABITS_FILE, {})
    if not data.get("total_interactions"): return {"status": "no_data", "message": "No interactions logged yet"}
    hours = data.get("hour_counts", {}); peak = sorted(hours.items(), key=lambda x: -x[1])[:3]
    topics = data.get("topic_counts", {}); top = sorted(topics.items(), key=lambda x: -x[1])[:5]
    recent = data.get("interactions", [])[-50:]
    good = sum(1 for i in recent if i.get("response_was_good", True))
    rate = round(good / len(recent) * 100) if recent else 0
    recs = []
    if peak: recs.append(f"Most active around {peak[0][0]}:00")
    if top: recs.append(f"Frequent topic: {top[0][0]}")
    return {"total_interactions": data["total_interactions"], "peak_hours": [f"{h}:00 ({c} msgs)" for h, c in peak], "top_topics": [{"topic": t, "count": c} for t, c in top], "response_success_rate": f"{rate}%", "avg_message_length": data.get("avg_message_length", 0), "recommendations": recs}


# ═══════════════════════════════════════════════════════════════════════
# 8. CACHE
# ═══════════════════════════════════════════════════════════════════════

CACHE_FILE = CACHE_DIR / "delegation-cache.json"
MAX_CACHE_ENTRIES = 100
CACHE_TTL = 86400


def cache_stats() -> dict:
    cache = _rj(CACHE_FILE, {}); now = time.time()
    valid = sum(1 for v in cache.values() if now - v.get("cached_at", 0) < CACHE_TTL)
    hits = sum(v.get("hit_count", 0) for v in cache.values())
    return {"total": len(cache), "valid": valid, "total_hits": hits, "max": MAX_CACHE_ENTRIES, "ttl_hours": 24}


def cached_delegate(body: dict) -> dict:
    model = body.get("model", ""); goal = body.get("goal", "")
    if not model or not goal: return {"status": "error", "error": "model and goal required"}
    bypass = body.get("bypass_cache", False)
    key = hashlib.sha256(f"{model.strip().lower()}::{goal.strip().lower()}".encode()).hexdigest()[:16]
    cache = _rj(CACHE_FILE, {}); now = time.time()
    if not bypass and key in cache:
        entry = cache[key]; age = now - (entry.get("cached_at") or 0)
        if age < CACHE_TTL:
            entry["last_accessed"] = now; entry["hit_count"] = entry.get("hit_count", 0) + 1
            _wj(CACHE_FILE, cache)
            return {"status": "cache_hit", "result": entry.get("result", ""), "age_seconds": round(age), "hit_count": entry["hit_count"]}
    return {"status": "cache_miss", "cache_key": key, "message": "No cached result"}


# ═══════════════════════════════════════════════════════════════════════
# 9. SCHEDULER
# ═══════════════════════════════════════════════════════════════════════

SCHEDULE_FILE = DATA_DIR / "schedule.json"


def schedule_add(body: dict) -> dict:
    title = body.get("title", ""); when = body.get("when", "")
    if not title or not when: return {"status": "error", "error": "title and when required"}
    data = _rj(SCHEDULE_FILE, {"events": []})
    event = {"id": f"ev-{int(time.time() * 1000)}", "title": title, "when": when, "duration_minutes": int(body.get("duration_minutes", 30)), "category": body.get("category", "task"), "notes": body.get("notes", ""), "created_at": datetime.now().isoformat(), "status": "active"}
    data["events"].append(event); _wj(SCHEDULE_FILE, data)
    return {"status": "added", "event": event}


def schedule_list() -> dict:
    data = _rj(SCHEDULE_FILE, {"events": []})
    events = [e for e in data.get("events", []) if e.get("status") == "active"]
    events.sort(key=lambda e: e.get("when", ""))
    return {"status": "ok", "count": len(events), "events": events}


def schedule_remove(event_id: str) -> dict:
    data = _rj(SCHEDULE_FILE, {"events": []})
    for ev in data.get("events", []):
        if ev.get("id") == event_id:
            ev["status"] = "removed"; _wj(SCHEDULE_FILE, data)
            return {"status": "removed", "event_id": event_id}
    return {"status": "not_found"}


# ═══════════════════════════════════════════════════════════════════════
# 10. WATCHDOG
# ═══════════════════════════════════════════════════════════════════════

WATCHDOG_FILE = DATA_DIR / "watchdog-state.json"


def _get_watchdog_status() -> dict:
    state = _rj(WATCHDOG_FILE, {"last_heartbeat": 0, "total_heartbeats": 0})
    now = time.time(); silent = -1; last_time = "never"
    if state.get("last_heartbeat"):
        silent = int((now - state["last_heartbeat"]) / 60)
        last_time = datetime.fromtimestamp(state["last_heartbeat"]).strftime("%H:%M")
    return {"status": "never" if silent < 0 else ("alive" if silent < 120 else "silent"), "last_heartbeat": last_time, "last_activity": state.get("last_activity", "none"), "silent_minutes": silent, "is_silent_alert": silent > 120 if silent > 0 else False, "total_heartbeats": state.get("total_heartbeats", 0), "alerts_sent_today": state.get("alerts_sent_today", 0), "threshold_minutes": 120}


def watchdog_heartbeat(activity: str = "heartbeat") -> dict:
    state = _rj(WATCHDOG_FILE, {"last_heartbeat": 0, "total_heartbeats": 0})
    now = time.time()
    state["last_heartbeat"] = now; state["last_activity"] = activity
    state["total_heartbeats"] = state.get("total_heartbeats", 0) + 1
    _wj(WATCHDOG_FILE, state)
    return {"status": "alive", "activity": activity, "total_heartbeats": state["total_heartbeats"]}


def watchdog_status() -> dict:
    return _get_watchdog_status()


# ═══════════════════════════════════════════════════════════════════════
# HTTP HANDLER FUNCTIONS (für api/routes.py)
# ═══════════════════════════════════════════════════════════════════════

def _read_body(handler) -> dict:
    try:
        content_length = int(handler.headers.get("Content-Length", 0))
        if content_length > 0:
            raw = handler.rfile.read(content_length)
            return json.loads(raw.decode("utf-8"))
    except Exception:
        pass
    return {}


try:
    from urllib.parse import parse_qs
except ImportError:
    from urllib.parse import parse_qs


def handle_evey_get(handler, parsed) -> bool:
    """Handle GET /api/evey/... requests."""
    from web.api.helpers import j, t
    path = parsed.path

    if path in ("/api/evey/dashboard", "/api/evey/"):
        html = _get_dashboard_html()
        return t(handler, html, content_type="text/html; charset=utf-8")
    if path == "/api/evey/status":
        return j(handler, get_status())
    if path == "/api/evey/telemetry":
        qs = parse_qs(parsed.query)
        return j(handler, query_telemetry((qs.get("type") or [None])[0] or "session_metrics", int((qs.get("limit") or [20])[0])))
    if path == "/api/evey/learnings":
        return j(handler, list_learnings())
    if path == "/api/evey/delegation/stats":
        qs = parse_qs(parsed.query)
        return j(handler, delegation_stats((qs.get("period") or [None])[0] or "all"))
    if path == "/api/evey/habits/insights":
        return j(handler, habits_insights())
    if path == "/api/evey/cache":
        return j(handler, cache_stats())
    if path == "/api/evey/schedule":
        return j(handler, schedule_list())
    if path == "/api/evey/watchdog/status":
        return j(handler, watchdog_status())
    return False


def handle_evey_post(handler, parsed, body) -> bool:
    """Handle POST /api/evey/... requests."""
    from web.api.helpers import j
    path = parsed.path
    if body is None:
        body = _read_body(handler)
    b = body or {}
    if path == "/api/evey/learn":
        return j(handler, learn_from_interaction(b))
    if path == "/api/evey/learnings/apply":
        return j(handler, apply_learnings(b))
    if path == "/api/evey/validate":
        return j(handler, validate_output(b))
    if path == "/api/evey/delegation/log":
        return j(handler, delegation_log(b))
    if path == "/api/evey/memory/score":
        return j(handler, memory_score(b))
    if path == "/api/evey/memory/decay":
        return j(handler, memory_decay(float(b.get("threshold", 0.1))))
    if path == "/api/evey/habits/log":
        return j(handler, habits_log(b))
    if path == "/api/evey/cache/lookup":
        return j(handler, cached_delegate(b))
    if path == "/api/evey/schedule":
        return j(handler, schedule_add(b))
    if path.startswith("/api/evey/schedule/") and len(path) > 19:
        return j(handler, schedule_remove(path.split("/")[-1]))
    if path == "/api/evey/watchdog/heartbeat":
        return j(handler, watchdog_heartbeat(b.get("activity", "heartbeat")))
    return False


# ═══════════════════════════════════════════════════════════════════════
# DASHBOARD HTML
# ═══════════════════════════════════════════════════════════════════════

_DASHBOARD_HTML = None


def _get_dashboard_html() -> str:
    global _DASHBOARD_HTML
    if _DASHBOARD_HTML is not None:
        return _DASHBOARD_HTML
    static_path = Path(__file__).parent.parent / "static" / "evey-dashboard.html"
    if static_path.exists():
        _DASHBOARD_HTML = static_path.read_text(encoding="utf-8")
    else:
        _DASHBOARD_HTML = _FALLBACK_HTML
    return _DASHBOARD_HTML


_FALLBACK_HTML = """<!doctype html>
<html lang="de">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Evey Tools</title>
<style>
:root{--bg:#0d0d0d;--card:#1a1a1a;--border:#2a2a2a;--text:#e0e0e0;--accent:#4fc3f7;--muted:#888}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:system-ui,sans-serif;padding:20px}
h1{background:linear-gradient(135deg,#4fc3f7,#7c4dff);-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:16px}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:16px;margin-bottom:12px}
.card h3{color:var(--accent);font-size:.95rem;margin-bottom:10px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:10px;margin-bottom:12px}
.stat{text-align:center;padding:12px;background:rgba(79,195,247,.04);border:1px solid var(--border);border-radius:8px}
.stat-val{font-size:1.4rem;font-weight:700}
.stat-lbl{font-size:.75rem;color:var(--muted)}
.row{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid rgba(255,255,255,.04);font-size:.85rem}
.lbl{color:var(--muted)}
.badge{display:inline-block;padding:2px 10px;border-radius:10px;font-size:.7rem;font-weight:600}
.b-green{background:rgba(102,187,106,.15);color:#66bb6a}
.b-orange{background:rgba(255,167,38,.15);color:#ffa726}
.loading{text-align:center;padding:30px;color:var(--muted)}
</style></head>
<body>
<h1>🤖 Evey Tools</h1>
<div id="root"><div class="loading">Loading...</div></div>
<script>
const API='/api/evey';
async function api(m,p,b){const o={method:m,headers:{'Content-Type':'application/json'}};if(b)o.body=JSON.stringify(b);return(await fetch(API+p,o)).json()}
async function load(){
  const d=await api('GET','/status');
  let h='<div class="grid">';
  if(d.system) h+='<div class="stat"><div class="stat-val">'+d.system.memory_pct+'%</div><div class="stat-lbl">RAM</div></div><div class="stat"><div class="stat-val">'+Math.round(d.system.uptime/3600)+'h</div><div class="stat-lbl">Uptime</div></div><div class="stat"><div class="stat-val">'+d.system.cpus+'</div><div class="stat-lbl">CPUs</div></div>';
  if(d.evey) h+='<div class="stat"><div class="stat-val">'+d.evey.learnings+'</div><div class="stat-lbl">Learnings</div></div><div class="stat"><div class="stat-val">'+d.evey.delegation_scores+'</div><div class="stat-lbl">Scores</div></div>';
  if(d.watchdog) h+='<div class="stat"><div class="stat-val"><span class="badge '+(d.watchdog.status==='alive'?'b-green':'b-orange')+'">'+d.watchdog.status+'</span></div><div class="stat-lbl">Watchdog</div></div>';
  h+='</div><div class="card"><h3>📋 System</h3>';
  if(d.system){h+='<div class="row"><span class="lbl">Host</span><span>'+d.system.hostname+'</span></div><div class="row"><span class="lbl">OS</span><span>'+d.system.platform+'</span></div>';}
  if(d.process) h+='<div class="row"><span class="lbl">Python</span><span>'+d.process.python+'</span></div>';
  h+='</div><p style="color:var(--muted);font-size:.8rem">Evey Tools v1.0 — API endpoints active</p>';
  document.getElementById('root').innerHTML=h;
}
load();
</script>
</body></html>"""
