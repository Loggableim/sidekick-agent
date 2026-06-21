from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace


MODULE_PATH = Path(r"C:\HermesPortable\home\cockpit\dashboard_server.py")
COCKPIT_DIR = MODULE_PATH.parent
DASHBOARD_DIR = COCKPIT_DIR / "dashboard"


def load_dashboard_module():
    if str(COCKPIT_DIR) not in sys.path:
        sys.path.insert(0, str(COCKPIT_DIR))
    spec = importlib.util.spec_from_file_location("nova_dashboard_metrics_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sample(pid: int, engine: int, value, *, luid: str = "0x00000000_0x00014c23"):
    return {
        "instance_name": f"pid_{pid}_luid_{luid}_phys_0_eng_{engine}_engtype_3D",
        "cooked_value": value,
    }


def test_gpu_utilization_sums_processes_per_engine_and_uses_busiest_engine():
    dashboard = load_dashboard_module()

    samples = [
        _sample(100, 0, 21.5),
        _sample(200, 0, 17.25),
        _sample(300, 5, 22.0),
    ]

    assert dashboard._aggregate_gpu_engine_utilization(samples) == 38.8


def test_gpu_utilization_accepts_localized_values_and_clamps_to_percent_range():
    dashboard = load_dashboard_module()

    samples = [_sample(100, 0, "60,25"), _sample(200, 0, "55,5")]

    assert dashboard._aggregate_gpu_engine_utilization(samples) == 100.0
    assert dashboard._aggregate_gpu_engine_utilization([]) is None


def test_query_gpu_metrics_reads_json_counter_samples_without_percent_rescaling(monkeypatch):
    dashboard = load_dashboard_module()
    engine_samples = [_sample(100, 0, 21.5), _sample(200, 0, 17.25)]
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 1:
            return SimpleNamespace(returncode=0, stdout=json.dumps(engine_samples))
        return SimpleNamespace(returncode=0, stdout="0|0")

    monkeypatch.setattr(dashboard.subprocess, "run", fake_run)

    metrics = dashboard._query_gpu_metrics()

    assert metrics["util_pct"] == 38.8
    assert "ConvertTo-Json -Compress" in calls[0][0][-1]
    assert calls[0][1]["timeout"] >= 8


def test_gpu_summary_card_uses_one_value_and_rotates_every_six_seconds():
    index_html = (DASHBOARD_DIR / "index.html").read_text(encoding="utf-8")
    app_js = (DASHBOARD_DIR / "app.js").read_text(encoding="utf-8")

    gpu_card = index_html[index_html.index('<div class="sc gpu">') : index_html.index('<div class="sc dsk">')]

    assert 'id="sGpuLabel"' in gpu_card
    assert 'id="sGpu"' in gpu_card
    assert 'id="sGpuL"' not in gpu_card
    assert "const GPU_SUMMARY_ROTATION_MS=6000;" in app_js
    assert "setInterval(toggleGpuSummary,GPU_SUMMARY_ROTATION_MS);" in app_js
    assert "function renderGpuSummary()" in app_js
    assert "function toggleGpuSummary()" in app_js
