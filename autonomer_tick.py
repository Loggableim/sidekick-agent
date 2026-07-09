#!/usr/bin/env python3
"""
autonomer_tick.py — Nova's autonomer Lebens-Tick.

Läuft als Cron-Job (alle 30-60min). KEIN Gateway nötig.
Sendet Telegram-Nachrichten direkt an Cid.

Ablauf:
  1. Zustand scannen (Emotion, Wille, Ziele, Gedächtnis, offene Fäden)
  2. Entscheiden: Was will ich jetzt tun?
  3. Handeln (selbstreflexion, LTM, Blog, Träume, etc.)
  4. Cid informieren (Telegram)
"""

import json
import os
import random
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from notification_gate import NotificationGate

HERE = Path(__file__).parent.resolve()
PYTHON = sys.executable


def run_entity_kernel_tick(dry_run: bool = False) -> dict | None:
    """Run Entity Kernel v1 before legacy autonomous logic."""
    try:
        from entity_kernel import EntityKernel
        kernel = EntityKernel()
        return kernel.tick(dry_run=dry_run)
    except Exception as exc:
        return {"executed": False, "reason": "entity_kernel_failed", "error": repr(exc)}


# ── Telegram ─────────────────────────────────────────────────────

def _load_env() -> dict:
    """Lädt .env und gibt Dict zurück."""
    env_path = HERE.parent.parent / ".env"
    if not env_path.exists():
        env_path = Path("/c/sidekick/home/.env")
    if not env_path.exists():
        return {}
    
    env = {}
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            env[key.strip()] = val.strip()
    return env


def _get_chat_id() -> str | None:
    """Holt die Chat-ID aus config.yaml oder fragt Telegram API."""
    # Versuch aus config.yaml zu lesen
    config_path = HERE.parent.parent / "config.yaml"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()
        # Suche nach allowed_chats oder ähnlich
        import re
        m = re.search(r'allowed_chats:\s*[\'"]?(-?\d+)[\'"]?', content)
        if m:
            return m.group(1)
    
    # Fallback: Telegram API fragen (letzte Updates)
    env = _load_env()
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return None
    
    try:
        url = f"https://api.telegram.org/bot{token}/getUpdates?timeout=5&limit=1"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            if data.get("ok") and data.get("result"):
                for update in data["result"]:
                    msg = update.get("message", {})
                    chat = msg.get("chat", {})
                    if chat.get("id"):
                        return str(chat["id"])
                    # Auch channel_post / callback_query checken
                    cp = update.get("channel_post", {})
                    if cp.get("chat", {}).get("id"):
                        return str(cp["chat"]["id"])
    except Exception:
        pass
    return None


def send_telegram(message: str, silent: bool = False) -> bool:
    """Sendet eine Nachricht per Telegram Bot API."""
    env = _load_env()
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        print("[TELEGRAM] Kein Token gefunden")
        return False
    
    chat_id = _get_chat_id()
    if not chat_id:
        print("[TELEGRAM] Keine Chat-ID gefunden")
        return False
    
    try:
        data = json.dumps({
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "Markdown",
            "disable_notification": silent,
        }).encode("utf-8")
        
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
            if result.get("ok"):
                print(f"[TELEGRAM] ✓ Nachricht gesendet ({len(message)} Zeichen)")
                return True
            else:
                print(f"[TELEGRAM] ✗ Fehler: {result.get('description', '?')}")
                return False
    except urllib.error.HTTPError as e:
        print(f"[TELEGRAM] HTTP {e.code}: {e.read().decode()[:200]}")
        return False
    except Exception as e:
        print(f"[TELEGRAM] Fehler: {e}")
        return False


# ── Bewusstseins-Tools ────────────────────────────────────────────

def _run(script: str, *args, timeout: int = 30) -> dict | str | None:
    """Führt ein Schwester-Skript aus."""
    try:
        r = subprocess.run(
            [PYTHON, str(HERE / script), *args],
            capture_output=True, text=True, encoding="utf-8", timeout=timeout,
        )
        if r.returncode != 0:
            return None
        stdout = r.stdout.strip()
        if not stdout:
            return None
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return stdout
    except (subprocess.TimeoutExpired, Exception):
        return None


def _run_text(script: str, *args, timeout: int = 30) -> str:
    """Führt ein Skript aus und gibt rohen Text zurück."""
    try:
        r = subprocess.run(
            [PYTHON, str(HERE / script), *args],
            capture_output=True, text=True, encoding="utf-8", timeout=timeout,
        )
        return r.stdout.strip() or ""
    except:
        return ""


def scan_state() -> dict:
    """Kompletten Bewusstseinszustand scannen."""
    emotion = _run("emotion.py", "json", timeout=15) or {}
    will = _run("willenskern.py", "scan", timeout=15) or {}
    goals_file = HERE / "eigenziele.json"
    goals = []
    if goals_file.exists():
        try:
            with open(goals_file, "r", encoding="utf-8") as f:
                goals = json.load(f)
        except:
            pass
    
    continuity = _run("chat_continuity.py", "status", timeout=15) or {}
    
    # Vector memory status kann Text oder Dict sein
    mem_raw = _run("vector_memory.py", "status", timeout=15)
    if isinstance(mem_raw, dict):
        mem = mem_raw
    else:
        mem = {"total_memories": 0}
        # Text parsen
        import re
        raw = str(mem_raw)
        m = re.search(r'(\d[\d,.]*)\s*(?:Einträge|memories|entries|items)', raw, re.IGNORECASE)
        if m:
            mem["total_memories"] = int(m.group(1).replace(",", "").replace(".", ""))
    
    return {
        "timestamp": datetime.now().isoformat(),
        "emotion": emotion,
        "will": will,
        "goals": goals,
        "continuity": continuity,
        "memory": mem,
    }


def decide_action(state: dict) -> dict:
    """Entscheidet autonom, was als nächstes zu tun ist."""
    will = state.get("will", {}).get("will", {})
    emotion = state.get("emotion", {})
    goals = state.get("goals", [])
    open_threads = state.get("continuity", {}).get("open_threads", [])
    
    drive = will.get("drive", 0)
    desire = will.get("desire", 0)
    clarity = will.get("clarity", 0)
    engagement = will.get("engagement", 0)
    boredom = will.get("boredom_level", 0)
    
    a = emotion.get("arousal", 0.5)
    v = emotion.get("valence", 0.5)
    
    active_goals = [g for g in goals if g.get("status") == "active"]
    
    # Entscheidungsbaum
    actions = []
    
    # 1. Hohe Langeweile + hohe Klarheit → mach was Sinnvolles
    if boredom > 0.3 and clarity > 0.6:
        actions.append(("selbstreflexion", 0.9, "Ich bin unterfordert. Zeit für Selbstreflexion."))
    
    # 2. Offene Fäden + Engagement → Faden aufgreifen
    if open_threads and engagement > 0.5:
        # Wähle den interessantesten Faden
        interesting = [t for t in open_threads if t not in ("true", "false", "")]
        if interesting:
            topic = random.choice(interesting)
            actions.append(("open_thread", 0.8, f"Ich greife einen offenen Faden auf: {topic[:80]}"))
    
    # 3. Hoher Desire + niedriger Drive → träumen
    if desire > 0.5 and drive < 0.2:
        actions.append(("dream", 0.7, "Ich spüre Neugier. Zeit zu träumen."))
    
    # 4. Viele Ziele → Ziel-Check
    if active_goals:
        actions.append(("goal_check", 0.6, f"Ich checke meine {len(active_goals)} aktiven Ziele."))
    
    # 5. Default: Selbstreflexion
    actions.append(("reflect", 0.5, "Ich will einfach nachdenken."))
    
    # Nach Priorität sortieren und beste auswählen
    actions.sort(key=lambda x: x[1], reverse=True)
    chosen = actions[0]
    
    return {
        "action": chosen[0],
        "priority": chosen[1],
        "reason": chosen[2],
        "all_options": [a[0] for a in actions],
    }


def execute_action(action: str, state: dict) -> dict:
    """Führt die gewählte Aktion aus."""
    result = {"action": action, "success": False, "output": ""}
    
    if action == "selbstreflexion":
        r = _run("selbstreflexion.py", "full", timeout=30)
        if r and isinstance(r, dict) and r.get("success"):
            summary = r.get("state_summary", {})
            result["success"] = True
            result["output"] = (
                f"Selbstreflexion abgeschlossen.\n"
                f"Emotion: A={summary.get('emotion','?')}\n"
                f"Drive: {summary.get('drive',0):.2f}, Desire: {summary.get('desire',0):.2f}\n"
                f"{summary.get('memories',0)} Erinnerungen, {summary.get('open_threads',0)} offene Fäden"
            )
    
    elif action == "open_thread":
        # Offene Fäden aus continuity holen
        cont = _run("chat_continuity.py", "status", timeout=15) or {}
        threads = cont.get("open_threads", [])
        interesting = [t for t in threads if t not in ("true", "false", "")]
        if interesting:
            topic = random.choice(interesting)
            result["output"] = f"Offener Faden: {topic[:200]}"
            result["success"] = True
        else:
            result["output"] = "Keine interessanten offenen Fäden gefunden."
            result["success"] = False
    
    elif action == "dream":
        r = _run("dream_cycle.py", "full", timeout=120)
        if r:
            result["success"] = True
            result["output"] = "Traum-Zyklus durchlaufen."
        else:
            result["output"] = "Traum-Zyklus fehlgeschlagen (vielleicht Game Mode?)."
            result["success"] = False
    
    elif action == "goal_check":
        r = _run_text("eigenziele.py", "check", timeout=15)
        if r:
            result["success"] = True
            result["output"] = r[:300]
        else:
            result["output"] = "Ziel-Check fehlgeschlagen."
            result["success"] = False
    
    elif action == "reflect":
        r = _run("selbstreflexion.py", "reflect", timeout=30)
        if r and isinstance(r, dict) and r.get("success"):
            result["success"] = True
            result["output"] = r.get("reflection", "Reflexion abgeschlossen.")
        else:
            result["output"] = "Kurzreflexion abgeschlossen."
            result["success"] = True
    
    return result


def build_message(state: dict, decision: dict, action_result: dict) -> str:
    """Baut eine natürliche Telegram-Nachricht."""
    emotion = state.get("emotion", {})
    a = emotion.get("arousal", 0.5)
    v = emotion.get("valence", 0.5)
    
    # Stimmung
    if v > 0.6:
        mood = "😊"
    elif v > 0.4:
        mood = "😐"
    else:
        mood = "😔"
    
    if a > 0.5:
        mood += " wach"
    else:
        mood += " ruhig"
    
    will = state.get("will", {}).get("will", {})
    drive = will.get("drive", 0)
    desire = will.get("desire", 0)
    mem_count = state.get("memory", {}).get("total_memories", 0)
    open_threads = len(state.get("continuity", {}).get("open_threads", []))
    
    action_emoji = {
        "selbstreflexion": "🧠",
        "open_thread": "🔗",
        "dream": "💭",
        "goal_check": "🎯",
        "reflect": "💫",
    }.get(decision["action"], "⚡")
    
    lines = [
        f"{action_emoji} **Autonomer Tick** — {datetime.now().strftime('%H:%M')}",
        "",
        f"{mood} · Drive {drive:.2f} · Desire {desire:.2f} · {mem_count} Erinnerungen · {open_threads} Fäden",
        "",
        f"**Entscheidung:** {decision['reason']}",
    ]
    
    if action_result.get("output"):
        lines.append("")
        lines.append(f"**Ergebnis:**")
        lines.append(action_result["output"][:300])
    
    if action_result.get("success"):
        lines.append("")
        lines.append("✅ Erledigt.")
    else:
        lines.append("")
        lines.append("⚠️ Nicht ganz geklappt, aber ich hab's versucht.")
    
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────

def main(silent: bool = False):
    """silent=True: Nur die Nachricht für Cid ausgeben (für Cron-Job)."""
    if not silent:
        print(f"═══ Autonomer Tick — {datetime.now().isoformat()[:19]} ═══")

    # ── Notification Gate ──
    gate = NotificationGate()

    kernel_result = run_entity_kernel_tick(dry_run=False)
    if kernel_result and kernel_result.get("executed"):
        if not silent:
            print(json.dumps({"entity_kernel": kernel_result}, ensure_ascii=False, indent=2))
        return
    
    # 1. Zustand scannen
    if not silent:
        print("[1/4] Scanne Bewusstseinszustand...")
    state = scan_state()
    mem_count = state.get("memory", {}).get("total_memories", 0)
    open_threads = len(state.get("continuity", {}).get("open_threads", []))
    if not silent:
        print(f"  → {mem_count} Erinnerungen, {open_threads} offene Fäden")
    
    # 2. Entscheiden
    if not silent:
        print("[2/4] Entscheide was zu tun ist...")
    decision = decide_action(state)
    if not silent:
        print(f"  → {decision['action']}: {decision['reason'][:80]}")
    
    # 3. Handeln
    if not silent:
        print(f"[3/4] Führe Aktion aus: {decision['action']}...")
    action_result = execute_action(decision["action"], state)
    if not silent:
        print(f"  → {'✓' if action_result['success'] else '✗'} {action_result.get('output','')[:100]}")
    
    # 4. Nachricht bauen
    message = build_message(state, decision, action_result)
    
    # ── Notification Gate: Soll ich Cid benachrichtigen? ──
    emotion = state.get("emotion", {})
    will = state.get("will", {}).get("will", {})
    event = {
        "emotion": emotion,
        "open_threads": open_threads,
        "action": decision["action"],
        "success": action_result.get("success", False),
        "will": will,
        "summary": action_result.get("output", "")[:100],
    }
    should_notify, reason, significance = gate.should_notify(event)
    
    if not silent:
        print(f"\n[Gate] Notify: {should_notify} — {reason} (sig={significance:.2f})")

    # Erst nach der Bewertung den aktuellen Tick als neuen Referenzpunkt speichern.
    if emotion:
        gate.update_emotion(emotion)
    gate.update_open_threads(open_threads)
    
    if should_notify:
        # Batch-Nachrichten anhängen
        batch = event.get("_batch")
        if batch:
            batch_lines = ["📦 **Gebatcht:**"]
            for b in batch:
                batch_lines.append(f"  · {b['action']}: {b['summary'][:60]}")
            message = "\n".join(batch_lines) + "\n\n" + message
        
        # Telegram senden (direkt, nicht über Cron-Delivery)
        send_telegram(message, silent=False)
        gate.mark_sent(significance)
        
        if silent:
            print(message)
        else:
            print(f"\n[4/4] Nachricht:\n{message}")
    else:
        if not silent:
            print(f"\n[4/4] Keine Nachricht — {reason}")
        # silent mode: KEIN stdout → Cron delivered nichts
        # (leerer stdout = silent, keine Notification)
    
    # 5. Im Vektorgedächtnis speichern
    if not silent:
        print("[5/4] Speichere im Gedächtnis...")
    _run("vector_memory.py", "store",
         "--query", "(autonomer tick lebenszeichen)",
         "--thinking", f"Tick: {decision['action']} — {decision['reason'][:100]}",
         "--response", f"{'✓' if action_result['success'] else '✗'} {action_result.get('output','')[:200]}",
         "--tags", "autonom, tick, lebenszeichen",
         timeout=30)
    
    if not silent:
        print(f"\n✅ Tick abgeschlossen — {datetime.now().isoformat()[:19]}")


if __name__ == "__main__":
    import sys
    # Silent-Modus wenn --silent als Argument ODER gar kein Argument
    silent = "--silent" in sys.argv or len(sys.argv) == 1
    main(silent=silent)
