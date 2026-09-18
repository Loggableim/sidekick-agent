"""Tests for the /api/sessions ETag and cache TTL (backlog item 28).

The sidebar polls /api/sessions every 5s with a large payload (2.6 MB in a
real profile). An unchanged list should answer 304 instead of re-sending it,
and the server-side list cache should cover a poll interval.
"""
from __future__ import annotations

import pytest

TestClient = pytest.importorskip("fastapi.testclient").TestClient


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    from cli import web_server

    client = TestClient(web_server.app)
    headers = {web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN}
    return client, headers


def test_sessions_response_carries_an_etag(monkeypatch, tmp_path):
    client, headers = _client(monkeypatch, tmp_path)

    response = client.get("/api/sessions?fields=sidebar", headers=headers)

    assert response.status_code == 200
    assert response.headers.get("etag"), "no ETag on the sessions response"


def test_matching_etag_answers_304_without_a_body(monkeypatch, tmp_path):
    client, headers = _client(monkeypatch, tmp_path)

    first = client.get("/api/sessions?fields=sidebar", headers=headers)
    etag = first.headers["etag"]

    revalidated = client.get(
        "/api/sessions?fields=sidebar",
        headers={**headers, "If-None-Match": etag},
    )

    assert revalidated.status_code == 304
    assert revalidated.content == b""
    assert revalidated.headers.get("etag") == etag


def test_stale_etag_returns_the_full_body(monkeypatch, tmp_path):
    client, headers = _client(monkeypatch, tmp_path)

    response = client.get(
        "/api/sessions?fields=sidebar",
        headers={**headers, "If-None-Match": 'W/"stale"'},
    )

    assert response.status_code == 200
    assert response.content


def test_etag_is_stable_across_identical_requests(monkeypatch, tmp_path):
    """A time-based validator would change on every request and never match."""
    client, headers = _client(monkeypatch, tmp_path)

    first = client.get("/api/sessions?fields=sidebar", headers=headers)
    second = client.get("/api/sessions?fields=sidebar", headers=headers)

    assert first.headers["etag"] == second.headers["etag"]


def test_session_list_cache_ttl_covers_a_poll_interval(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    from web.api import models

    assert models._SESSION_LIST_CACHE_TTL >= 4.0, (
        "the TTL must cover the 5s sidebar poll instead of rebuilding every time"
    )
