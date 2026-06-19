from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from web.api import config as cfg

logger = logging.getLogger(__name__)


_LOCAL_MODEL_PROCESS_MARKERS = (
    "llama",
    "llama-server",
    "llamacpp",
    "kobold",
    "koboldcpp",
    "vllm",
    "text-generation",
    "oobabooga",
    "lm studio",
    "lmstudio",
)

_LOCAL_IMAGE_QUEUE_PORTS = (8283,)
_LOCAL_IMAGE_QUEUE_PROCESS_MARKERS = (
    "local_gen_queue.py",
    "gen_queue_worker.py",
    "horde_worker.py",
)


def _normalize_ollama_root(raw_url: str) -> str:
    value = str(raw_url or "").strip()
    if not value:
        value = "http://127.0.0.1:11434"
    if "://" not in value:
        value = "http://" + value
    parsed = urllib.parse.urlparse(value)
    path = (parsed.path or "").rstrip("/")
    if path.endswith("/v1"):
        path = path[:-3]
    rebuilt = parsed._replace(path=path, params="", query="", fragment="")
    return urllib.parse.urlunparse(rebuilt).rstrip("/")


def _ollama_base_urls() -> list[str]:
    urls = {_normalize_ollama_root(os.getenv("OLLAMA_HOST", ""))}
    try:
        conf = cfg.get_config()
    except Exception:
        conf = {}

    def consider(value: Any) -> None:
        text = str(value or "").strip()
        if text and "ollama" in text.lower():
            urls.add(_normalize_ollama_root(text))

    if isinstance(conf, dict):
        model_cfg = conf.get("model") or {}
        if isinstance(model_cfg, dict):
            consider(model_cfg.get("base_url"))
        providers = conf.get("providers") or {}
        if isinstance(providers, dict):
            for key, entry in providers.items():
                if str(key).strip().lower() == "ollama" and isinstance(entry, dict):
                    consider(entry.get("base_url"))
        custom = conf.get("custom_providers") or []
        if isinstance(custom, list):
            for entry in custom:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name") or entry.get("provider") or "").lower()
                base_url = str(entry.get("base_url") or "")
                if "ollama" in name or "ollama" in base_url.lower():
                    consider(base_url)
    return sorted(urls)


def _json_request(base_url: str, path: str, payload: dict | None = None, timeout: float = 2.0) -> Any:
    data = None
    method = "GET"
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        method = "POST"
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        _normalize_ollama_root(base_url) + path,
        data=data,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", errors="replace")
    return json.loads(body) if body else {}


def _json_request_url(url: str, payload: dict | None = None, timeout: float = 2.0) -> Any:
    data = None
    method = "GET"
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        method = "POST"
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(str(url), data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", errors="replace")
    return json.loads(body) if body else {}


def _tcp_endpoint_open(url: str, timeout: float = 0.25) -> bool:
    try:
        parsed = urllib.parse.urlparse(str(url))
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _loaded_ollama_models(base_url: str) -> list[str]:
    try:
        payload = _json_request(base_url, "/api/ps", timeout=1.5)
    except Exception:
        return []
    models = payload.get("models") if isinstance(payload, dict) else []
    names: list[str] = []
    if isinstance(models, list):
        for item in models:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("model") or "").strip()
            if name and name not in names:
                names.append(name)
    return names


def _unload_ollama_model(base_url: str, model: str) -> dict:
    model_name = str(model or "").strip()
    if not model_name:
        return {"ok": False, "model": model_name, "error": "empty model"}
    try:
        _json_request(base_url, "/api/generate", {"model": model_name, "keep_alive": 0}, timeout=5.0)
        return {"ok": True, "model": model_name, "method": "api"}
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        logger.debug("Ollama API unload failed for %s via %s: %r", model_name, base_url, exc)
    except Exception as exc:
        logger.debug("Ollama API unload failed for %s via %s: %r", model_name, base_url, exc)

    try:
        completed = subprocess.run(
            ["ollama", "stop", model_name],
            capture_output=True,
            text=True,
            timeout=8,
        )
        return {
            "ok": completed.returncode == 0,
            "model": model_name,
            "method": "cli",
            "returncode": completed.returncode,
            "stderr": (completed.stderr or "")[:240],
        }
    except Exception as exc:
        return {"ok": False, "model": model_name, "method": "cli", "error": repr(exc)[:240]}


def _unload_ollama_models() -> dict:
    unloaded: list[dict] = []
    checked: list[str] = []
    for base_url in _ollama_base_urls():
        checked.append(base_url)
        for model in _loaded_ollama_models(base_url):
            result = _unload_ollama_model(base_url, model)
            result.setdefault("base_url", base_url)
            unloaded.append(result)
    return {"checked": checked, "unloaded": unloaded}


def _cancel_stream(stream_id: str) -> bool:
    from web.api.streaming import cancel_stream

    return cancel_stream(stream_id)


def _active_local_stream_ids() -> list[str]:
    local: list[str] = []
    with cfg.ACTIVE_RUNS_LOCK:
        runs = dict(cfg.ACTIVE_RUNS or {})
    for stream_id, raw in runs.items():
        run = raw if isinstance(raw, dict) else {}
        provider = str(run.get("provider") or "").strip()
        base_url = str(run.get("base_url") or "").strip()
        if cfg.game_mode_blocks_local_model_request(provider, base_url):
            local.append(str(stream_id))
    return local


def _nova_local_model_ports() -> set[int]:
    ports: set[int] = set()
    try:
        from web.api.nova_lifecycle import MODEL_STRATEGY
    except Exception:
        return ports
    for spec in MODEL_STRATEGY.values():
        if not isinstance(spec, dict):
            continue
        try:
            ports.add(int(spec.get("port")))
        except Exception:
            pass
    return ports


def _process_looks_like_local_model_server(proc: Any) -> bool:
    try:
        bits = [proc.name() or ""]
        bits.extend(proc.cmdline() or [])
    except Exception:
        return False
    text = " ".join(str(bit) for bit in bits).lower()
    return any(marker in text for marker in _LOCAL_MODEL_PROCESS_MARKERS)


def _terminate_known_local_model_servers() -> list[dict]:
    ports = _nova_local_model_ports()
    if not ports:
        return []
    try:
        import psutil
    except Exception as exc:
        return [{"ok": False, "error": f"psutil unavailable: {exc!r}"}]

    terminated: list[dict] = []
    seen_pids: set[int] = set()
    try:
        connections = psutil.net_connections(kind="inet")
    except Exception as exc:
        return [{"ok": False, "error": f"net_connections failed: {exc!r}"}]

    for conn in connections:
        try:
            if conn.status != psutil.CONN_LISTEN:
                continue
            if not conn.laddr or int(conn.laddr.port) not in ports:
                continue
            pid = int(conn.pid or 0)
            if not pid or pid in seen_pids:
                continue
            seen_pids.add(pid)
            proc = psutil.Process(pid)
            if not _process_looks_like_local_model_server(proc):
                terminated.append({"ok": False, "pid": pid, "port": int(conn.laddr.port), "skipped": "unrecognized process"})
                continue
            name = proc.name()
            proc.terminate()
            try:
                proc.wait(timeout=5)
                ok = True
            except psutil.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
                ok = True
            terminated.append({"ok": ok, "pid": pid, "port": int(conn.laddr.port), "process": name})
        except Exception as exc:
            terminated.append({"ok": False, "error": repr(exc)[:240]})
    return terminated


def _flush_local_image_generation_queue(base_url: str) -> dict:
    if not _tcp_endpoint_open(base_url):
        return {"ok": False, "skipped": "not_listening"}
    try:
        payload = _json_request_url(f"{base_url.rstrip('/')}/flush", {}, timeout=2.0)
        if isinstance(payload, dict):
            payload.setdefault("ok", True)
            return payload
        return {"ok": True, "response": payload}
    except Exception as exc:
        return {"ok": False, "error": repr(exc)[:240]}


def _process_looks_like_local_image_generation_queue(proc: Any) -> bool:
    try:
        bits = [proc.name() or ""]
        bits.extend(proc.cmdline() or [])
    except Exception:
        return False
    text = " ".join(str(bit) for bit in bits).replace("\\", "/").lower()
    return any(marker in text for marker in _LOCAL_IMAGE_QUEUE_PROCESS_MARKERS)


def _terminate_process_tree(proc: Any) -> dict:
    try:
        children = list(proc.children(recursive=True))
    except Exception:
        children = []
    targets = children + [proc]
    result: dict[str, Any] = {
        "ok": True,
        "pid": getattr(proc, "pid", None),
        "process": "",
        "children": [getattr(child, "pid", None) for child in children],
    }
    try:
        result["process"] = proc.name()
    except Exception:
        pass

    for item in targets:
        try:
            item.terminate()
        except Exception:
            pass
    for item in targets:
        try:
            item.wait(timeout=4)
        except Exception:
            try:
                item.kill()
                item.wait(timeout=4)
            except Exception as exc:
                result["ok"] = False
                result.setdefault("errors", []).append({"pid": getattr(item, "pid", None), "error": repr(exc)[:180]})
    return result


def _terminate_local_image_generation_queue_processes(ports: set[int] | None = None) -> list[dict]:
    target_ports = set(ports or _LOCAL_IMAGE_QUEUE_PORTS)
    try:
        import psutil
    except Exception as exc:
        return [{"ok": False, "error": f"psutil unavailable: {exc!r}"}]

    terminated: list[dict] = []
    seen_pids: set[int] = set()
    try:
        connections = psutil.net_connections(kind="inet")
    except Exception as exc:
        return [{"ok": False, "error": f"net_connections failed: {exc!r}"}]

    for conn in connections:
        try:
            if conn.status != psutil.CONN_LISTEN:
                continue
            if not conn.laddr or int(conn.laddr.port) not in target_ports:
                continue
            pid = int(conn.pid or 0)
            if not pid or pid in seen_pids:
                continue
            seen_pids.add(pid)
            proc = psutil.Process(pid)
            port = int(conn.laddr.port)
            if not _process_looks_like_local_image_generation_queue(proc):
                terminated.append({"ok": False, "pid": pid, "port": port, "skipped": "unrecognized process"})
                continue
            item = _terminate_process_tree(proc)
            item["port"] = port
            terminated.append(item)
        except Exception as exc:
            terminated.append({"ok": False, "error": repr(exc)[:240]})

    for proc in psutil.process_iter(["pid"]):
        try:
            pid = int(getattr(proc, "pid", 0) or 0)
            if not pid or pid in seen_pids or pid == os.getpid():
                continue
            if not _process_looks_like_local_image_generation_queue(proc):
                continue
            seen_pids.add(pid)
            item = _terminate_process_tree(proc)
            item["match"] = "commandline"
            terminated.append(item)
        except Exception as exc:
            terminated.append({"ok": False, "error": repr(exc)[:240]})
    return terminated


def _release_local_image_generation_queues() -> dict:
    queues: list[dict] = []
    for port in _LOCAL_IMAGE_QUEUE_PORTS:
        base_url = f"http://127.0.0.1:{port}"
        queues.append({"base_url": base_url, "flush": _flush_local_image_generation_queue(base_url)})
    return {
        "queues": queues,
        "terminated": _terminate_local_image_generation_queue_processes(set(_LOCAL_IMAGE_QUEUE_PORTS)),
    }


def release_game_mode_resources() -> dict:
    """Best-effort VRAM release for Game Mode activation.

    This cancels local-provider WebUI streams and unloads local Ollama models.
    It only terminates external processes when they are listening on a known
    Sidekick/Nova GPU model port and their process metadata matches a model
    server marker.
    """
    cancelled: list[str] = []
    cancel_errors: list[dict] = []
    for stream_id in _active_local_stream_ids():
        try:
            if _cancel_stream(stream_id):
                cancelled.append(stream_id)
        except Exception as exc:
            cancel_errors.append({"stream_id": stream_id, "error": repr(exc)[:240]})

    return {
        "cancelled_local_streams": cancelled,
        "cancel_errors": cancel_errors,
        "ollama": _unload_ollama_models(),
        "local_model_servers": _terminate_known_local_model_servers(),
        "image_generation_queue": _release_local_image_generation_queues(),
    }
