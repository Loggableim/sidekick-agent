from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path


MODULE_PATH = Path("C:/HermesPortable/home/cockpit/dashboard_server.py")
COCKPIT_DIR = MODULE_PATH.parent


def load_dashboard_module():
    if str(COCKPIT_DIR) not in sys.path:
        sys.path.insert(0, str(COCKPIT_DIR))
    spec = importlib.util.spec_from_file_location("nova_dashboard_notifications_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_notify_accepts_level_and_duration():
    dashboard = load_dashboard_module()
    with dashboard._notify_lock:
        dashboard._notify_queue.clear()

    result = asyncio.run(dashboard.api_notify({
        "title": "Warnung",
        "message": "Details",
        "level": "warning",
        "source": "nova",
        "duration": 3,
    }))

    assert result["ok"] is True
    assert result["notification"]["level"] == "warning"
    assert result["notification"]["duration"] == 3


def test_notify_keeps_type_backward_compatibility():
    dashboard = load_dashboard_module()

    result = asyncio.run(dashboard.api_notify({
        "title": "Fehler",
        "message": "Details",
        "type": "error",
    }))

    assert result["notification"]["level"] == "error"


def test_ticker_push_accepts_text_alias():
    dashboard = load_dashboard_module()
    with dashboard._ticker_lock:
        dashboard._ticker_queue.clear()

    result = asyncio.run(dashboard.api_ticker_push({
        "text": "Hub Smoke ok",
        "source": "nova",
    }))

    assert result == {"status": "ok"}
    with dashboard._ticker_lock:
        assert dashboard._ticker_queue[-1]["msg"] == "Hub Smoke ok"
