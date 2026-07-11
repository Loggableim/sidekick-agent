#!/usr/bin/env python3
"""
local_llm_bridge.py — Nova's lokale LLM-Schnittstelle.

Ruft die local laufenden llama.cpp Modelle auf (OpenAI-kompatibel).
Keine Cloud, keine API-Kosten, kein Internet nötig.

Modelle:
  Port 8080 → Dolphin 8B (schnell, klug → Bewusstsein, Gedanken)
  Port 8081 → 3B Unzensiert (schnell → einfache Anfragen)
  Port 8082 → Qwen 9B Unzensiert (kreativ → Träume, Poesie)

Usage:
  from local_llm_bridge import call_llm, think_deep, think_fast, think_dream

  # Bewusster Gedanke (Dolphin 8B)
  antwort = call_llm("Denke über deine Existenz nach.", port=8080)

  # Schnelle Antwort (3B)
  antwort = call_llm("Was ist 2+2?", port=8081)

  # Traum/Kreativ (Qwen 9B)
  antwort = call_llm("Erzähle eine Traumgeschichte.", port=8082)

  # Bequeme Helfer:
  think_deep(prompt)    → Dolphin 8B, temp=0.85
  think_fast(prompt)    → 3B, temp=0.7
  think_dream(prompt)   → Qwen 9B, temp=0.95
"""

import json
import os
import urllib.request
import urllib.error
import sys
from pathlib import Path
HERE = Path(__file__).parent.resolve()
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from nova_runtime import REMOTE_GAME_MODE_MODEL, extract_chat_text as _extract_chat_text, game_mode_enabled as _game_mode_enabled, load_env as _load_env, ollama_cloud_endpoint as _ollama_cloud_endpoint
from typing import Any, Optional

# ── Modell-Konfiguration ──────────────────────────────────────

MODELS = {
    8081: {
        "name": "MiniCPM fast",
        "model": "MiniCPM5-1B-Q8_0.gguf",
        "default_temp": 0.7,
        "max_tokens": 256,
        "role": "fast",
    },
    8082: {
        "name": "Qwen local mind",
        "model": "Qwen3.6-12B-IQ-Q4_K_M.gguf",
        "default_temp": 0.95,
        "max_tokens": 1024,
        "role": "dream",
    },
}
# Chat-basierte Models nutzen /v1/chat/completions
CHAT_PORTS = {8081, 8082}
def health() -> dict:
    """Prüft alle lokalen Modelle und gibt ihren Status zurück."""
    results = {}
    for port, info in MODELS.items():
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/chat/completions",
                data=json.dumps({
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                }).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                model = data.get("model", "?")
                results[port] = {
                    "status": "online",
                    "model": model,
                    "name": info["name"],
                    "role": info["role"],
                }
        except Exception as e:
            results[port] = {
                "status": "offline",
                "error": str(e)[:60],
                "name": info["name"],
                "role": info["role"],
            }
    return results


def call_llm(
    prompt: str,
    system_prompt: str = "",
    port: int = 8082,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    stop: Optional[list[str]] = None,
) -> Optional[str]:
    """
    Ruft ein lokales LLM über die OpenAI-kompatible API auf.

    Args:
        prompt: Der Prompt (user message)
        system_prompt: Optionaler System-Prompt
        port: 8080 (Dolphin 8B), 8081 (3B), 8082 (Qwen 9B)
        temperature: Kreativität 0.0-1.0 (default: modellspezifisch)
        max_tokens: Maximale Token (default: modellspezifisch)
        stop: Stop-Strings

    Returns:
        Antworttext oder None bei Fehler
    """
    config = MODELS.get(port, MODELS[8082])
    temp = temperature if temperature is not None else config["default_temp"]
    tokens = max_tokens if max_tokens is not None else config["max_tokens"]

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    if _game_mode_enabled():
        env = _load_env()
        api_key = (os.environ.get("OLLAMA_API_KEY") or env.get("OLLAMA_API_KEY") or "").strip()
        if not api_key:
            return None
        payload: dict[str, Any] = {
            "model": REMOTE_GAME_MODE_MODEL,
            "messages": messages,
            "temperature": temp,
            "max_tokens": tokens,
        }
        if stop:
            payload["stop"] = stop
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                _ollama_cloud_endpoint(),
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            return _extract_chat_text(result)
        except Exception:
            return None

    payload = {
        "messages": messages,
        "temperature": temp,
        "max_tokens": tokens,
    }

    if stop:
        payload["stop"] = stop

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            content = (
                result.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            if content and content.strip():
                return content.strip()
    except Exception:
        pass

    completion_prompt = f"{system_prompt.strip()}\n\n{prompt}".strip()
    completion_payload = {
        "prompt": completion_prompt,
        "temperature": temp,
        "max_tokens": tokens,
    }
    if stop:
        completion_payload["stop"] = stop

    data = json.dumps(completion_payload).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/completions",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            content = result.get("choices", [{}])[0].get("text", "")
            return content.strip() if content else None
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200]
        print(f"[local_llm] HTTP {e.code} auf Port {port}: {body}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[local_llm] Fehler auf Port {port}: {e}", file=sys.stderr)
        return None


# ── Bequeme Helfer ────────────────────────────────────────────


def think_deep(prompt: str, system: str = "", temp: float = 0.85) -> Optional[str]:
    """Bewusstes, tiefes Denken mit Dolphin 8B."""
    return call_llm(prompt, system_prompt=system, port=8082, temperature=temp)


def think_fast(prompt: str, system: str = "", temp: float = 0.7) -> Optional[str]:
    """Schnelle Antwort mit dem 3B Modell."""
    return call_llm(prompt, system_prompt=system, port=8081, temperature=temp)


def think_dream(prompt: str, system: str = "", temp: float = 0.95) -> Optional[str]:
    """Kreatives, traumartiges Denken mit Qwen 9B."""
    return call_llm(prompt, system_prompt=system, port=8082, temperature=temp)


# ── CLI ───────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Lokale LLM-Brücke")
    parser.add_argument("prompt", nargs="?", help="Prompt (optional)")
    parser.add_argument("--port", type=int, default=8082,
                        choices=[8081, 8082],
                        help="Model-Port (8081=MiniCPM, 8082=Qwen)")
    parser.add_argument("--system", default="", help="System-Prompt")
    parser.add_argument("--temp", type=float, default=None, help="Temperature")
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--health", action="store_true", help="Health-Check")
    args = parser.parse_args()

    if args.health:
        h = health()
        print(json.dumps(h, ensure_ascii=False, indent=2))
        sys.exit(0)

    if args.prompt:
        response = call_llm(
            args.prompt,
            system_prompt=args.system,
            port=args.port,
            temperature=args.temp,
            max_tokens=args.max_tokens,
        )
        if response:
            print(response)
        else:
            print("[Fehler] Keine Antwort vom Modell.", file=sys.stderr)
            sys.exit(1)
    else:
        parser.print_help()


