"""Regression tests for the 2026-09-19 smoke-reload fix bundle.

Three bugs, one session:

1. ``_scan_fs_for_spaces()`` advertised directories whose names are not valid
   slugs (``_bridge``, ``_bewusstsein_archived_20260605``) -> per-space API
   calls 400 -> console errors on every page load. Fixed in space_engine.py;
   pinned by ``test_space_slug_validation.py::test_fs_scan_skips_invalid_slug_dirs``.

2. The composer browser-drawer button called ``browserToggleDrawer()``
   directly, but the function lives in lazy-loaded ``browser.js``
   (feature-loader, backlog item 13) -> silent ReferenceError, drawer never
   opens, ``browserGetState`` undefined -> every ``/api/browser/permission``
   POST 400s with "session_id is required" -> the whole browser-control
   smoke cluster fails. Same class of bug the sibling commit 96d06e6 fixed
   for boot.js/loadWorkspaceList.

3. The July PR #7 merge (1af430c) dropped the ``#browserQaCard`` markup AND
   its ``.browser-qa-*`` styles from index.html/style.css while
   ``browser.js`` kept rendering into them (null-guarded no-ops). The
   documented "merge drops markup from index.html" failure mode: contract
   tests on browser.js stayed green, the live QA card silently vanished.
"""

from __future__ import annotations

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 2. Composer drawer button must load browser.js before toggling
# ---------------------------------------------------------------------------


def test_composer_drawer_button_uses_lazy_load_shim():
    index_html = _read("web/static/index.html")
    # The button must not call the lazy-loaded function directly.
    assert 'onclick="browserToggleDrawer()"' not in index_html
    assert 'onclick="browserToggleDrawerFromComposer()"' in index_html
    # The shim must load browser.js via the feature loader before toggling.
    assert "function browserToggleDrawerFromComposer()" in index_html
    assert "window.__sidekickLoadPanelScripts('browser')" in index_html


def test_workflow_menu_browser_action_uses_lazy_load_shim():
    index_html = _read("web/static/index.html")
    assert (
        "if (String(action || '') === 'browser-toggle') return browserToggleDrawerFromComposer();"
        in index_html
    )


# ---------------------------------------------------------------------------
# 3. Browser QA card markup + styles must exist (merge-drop regression)
# ---------------------------------------------------------------------------


def test_browser_qa_card_markup_present_in_index_html():
    index_html = _read("web/static/index.html")
    for element_id in (
        "browserQaCard",
        "browserQaCardStatus",
        "browserQaCardUrl",
        "browserQaCardMetrics",
        "browserQaFixBtn",
        "browserQaRetestBtn",
        "browserQaReproBtn",
        "browserQaCopyBtn",
        "browserQaDetailsBtn",
        "browserQaClearBtn",
        "browserQaDetails",
    ):
        assert f'id="{element_id}"' in index_html, f"#{element_id} missing from index.html"
    # The card sits between its two neighbours in the drawer.
    trace_pos = index_html.find('id="browserActionTrace"')
    card_pos = index_html.find('id="browserQaCard"')
    stage_pos = index_html.find('id="browserStageWrap"')
    assert 0 < trace_pos < card_pos < stage_pos


def test_browser_qa_card_styles_present_in_style_css():
    style_css = _read("web/static/style.css")
    for selector in (
        ".browser-qa-card {",
        ".browser-qa-main {",
        ".browser-qa-metrics {",
        ".browser-qa-actions {",
        ".browser-qa-action-btn {",
        ".browser-qa-detail-grid {",
    ):
        assert selector in style_css, f"missing CSS rule: {selector}"


def test_browser_qa_card_js_handlers_are_exported():
    browser_js = _read("web/static/browser.js")
    for fn in (
        "browserFixFindingsToChat",
        "browserRetestCurrentPageToChat",
        "browserQaReproToChat",
        "browserCopyQaReport",
        "browserToggleQaDetails",
        "browserClearQaReports",
        "browserGetQaState",
        "browserGetState",
    ):
        assert f"window.{fn} = {fn};" in browser_js, f"{fn} not exported to window"


# ---------------------------------------------------------------------------
# Smoke-script contract: legacy-collision check must not false-fail on the
# current dual-attribute renderer (data-space-slug + data-titlebar-space-slug).
# ---------------------------------------------------------------------------


def test_smoke_legacy_collision_check_requires_missing_new_attribute():
    smoke = _read("scripts/browser_webui_smoke.py")
    # The naive selector that counted every item with the old attribute is
    # gone (it false-fails whenever the dropdown has rendered).
    assert 'legacy_titlebar_collision = _unique_count' not in smoke
    # The evaluate-based check filters on the NEW attribute being absent.
    assert "getAttribute('data-titlebar-space-slug') !== slug" in smoke