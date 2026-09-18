"""Regression tests for the WebUI auto-title gate.

Background: ``DEFAULT_SESSION_TITLE`` changed from ``"Untitled"`` to
``"New chat"`` (commit e96bd8d, 2026-07-09) while ``web/api/streaming.py``
kept comparing against the literal ``'New Chat'`` (capital C).  Every new
session therefore failed the "is this still a default title?" check, so
``_should_bg_title`` was always False and no title was ever generated —
sessions accumulated with ``title='New chat'`` and
``llm_title_generated=False``.

These tests pin the gate to the canonical ``is_default_session_title``
helper so a future default-title rename cannot silently disable
auto-titling again.
"""

from __future__ import annotations

from types import SimpleNamespace

from shared.sessions import DEFAULT_SESSION_TITLE


def _session(title, messages=None, llm_title_generated=False):
    return SimpleNamespace(
        title=title,
        messages=messages if messages is not None else [{"role": "user", "content": "hello world"}],
        llm_title_generated=llm_title_generated,
    )


def test_current_default_title_triggers_generation():
    """The live default ('New chat') must be recognised as a placeholder."""
    from web.api.streaming import _should_generate_background_title

    assert DEFAULT_SESSION_TITLE == "New chat"
    assert _should_generate_background_title(_session(DEFAULT_SESSION_TITLE)) is True


def test_legacy_default_titles_still_trigger_generation():
    from web.api.streaming import _should_generate_background_title

    assert _should_generate_background_title(_session("Untitled")) is True
    assert _should_generate_background_title(_session("New Chat")) is True
    assert _should_generate_background_title(_session("")) is True


def test_provisional_title_triggers_generation():
    """A title derived from the first user message is still a placeholder."""
    from web.api.streaming import _should_generate_background_title

    session = _session("hello world", messages=[{"role": "user", "content": "hello world"}])
    assert _should_generate_background_title(session) is True


def test_manual_title_is_preserved():
    from web.api.streaming import _should_generate_background_title

    assert _should_generate_background_title(_session("My custom title")) is False


def test_already_generated_title_is_not_regenerated():
    from web.api.streaming import _should_generate_background_title

    session = _session("LLM Generated Title", llm_title_generated=True)
    assert _should_generate_background_title(session) is False


def test_invalid_generated_title_is_healed():
    """A leaked chain-of-thought title must be regenerated even when flagged."""
    from web.api.streaming import _should_generate_background_title

    session = _session("The user is asking about titles", llm_title_generated=True)
    assert _should_generate_background_title(session) is True


def test_missing_messages_attribute_is_tolerated():
    from web.api.streaming import _should_generate_background_title

    session = SimpleNamespace(title=DEFAULT_SESSION_TITLE, llm_title_generated=False)
    assert _should_generate_background_title(session) is True


def test_no_hardcoded_capital_c_default_remains_in_streaming():
    """Guard against reintroducing the stale literal comparison."""
    from pathlib import Path

    source = Path(__file__).resolve().parent.parent / "web" / "api" / "streaming.py"
    text = source.read_text(encoding="utf-8")
    # The docstring may mention the legacy spelling; comparisons must not.
    assert "s.title == 'New Chat'" not in text
    assert "in ('Untitled', 'New Chat'" not in text
