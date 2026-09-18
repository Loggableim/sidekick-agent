"""Regression tests for the env-password hash cache (backlog item 6).

``get_password_hash`` hashes the ``SIDEKICK_WEBUI_PASSWORD`` env value with
PBKDF2/600k on every call (~240 ms), and ``is_auth_enabled`` calls it on every
API request. The result is cached; these tests pin that the cache never
returns a stale hash.
"""
from __future__ import annotations

import importlib

import pytest


def _fresh_auth(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    from web.api import auth as auth_module

    importlib.reload(auth_module)
    return auth_module


def test_env_password_hash_is_cached_but_still_correct(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_WEBUI_PASSWORD", "hunter2")
    auth = _fresh_auth(monkeypatch, tmp_path)

    first = auth.get_password_hash()
    second = auth.get_password_hash()
    assert first == second
    assert first == auth._hash_password("hunter2")

    assert auth.verify_password("hunter2") is True
    assert auth.verify_password("wrong") is False


def test_env_password_change_invalidates_the_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_WEBUI_PASSWORD", "first")
    auth = _fresh_auth(monkeypatch, tmp_path)

    assert auth.verify_password("first") is True

    monkeypatch.setenv("SIDEKICK_WEBUI_PASSWORD", "second")
    assert auth.verify_password("second") is True
    assert auth.verify_password("first") is False
    assert auth.get_password_hash() == auth._hash_password("second")


def test_removing_env_password_falls_back_to_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_WEBUI_PASSWORD", "envpw")
    auth = _fresh_auth(monkeypatch, tmp_path)

    assert auth.is_auth_enabled() is True

    monkeypatch.delenv("SIDEKICK_WEBUI_PASSWORD", raising=False)
    assert auth.is_auth_enabled() is False
    assert auth.get_password_hash() is None


def test_signing_key_is_stable_across_calls(monkeypatch, tmp_path):
    auth = _fresh_auth(monkeypatch, tmp_path)

    first = auth._signing_key()
    second = auth._signing_key()
    assert first == second
    assert len(first) == 32

    # A session created with the cached key still verifies.
    cookie = auth.create_session()
    assert auth.verify_session(cookie) is True
