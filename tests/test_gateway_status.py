from __future__ import annotations

import gateway.status as gateway_status
from runtime.gateway.status import _record_looks_like_gateway


def test_gateway_status_forwarder_exports_pid_exists() -> None:
    assert callable(gateway_status._pid_exists)


def test_gateway_record_accepts_sidekick_app_module_entrypoint() -> None:
    record = {
        "kind": "sidekick-gateway",
        "argv": [
            r"C:\sidekick\sidekick\sidekick_app\__main__.py",
            "gateway",
            "run",
            "--replace",
            "--quiet",
        ],
    }

    assert _record_looks_like_gateway(record) is True


def test_gateway_record_accepts_python_module_invocation() -> None:
    record = {
        "kind": "sidekick-gateway",
        "argv": [
            "python",
            "-m",
            "sidekick_app",
            "gateway",
            "run",
        ],
    }

    assert _record_looks_like_gateway(record) is True


def test_windows_gateway_status_skips_verbose_task_query_by_default(monkeypatch, capsys) -> None:
    from cli import gateway_windows

    monkeypatch.setattr(gateway_windows.sys, "platform", "win32")
    monkeypatch.setattr(gateway_windows, "get_task_name", lambda: "Sidekick_Gateway")
    monkeypatch.setattr(gateway_windows, "is_task_registered", lambda: True)
    monkeypatch.setattr(gateway_windows, "is_startup_entry_installed", lambda: False)
    monkeypatch.setattr(gateway_windows, "_gateway_pids", lambda: [])

    calls = []

    def query_task_status():
        calls.append(True)
        raise AssertionError("default status should not run verbose schtasks query")

    monkeypatch.setattr(gateway_windows, "query_task_status", query_task_status)

    gateway_windows.status(deep=False)

    assert calls == []
    assert "Scheduled Task registered" in capsys.readouterr().out


def test_windows_gateway_status_queries_task_details_when_deep(monkeypatch, capsys) -> None:
    from cli import gateway_windows

    monkeypatch.setattr(gateway_windows.sys, "platform", "win32")
    monkeypatch.setattr(gateway_windows, "get_task_name", lambda: "Sidekick_Gateway")
    monkeypatch.setattr(gateway_windows, "is_task_registered", lambda: True)
    monkeypatch.setattr(gateway_windows, "is_startup_entry_installed", lambda: False)
    monkeypatch.setattr(gateway_windows, "_gateway_pids", lambda: [])
    monkeypatch.setattr(gateway_windows, "get_task_script_path", lambda: r"C:\sidekick\gateway.cmd")
    monkeypatch.setattr(gateway_windows, "get_startup_entry_path", lambda: r"C:\Users\logga\Startup\Sidekick_Gateway.cmd")

    calls = []

    def query_task_status():
        calls.append(True)
        return {"status": "Ready", "last run result": "0x0"}

    monkeypatch.setattr(gateway_windows, "query_task_status", query_task_status)

    gateway_windows.status(deep=True)

    out = capsys.readouterr().out
    assert calls == [True]
    assert "Status: Ready" in out
    assert "Last Run Result: 0x0" in out
