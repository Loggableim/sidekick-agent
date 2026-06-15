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
