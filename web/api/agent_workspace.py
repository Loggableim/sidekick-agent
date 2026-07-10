"""Agent Workspace — LLM-basierte Intent-Erkennung für autonome Agenten.

Ersetzt das alte Keyword-Matching durch echte LLM-Intents.
Der LLM analysiert die User-Anfrage und gibt einen strukturierten
Plan mit Shell-Kommandos zurück, die dann ausgeführt werden.
"""

import json
import logging
import os
import queue
import select
import re
import subprocess
import threading
import time
import uuid
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Optional

from web.api._home import get_active_webui_home, get_webui_home

logger = logging.getLogger(__name__)

# ── Pfade ──────────────────────────────────────────────────────────────
SIDEKICK_HOME = get_webui_home()
WORKSPACES_ROOT = SIDEKICK_HOME / "workspaces"

_LLM_CACHE = {}  # Cache für Config


def _active_home() -> Path:
    """Return the active home for the current request when available."""
    try:
        return Path(get_active_webui_home()).expanduser().resolve()
    except Exception:
        return Path(get_webui_home()).expanduser().resolve()


# ── LLM-Config (lazy, gecached) ────────────────────────────────────────

def _get_llm_config() -> dict:
    """Lade LLM-Config aus .env + config.yaml (einmalig gecached)."""
    home = _active_home()
    if _LLM_CACHE.get("config") and _LLM_CACHE.get("home") == str(home):
        return _LLM_CACHE["config"]

    try:
        from web.api.config import resolve_active_provider_context

        context = resolve_active_provider_context()
        if context.get("provider"):
            config = {
                "provider": context.get("provider"),
                "api_key": context.get("api_key") or "",
                "model": context.get("model") or "",
                "base_url": context.get("base_url") or "",
            }
            _LLM_CACHE["config"] = config
            return config
    except Exception:
        logger.debug("Shared provider context unavailable for agent workspace", exc_info=True)

    # .env lesen
    api_key = ""
    model = "openai/gpt-oss-20b:free"
    base_url = "https://openrouter.ai/api/v1"
    env_path = home / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("OPENROUTER_API_KEY="):
                api_key = line.split("=", 1)[1].strip().strip("\"'")

    # Fallback: os.environ
    if not api_key:
        api_key = os.environ.get("OPENROUTER_API_KEY", "")

    # Model aus config.yaml
    config_path = home / "config.yaml"
    if config_path.exists():
        mm = re.search(r'default:\s*[\"\']?(.+?)[\"\']?\s*$',
                       config_path.read_text(encoding="utf-8"), re.MULTILINE)
        if mm and not mm.group(1).startswith("openai/gpt-oss"):
            model = mm.group(1).strip()

    config = {"api_key": api_key, "model": model, "base_url": base_url}
    _LLM_CACHE["home"] = str(home)
    _LLM_CACHE["config"] = config
    return config


def _call_llm(messages: list, timeout: int = 30) -> Optional[str]:
    """Rufe Chat-Completion API an (OpenRouter-kompatibel). Gibt Text oder None."""
    config = _get_llm_config()
    api_key = config.get("api_key", "")
    model = config.get("model", "openai/gpt-oss-20b:free")
    base_url = config.get("base_url") or "https://openrouter.ai/api/v1"

    try:
        from web.api.config import game_mode_blocks_local_model_request

        if game_mode_blocks_local_model_request(config.get("provider"), base_url):
            from runtime.auxiliary_client import call_llm

            response = call_llm(
                provider="ollama-cloud",
                model="deepseek-v4-flash",
                messages=messages,
                temperature=0.3,
                max_tokens=1000,
                timeout=timeout,
                extra_body={"response_format": {"type": "json_object"}},
            )
            return response.choices[0].message.content
    except Exception:
        logger.debug("Agent workspace Game Mode remote fallback failed", exc_info=True)
        return None

    if not api_key or len(api_key) < 10:
        logger.warning("No valid API key for LLM call")
        return None

    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 1000,
        "response_format": {"type": "json_object"},
    }).encode()

    req = urllib.request.Request(
        url, data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        logger.warning(f"LLM call failed: {e}")
        return None


# ── Intent-Prompt ──────────────────────────────────────────────────────

INTENT_SYSTEM_PROMPT = """Du bist ein KI-Assistent, der User-Anfragen analysiert und in Shell-Kommandos übersetzt.

## Deine Aufgabe
Analysiere die User-Anfrage und erstelle einen strukturierten Plan mit Shell-Kommandos, die im Workspace-Verzeichnis ausgeführt werden.

## Wichtig
- Gib NUR valides JSON zurück (response_format = json_object)
- Wähle die passendsten Kommandos für die Anfrage
- Maximal 5 Kommandos pro Anfrage
- Jedes Kommando sollte eigenständig ausführbar sein
- Verwende absolute Pfade oder relative Pfade zum Workspace

## Antwort-Format (JSON)
{
  "intent": "explore|git|install|create|search|run|analyze|other",
  "explanation": "Kurze Erklärung auf Deutsch, was gemacht wird",
  "commands": [
    {"cmd": "ls -la", "description": "Zeige Dateien", "timeout": 15},
    {"cmd": "git status 2>&1 || echo 'kein git'", "description": "Prüfe Git-Status", "timeout": 15}
  ],
  "needs_confirmation": false
}

## Intent-Typen
- explore: Verzeichnis erkunden, Dateien auflisten, Struktur anzeigen
- git: Git-Status, Log, Branch, Commit (KEIN git push ohne Bestätigung!)
- install: Pakete installieren (pip, npm, apt)
- create: Dateien oder Ordner erstellen
- search: Nach Inhalten suchen (grep, find)
- run: Ein Skript oder Programm ausführen
- analyze: Daten analysieren, Logs prüfen, Auswerten
- other: Alles andere

## Sicherheit
- Bei `git push`, `rm -rf`, `sudo`, `chmod -R` setze needs_confirmation=true
- Führe NICHTS aus, was das System gefährden könnte
- Bei unsicheren Anfragen setze commands = [] und erkläre warum
"""  # noqa: E501


def analyze_intent(user_message: str, workdir: str) -> Optional[dict]:
    """Analysiere die User-Anfrage mit einem LLM und gib einen Aktionsplan zurück."""
    messages = [
        {"role": "system", "content": INTENT_SYSTEM_PROMPT},
        {"role": "user", "content": f"Arbeitsverzeichnis: {workdir}\n\nUser-Anfrage: {user_message}"},
    ]

    response = _call_llm(messages, timeout=20)
    if not response:
        return None

    try:
        plan = json.loads(response)
        if "commands" not in plan:
            return None
        return plan
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"LLM response parse failed: {e}")
        return None


# ── Workspace Directory Management ─────────────────────────────────────

def ensure_agent_workspace(agent_slug: str, workdir: str = "") -> str:
    """Stelle sicher, dass ein Agent sein Workspace-Verzeichnis hat."""
    if workdir:
        ws_dir = Path(workdir)
        ws_dir.mkdir(parents=True, exist_ok=True)
        return str(ws_dir.resolve())

    ws_dir = _active_home() / "workspaces" / agent_slug
    ws_dir.mkdir(parents=True, exist_ok=True)
    readme = ws_dir / "README.md"
    if not readme.exists():
        readme.write_text(
            f"# Agent Workspace: {agent_slug}\n\n"
            f"Dieses Verzeichnis gehört dem Agenten **{agent_slug}**.\n"
            f"Hier kann der Agent Dateien erstellen, bearbeiten und ausführen.\n\n"
            f"Erstellt: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        )
    return str(ws_dir.resolve())


def init_workspaces_for_agents(agent_slugs: list[str]) -> dict[str, str]:
    """Erstelle Workspaces für alle aktiven Agenten."""
    results = {}
    for slug in agent_slugs:
        try:
            path = ensure_agent_workspace(slug)
            results[slug] = path
        except Exception as e:
            logger.warning(f"Could not create workspace for agent '{slug}': {e}")
            results[slug] = ""
    return results


# ── States ─────────────────────────────────────────────────────────────
STATE_IDLE = "idle"
STATE_RUNNING = "running"
STATE_WAITING = "waiting"
STATE_DONE = "done"
STATE_ERROR = "error"

# ── Aktive Sessions ──────────────────────────────────────────────────────
_workspace_sessions: dict[str, dict] = {}
_sessions_lock = threading.Lock()


# ── Session Management ──────────────────────────────────────────────────

def create_workspace_session(agent_slug: str, workdir: str = "") -> dict:
    """Erstelle eine neue Arbeits-Session für einen Agenten."""
    session_id = str(uuid.uuid4())[:8]
    ws_dir = ensure_agent_workspace(agent_slug, workdir)

    session = {
        "id": session_id,
        "agent_slug": agent_slug,
        "state": STATE_IDLE,
        "workdir": ws_dir,
        "process": None,
        "process_pid": None,
        "output_queue": queue.Queue(),
        "event_queue": queue.Queue(),
        "events": [],
        "created_at": time.time(),
        "tool_calls": [],
    }

    session["events"].append({
        "type": "info",
        "data": f"Workspace: {ws_dir}",
        "timestamp": time.time(),
    })

    with _sessions_lock:
        _workspace_sessions[session_id] = session
    return session


def get_workspace_session(session_id: str) -> Optional[dict]:
    with _sessions_lock:
        return _workspace_sessions.get(session_id)


def destroy_workspace_session(session_id: str):
    with _sessions_lock:
        session = _workspace_sessions.pop(session_id, None)
    if session and session["process"]:
        try:
            session["process"].terminate()
            session["process"].wait(timeout=5)
        except Exception:
            try:
                session["process"].kill()
            except Exception:
                pass


# ── Terminal Execution ──────────────────────────────────────────────────

def _run_in_terminal(session: dict, command: str, timeout: int = 120) -> dict:
    """Führe einen Befehl im Terminal aus und streame die Ausgabe per SSE."""
    session["events"].append({
        "type": "command", "command": command,
        "timestamp": time.time(), "output": "",
    })

    try:
        process = subprocess.Popen(
            command, shell=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=session["workdir"],
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        session["process"] = process
        session["process_pid"] = process.pid

        start = time.time()
        output_lines = []

        while True:
            exit_code = process.poll()
            if exit_code is not None:
                try:
                    remaining = process.stdout.read()
                    if remaining:
                        output_lines.append(remaining)
                        session["event_queue"].put({
                            "type": "output", "data": remaining,
                            "session_id": session["id"],
                        })
                except Exception:
                    pass
                break

            try:
                reads = [process.stdout.fileno()]
                ready, _, _ = select.select(reads, [], [], 0.1)
                if ready:
                    line = process.stdout.readline()
                    if line:
                        output_lines.append(line)
                        session["event_queue"].put({
                            "type": "output", "data": line,
                            "session_id": session["id"],
                        })
            except (ValueError, OSError):
                try:
                    remaining, _ = process.communicate(
                        timeout=max(0.1, timeout - (time.time() - start))
                    )
                    if remaining:
                        output_lines.append(remaining)
                        session["event_queue"].put({
                            "type": "output", "data": remaining,
                            "session_id": session["id"],
                        })
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        remaining, _ = process.communicate(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        remaining, _ = process.communicate()
                    if remaining:
                        output_lines.append(remaining)
                        session["event_queue"].put({
                            "type": "output", "data": remaining,
                            "session_id": session["id"],
                        })
                    msg = f"\nTimeout after {timeout}s\n"
                    output_lines.append(msg)
                    session["event_queue"].put({
                        "type": "output", "data": msg,
                        "session_id": session["id"],
                    })
                break

            if time.time() - start > timeout:
                process.terminate()
                try:
                    remaining, _ = process.communicate(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    remaining, _ = process.communicate()
                if remaining:
                    output_lines.append(remaining)
                    session["event_queue"].put({
                        "type": "output", "data": remaining,
                        "session_id": session["id"],
                    })
                msg = f"\n⏱ Timeout nach {timeout}s\n"
                output_lines.append(msg)
                session["event_queue"].put({
                    "type": "output", "data": msg,
                    "session_id": session["id"],
                })
                break

        exit_code = process.poll()
        full_output = "".join(output_lines)

        if session["events"]:
            session["events"][-1]["output"] = full_output
            session["events"][-1]["exit_code"] = exit_code

        session["event_queue"].put({
            "type": "complete", "exit_code": exit_code,
            "duration": round(time.time() - start, 1),
            "session_id": session["id"],
        })

        return {"output": full_output, "exit_code": exit_code}

    except Exception as e:
        try:
            session["state"] = STATE_ERROR
        except Exception:
            pass
        msg = f"❌ Fehler: {str(e)}"
        session["event_queue"].put({
            "type": "error", "data": msg, "session_id": session["id"],
        })
        return {"output": msg, "exit_code": -1}


# ── SSE Event Streaming ─────────────────────────────────────────────────

def stream_events(session_id: str):
    """Generator für SSE-Events einer Agent-Session."""
    session = get_workspace_session(session_id)
    if not session:
        yield f"data: {json.dumps({'type': 'error', 'data': 'Session not found'})}\n\n"
        return

    for event in session["events"]:
        yield f"data: {json.dumps(event)}\n\n"

    was_running = session["state"] in (STATE_RUNNING, STATE_IDLE)
    while was_running:
        try:
            event = session["event_queue"].get(timeout=1)
            yield f"data: {json.dumps(event)}\n\n"
            if event.get("type") in ("complete", "done"):
                break
        except queue.Empty:
            if session["state"] in (STATE_RUNNING, STATE_IDLE):
                yield f"data: {json.dumps({'type': 'keepalive'})}\n\n"
            else:
                break
        session = get_workspace_session(session_id)
        if not session:
            break
        was_running = session["state"] in (STATE_RUNNING, STATE_IDLE)

    yield f"data: {json.dumps({'type': 'done'})}\n\n"


# ── Agent Autonomy Engine (LLM-based) ──────────────────────────────────

def process_agent_request(agent_slug: str, user_message: str,
                          workdir: str = "", session_id: str = None) -> dict:
    """Verarbeite eine User-Anfrage mittels LLM-basierter Intent-Erkennung.

    Der LLM:
    1. Analysiert die User-Anfrage
    2. Gibt einen strukturierten Plan mit Shell-Kommandos zurück
    3. Die Kommandos werden nacheinander ausgeführt
    4. Alles wird live per SSE gestreamt
    """
    if not session_id:
        session = create_workspace_session(agent_slug, workdir)
        session_id = session["id"]
    else:
        session = get_workspace_session(session_id)
        if not session:
            return {"error": "Session not found"}

    def _run_agent():
        try:
            session["state"] = STATE_RUNNING
            ws = session["workdir"]

            # 1. LLM: Intent erkennen
            session["event_queue"].put({
                "type": "thinking",
                "data": f"🧠 {agent_slug} analysiert deine Anfrage mit KI...",
                "session_id": session_id,
            })

            plan = analyze_intent(user_message, ws)

            if not plan or not plan.get("commands"):
                # Fallback: LLM nicht verfügbar -> einfaches ls
                session["event_queue"].put({
                    "type": "thought",
                    "data": "⚠️ KI-Analyse nicht verfügbar, führe Standard-Erkundung aus",
                    "session_id": session_id,
                })
                _run_in_terminal(session, "ls -la", timeout=15)
                session["state"] = STATE_IDLE
                session["event_queue"].put({
                    "type": "complete",
                    "data": "✅ Standard-Erkundung abgeschlossen (LLM nicht verfügbar).",
                    "session_id": session_id,
                })
                return

            # 2. Plan ausführen
            intent = plan.get("intent", "other")
            explanation = plan.get("explanation", "")
            commands = plan.get("commands", [])
            needs_confirmation = plan.get("needs_confirmation", False)

            session["event_queue"].put({
                "type": "thought",
                "data": f"🎯 Intent erkannt: **{intent}** — {explanation}",
                "session_id": session_id,
            })

            if needs_confirmation:
                session["event_queue"].put({
                    "type": "thought",
                    "data": "⚠️ Dieser Vorgang könnte gefährlich sein. Überspringe.",
                    "session_id": session_id,
                })
                session["state"] = STATE_IDLE
                session["event_queue"].put({
                    "type": "complete",
                    "data": "⏸️ Aus Sicherheitsgründen übersprungen. Nutze das direkte Terminal für riskante Befehle.",
                    "session_id": session_id,
                })
                return

            # 3. Kommandos nacheinander ausführen
            for i, cmd_info in enumerate(commands):
                cmd = cmd_info.get("cmd", "").strip()
                desc = cmd_info.get("description", "")
                cmd_timeout = cmd_info.get("timeout", 30)

                if not cmd:
                    continue

                session["event_queue"].put({
                    "type": "action",
                    "data": f"📋 ({i+1}/{len(commands)}) {desc}",
                    "session_id": session_id,
                })

                result = _run_in_terminal(session, cmd, timeout=cmd_timeout)

                # Kurze Pause zwischen Befehlen
                if result["exit_code"] != 0:
                    session["event_queue"].put({
                        "type": "result",
                        "data": f"⚠️ Exit-Code: {result['exit_code']} — fahre fort",
                        "session_id": session_id,
                    })

            # 4. Fertig
            session["state"] = STATE_IDLE
            session["event_queue"].put({
                "type": "complete",
                "data": f"✅ {len(commands)} Kommando(s) ausgeführt. {explanation}",
                "session_id": session_id,
            })

        except Exception as e:
            session["state"] = STATE_IDLE
            session["event_queue"].put({
                "type": "error",
                "data": f"❌ Fehler: {str(e)}",
                "session_id": session_id,
            })

    thread = threading.Thread(target=_run_agent, daemon=True)
    thread.start()

    return {"session_id": session_id, "status": "started", "agent_slug": agent_slug}


# ── Direkter Terminal-Befehl ───────────────────────────────────────────

def send_command_to_agent(session_id: str, command: str) -> dict:
    """Sende einen Befehl direkt an die Agent-Session."""
    session = get_workspace_session(session_id)
    if not session:
        return {"error": "Session not found"}

    if session["state"] == STATE_RUNNING:
        return {"error": "Agent is currently busy"}

    session["state"] = STATE_RUNNING

    def _exec():
        try:
            session["event_queue"].put({
                "type": "command", "data": f"$ {command}",
                "session_id": session_id,
            })
            result = _run_in_terminal(session, command, timeout=300)
            session["event_queue"].put({
                "type": "result", "data": f"Exit: {result['exit_code']}",
                "session_id": session_id,
            })
            session["state"] = STATE_DONE if result["exit_code"] == 0 else STATE_ERROR
        except Exception as e:
            session["state"] = STATE_ERROR
            session["event_queue"].put({
                "type": "error", "data": str(e),
                "session_id": session_id,
            })
        finally:
            if session["state"] == STATE_RUNNING:
                session["state"] = STATE_ERROR

    thread = threading.Thread(target=_exec, daemon=True)
    thread.start()
    return {"status": "executing", "session_id": session_id}
