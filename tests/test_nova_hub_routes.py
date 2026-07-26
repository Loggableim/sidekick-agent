from __future__ import annotations

import importlib.util
import inspect
import sys
import warnings
from pathlib import Path

import os
import pytest

_COCKPIT_ROOT = os.getenv("SIDEKICK_COCKPIT_ROOT", "").strip()
pytestmark = pytest.mark.skipif(
    not _COCKPIT_ROOT,
    reason="external cockpit integration requires SIDEKICK_COCKPIT_ROOT",
)


MODULE_PATH = Path("C:/SidekickPortable/home/cockpit/dashboard_server.py")
COCKPIT_DIR = MODULE_PATH.parent


def load_dashboard_module():
    if str(COCKPIT_DIR) not in sys.path:
        sys.path.insert(0, str(COCKPIT_DIR))
    spec = importlib.util.spec_from_file_location("nova_dashboard_routes_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_stream_routes_are_registered_before_dashboard_static_mount():
    dashboard = load_dashboard_module()
    routes = list(dashboard.app.routes)
    paths = [getattr(route, "path", "") for route in routes]

    stream_index = paths.index("/api/stream.ts")
    live_index = paths.index("/api/live.ts")
    static_index = next(
        index for index, route in enumerate(routes)
        if getattr(route, "name", "") == "dashboard"
    )

    assert live_index < static_index
    assert stream_index < static_index


def test_dashboard_import_does_not_emit_fastapi_on_event_deprecation_warning():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        load_dashboard_module()

    assert not any("on_event is deprecated" in str(item.message) for item in caught)


def test_stream_ts_aliases_live_ts_implementation():
    source = MODULE_PATH.read_text(encoding="utf-8")

    assert "async def video_stream()" in source
    assert "return api_live_ts()" in source


def test_cast_watchdog_uses_shutdown_event_not_stale_stream_stop():
    dashboard = load_dashboard_module()
    startup_source = inspect.getsource(dashboard.startup)

    assert "_LIVE_STREAM_EVENT.is_set()" in startup_source
    assert "_live_refresh_stop.set()" not in startup_source


def test_cast_controls_do_not_blanket_kill_chrome():
    source = MODULE_PATH.read_text(encoding="utf-8")

    assert "taskkill" not in source
    assert "chrome*" not in source


def test_hub_cockpit_settings_live_under_sidekick_home():
    dashboard = load_dashboard_module()

    assert dashboard._COCKPIT_SETTINGS_FILE == dashboard.SIDEKICK_HOME / "cockpit" / ".cockpit_settings.json"
    assert dashboard._DEFAULT_SETTINGS["hub_ip"] == dashboard.PUBLIC_HOST


def test_webui_cockpit_settings_use_same_sidekick_home(monkeypatch, tmp_path):
    from web.api import profiles, routes

    home = tmp_path / "home"
    monkeypatch.setenv("SIDEKICK_HOME", str(home))
    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    profiles.refresh_profile_base_home_from_env()

    assert routes._cockpit_settings_path() == home / "cockpit" / ".cockpit_settings.json"


def test_webui_cockpit_settings_follow_sidekick_home(monkeypatch, tmp_path):
    from web.api import profiles, routes

    sidekick_home = tmp_path / "sidekick-home"
    monkeypatch.setenv("SIDEKICK_HOME", str(sidekick_home))
    profiles.refresh_profile_base_home_from_env()

    assert routes._cockpit_settings_path() == sidekick_home / "cockpit" / ".cockpit_settings.json"
