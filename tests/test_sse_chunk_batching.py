"""Tests for SSE chunk batching in the route bridge (backlog item 9).

``_ResponseWriter.stream`` used one ``asyncio.to_thread`` call per chunk, and
each of those occupies a thread from anyio's default limiter (40 threads). A
busy stream could therefore consume the whole limiter. The stream now drains
whatever is already queued and yields it as one batch.
"""
from __future__ import annotations

import asyncio

import pytest

from web.api.fastapi_bridge import _ResponseWriter


def _collect(writer: _ResponseWriter) -> list[bytes]:
    async def run() -> list[bytes]:
        return [chunk async for chunk in writer.stream()]

    return asyncio.run(run())


def test_burst_is_batched_and_content_preserved() -> None:
    writer = _ResponseWriter()
    expected = b""
    for index in range(10):
        payload = f"chunk-{index}|".encode()
        expected += payload
        writer.write(payload)
    writer.finish()

    chunks = _collect(writer)

    assert b"".join(chunks) == expected
    assert len(chunks) == 1, f"a queued burst should yield once, got {len(chunks)}"


def test_live_chunks_are_delivered_in_order() -> None:
    writer = _ResponseWriter()

    async def scenario() -> list[bytes]:
        received: list[bytes] = []

        async def consume() -> None:
            async for chunk in writer.stream():
                received.append(chunk)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        for index in range(5):
            writer.write(f"live-{index}|".encode())
            await asyncio.sleep(0.02)
        writer.finish()
        await task
        return received

    chunks = asyncio.run(scenario())

    assert b"".join(chunks) == b"".join(f"live-{i}|".encode() for i in range(5))


def test_finish_terminates_the_stream_without_hanging() -> None:
    writer = _ResponseWriter()
    writer.finish()

    async def run() -> list[bytes]:
        return await asyncio.wait_for(
            _collect_async(writer), timeout=5
        )

    async def _collect_async(w: _ResponseWriter) -> list[bytes]:
        return [chunk async for chunk in w.stream()]

    assert asyncio.run(run()) == []


def test_batching_reduces_thread_hops(monkeypatch) -> None:
    """A 20-chunk burst must not cost 20 thread hops."""
    hops: list[int] = []
    original = asyncio.to_thread

    async def counting(fn, *args, **kwargs):
        hops.append(1)
        return await original(fn, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", counting)

    writer = _ResponseWriter()
    for _ in range(20):
        writer.write(b"x")
    writer.finish()
    _collect(writer)

    assert len(hops) <= 3, f"expected batched reads, got {len(hops)} thread hops"
