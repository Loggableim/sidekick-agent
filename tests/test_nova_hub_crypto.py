from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import os
import pytest

_COCKPIT_ROOT = os.getenv("SIDEKICK_COCKPIT_ROOT", "").strip()
pytestmark = pytest.mark.skipif(
    not _COCKPIT_ROOT,
    reason="external cockpit integration requires SIDEKICK_COCKPIT_ROOT",
)
from urllib.parse import parse_qs


MODULE_PATH = Path("C:/SidekickPortable/home/cockpit/dashboard_server.py")
COCKPIT_DIR = MODULE_PATH.parent


def load_dashboard_module():
    if str(COCKPIT_DIR) not in sys.path:
        sys.path.insert(0, str(COCKPIT_DIR))
    spec = importlib.util.spec_from_file_location("nova_dashboard_crypto_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_binance_rsa_signature_is_url_encoded(tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    dashboard = load_dashboard_module()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_path = tmp_path / "binance_private.pem"
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )

    qs = dashboard._binance_sign({"recvWindow": 5000, "timestamp": 123}, str(key_path))

    assert "signature=" in qs
    assert "%3D" in qs
    assert parse_qs(qs)["signature"][0]
    assert "signature=" + parse_qs(qs)["signature"][0] not in qs


def test_dashboard_does_not_hardcode_trading_api_keys():
    text = MODULE_PATH.read_text(encoding="utf-8")

    for name in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "BINANCE_API_KEY", "BINANCE_SPOT_KEY"):
        assert not re.search(rf"^{name}\s*=\s*['\"][^'\"]+['\"]", text, flags=re.MULTILINE)


def test_secret_value_prefers_environment(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "alpaca-env-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "alpaca-env-secret")
    monkeypatch.setenv("BINANCE_FUTURES_API_KEY", "futures-env-key")
    monkeypatch.setenv("BINANCE_SPOT_API_KEY", "spot-env-key")

    dashboard = load_dashboard_module()

    assert dashboard.ALPACA_API_KEY == "alpaca-env-key"
    assert dashboard.ALPACA_SECRET_KEY == "alpaca-env-secret"
    assert dashboard.BINANCE_API_KEY == "futures-env-key"
    assert dashboard.BINANCE_SPOT_KEY == "spot-env-key"


def test_binance_auth_error_has_actionable_message():
    dashboard = load_dashboard_module()

    payload = dashboard._binance_error_payload({"error": "HTTP 401", "status_code": 401}, "Futures")

    assert payload == {
        "error": "HTTP 401",
        "status_code": 401,
        "message": "Futures-Key pruefen",
    }


def test_binance_invalid_key_error_calls_out_permissions():
    dashboard = load_dashboard_module()

    payload = dashboard._binance_error_payload(
        {
            "error": "HTTP 401",
            "status_code": 401,
            "detail": '{"code":-2015,"msg":"Invalid API-key, IP, or permissions for action."}',
        },
        "Futures",
    )

    assert payload["message"] == "Futures-Key/IP/Berechtigung pruefen"


def test_binance_missing_credentials_are_actionable():
    dashboard = load_dashboard_module()

    payload = dashboard._binance_error_payload({"error": "missing_api_key"}, "Spot")

    assert payload == {"error": "missing_api_key", "message": "Spot-Credentials fehlen"}
