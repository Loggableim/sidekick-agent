from pathlib import Path


def test_browser_qa_stale_card_gates_fix_and_repro_actions():
    browser_js = Path("web/static/browser.js").read_text(encoding="utf-8")

    assert "if (stale || scopeUnknown)" in browser_js
    assert "const staleReason = stale ? 'stale' : 'unknown-scope';" in browser_js
    assert "text: 'Retest first'" in browser_js
    assert "Retest before fixing. QA scope is stale or the current browser URL was not observed." in browser_js
    assert "Retest before creating a repro. QA scope is stale or the current browser URL was not observed." in browser_js
    assert "card.dataset.stale = stale ? '1' : '0';" in browser_js
    assert "card.dataset.scopeUnknown = scopeUnknown ? '1' : '0';" in browser_js


def test_browser_qa_header_menu_uses_rendered_stale_scope():
    browser_js = Path("web/static/browser.js").read_text(encoding="utf-8")

    refresh_start = browser_js.index("function _browserRefreshHeaderMenu()")
    refresh_end = browser_js.index("function browserSetDrawerOpen", refresh_start)
    refresh_body = browser_js[refresh_start:refresh_end]

    assert "const renderedScopeRisk = {" in refresh_body
    assert "qaCard.dataset.stale === '1'" in refresh_body
    assert "qaCard.dataset.scopeUnknown === '1'" in refresh_body
    assert "const rememberedScopeRisk = hasRenderedQaCard ? renderedScopeRisk" in refresh_body
    assert "if (rememberedScopeRisk.risk)" in refresh_body
    assert "text: 'Retest first'" in refresh_body
    assert "_browserApplySharedFixReproUi(fixUi, reproUi);" in refresh_body


def test_browser_js_cache_key_bumped_for_qa_gate_contract():
    index_html = Path("web/static/index.html").read_text(encoding="utf-8")

    assert 'static/browser.js?v=__WEBUI_VERSION__&split=70' in index_html


def test_browser_webui_smoke_action_surfaces_report_and_visual_evidence():
    browser_js = Path("web/static/browser.js").read_text(encoding="utf-8")
    routes_py = Path("web/api/routes.py").read_text(encoding="utf-8")
    smoke_py = Path("scripts/browser_webui_smoke.py").read_text(encoding="utf-8")

    assert "function _browserBuildWebuiSmokeReport(result)" in browser_js
    assert "async function browserRunWebuiSmokeToChat()" in browser_js
    assert "showToast('Running WebUI smoke...'" in browser_js
    assert "await switchPanel('chat', {bypassSettingsGuard: true})" in browser_js
    assert "browserSetDrawerOpen(true)" in browser_js
    assert "api('/api/browser/webui-smoke'" in browser_js
    assert "const text = _browserBuildWebuiSmokeReport(result);" in browser_js
    assert "_browserRenderWebuiSmokeCard(result, text);" in browser_js
    assert "_browserSetComposerText(text)" in browser_js
    assert "Approval mode: " in browser_js
    assert "window._approvalMode" in browser_js
    assert "Active goal: " in browser_js
    assert "window._goalState" in browser_js
    assert "WebUI smoke passed" in browser_js
    assert "WebUI smoke failed; report ready" in browser_js
    assert "failure_screenshot" in browser_js
    assert "screenshot" in browser_js
    assert "approval_mode = _current_approval_mode()" in routes_py
    assert "active_goal = _active_goal_context(session_id)" in routes_py
    assert 'payload.setdefault("approval_mode", approval_mode)' in routes_py
    assert 'payload.setdefault("active_goal", active_goal)' in routes_py
    assert "persistent goal reload resumes automatically after refresh" in smoke_py
    assert "/api/chat/cancel?stream_id=" in smoke_py
    assert 'cleanup_detail["cancel"] = _get_json(' in smoke_py
    assert "/api/session/delete" in smoke_py
    assert "_workspace_scoped_url(" in smoke_py
    assert "workspace_slug" in smoke_py
    assert "strip().lower()" in smoke_py
    assert "no visible sessions in this space" in smoke_py
    assert "cleanup_sid" in smoke_py
    assert "deleted_verified" in smoke_py
    assert "delete_attempts" in smoke_py


def test_browser_fix_prompt_requires_root_cause_and_retest_evidence():
    browser_js = Path("web/static/browser.js").read_text(encoding="utf-8")

    prompt_start = browser_js.index("function _browserBuildFixFindingsPrompt")
    prompt_end = browser_js.index("async function browserTestCurrentPageToChat", prompt_start)
    prompt_body = browser_js[prompt_start:prompt_end]

    assert "Nutze den letzten Browser Test Report als Evidence und schliesse den Browser-Fix-Loop." in prompt_body
    assert "Finde die betroffenen Dateien im aktuellen Workspace." in prompt_body
    assert "Fixe die Root Cause, nicht nur das Symptom." in prompt_body
    assert 'Teste dieselbe URL danach erneut mit "Test current page".' in prompt_body
    assert "Retest/Refresh QA vor dem finalen Fix-Nachweis" in prompt_body
    assert "geaenderte Dateien, Retest-Status, verbleibende Risiken" in prompt_body
    assert "nutze den Approval mode aus dem Browser Test Report als Arbeitsbedingung" in prompt_body
    assert "keine riskanten Aktionen ohne Approval" in prompt_body
    assert "Active goal:" in prompt_body
    assert "reduziere Erfolg nicht auf kleinere Checks" in prompt_body
    assert "_browserBuildQaScopeLines(report, state).join('\\n')" in prompt_body
    assert "Browser Test Report:" in prompt_body


def test_browser_retest_returns_to_report_url_for_stale_scope():
    browser_js = Path("web/static/browser.js").read_text(encoding="utf-8")

    retest_start = browser_js.index("async function browserRetestCurrentPageToChat()")
    retest_end = browser_js.index("function _browserQaActionResult", retest_start)
    retest_body = browser_js[retest_start:retest_end]

    assert "const targetUrl = String((remembered.report && remembered.report.url) || '').trim();" in retest_body
    assert "if (_browserComparableQaUrl(currentUrl) !== _browserComparableQaUrl(targetUrl))" in retest_body
    assert "await browserNavigateUrl(targetUrl)" in retest_body
    assert "Retest navigating to report URL" in retest_body
    assert "Retest did not reach the QA URL" in retest_body
    assert "_browserBuildRetestComparison(previous, current)" in retest_body
    assert "Browser Retest Report" in retest_body
    assert "Scope before retest:" in retest_body
    assert "_browserRetestComparisonText(comparison)" in retest_body


def test_browser_shared_live_frame_state_contract():
    browser_js = Path("web/static/browser.js").read_text(encoding="utf-8")
    runtime_py = Path("web/api/browser_runtime.py").read_text(encoding="utf-8")

    state_start = browser_js.index("function browserGetState()")
    state_end = browser_js.index("function browserGetQaState", state_start)
    state_body = browser_js[state_start:state_end]
    render_start = browser_js.index("function _browserSetImage")
    render_end = browser_js.index("function _browserRender", render_start)
    render_body = browser_js[render_start:render_end]

    assert "const img = _browserEl('browserFrameImage');" in state_body
    assert "frame_rev: state.frame_rev == null ? null : state.frame_rev" in state_body
    assert "frame_url: String(state.frame_url || (img && img.src) || '')" in state_body
    assert "frame_complete: !!(img && img.complete)" in state_body
    assert "frame_width: img ? (img.naturalWidth || 0) : 0" in state_body
    assert "frame_height: img ? (img.naturalHeight || 0) : 0" in state_body
    assert "window.browserGetState = browserGetState;" in browser_js

    assert "const rev = String(state.frame_rev || 0);" in render_body
    assert "const nextSrc = state.frame_url || ('/api/browser/frame?session_id='" in render_body
    assert "fetch(frameRequestUrl, {credentials:'same-origin'})" in render_body
    assert "img.src = objectUrl;" in render_body
    assert "_browserFrameObjectUrl" in render_body

    assert "self._snapshot.frame_rev += 1" in runtime_py
    assert 'self._snapshot.frame_url = f"/api/browser/frame?session_id={self.session_id}&rev={self._snapshot.frame_rev}"' in runtime_py
    assert '"available": bool(self._frame_bytes and self._snapshot.frame_rev > 0)' in runtime_py
    assert '"frame_rev": self._snapshot.frame_rev' in runtime_py
    assert '"Screenshot: " + ("available" if report["screenshot"]["available"] else "missing") + f" (rev {report[\'screenshot\'][\'frame_rev\']})"' in runtime_py


def test_browser_action_trace_is_visible_and_bounded():
    browser_js = Path("web/static/browser.js").read_text(encoding="utf-8")
    index_html = Path("web/static/index.html").read_text(encoding="utf-8")

    trace_start = browser_js.index("function _browserRecordActionTrace")
    trace_end = browser_js.index("function _browserSetTarget", trace_start)
    trace_body = browser_js[trace_start:trace_end]
    render_start = browser_js.index("function _browserRender(state")
    render_end = browser_js.index("function browserPanelActivated", render_start)
    render_body = browser_js[render_start:render_end]

    assert 'id="browserActionSummary"' in index_html
    assert 'id="browserActionTrace"' in index_html
    assert 'aria-label="Browser action trace"' in index_html
    assert 'aria-live="polite"' in index_html

    assert "const actionText = String((state && state.last_action_detail) || (state && state.last_action) || '').trim();" in trace_body
    assert "_browserActionTraceKey" in trace_body
    assert "_browserActionTrace.unshift(item);" in trace_body
    assert "_browserActionTrace = _browserActionTrace.slice(0, 5);" in trace_body
    assert "frameRev: state && state.frame_rev != null ? String(state.frame_rev) : ''" in trace_body
    assert "approvalMode: approvalLabel" in trace_body
    assert "activeGoal: goalText" in trace_body
    assert "approvalMode: approvalLabel" in trace_body
    assert "activeGoal: goalText" in trace_body
    assert "const activeGoal = _browserActiveGoalForCurrentSession(sid);" in trace_body
    assert "thumb.src = entry.frameUrl;" in trace_body
    assert "main.textContent = entry.text;" in trace_body
    assert "const metaParts = [" in trace_body
    assert "entry.approvalMode ? ('approval ' + entry.approvalMode) : ''" in trace_body
    assert "entry.activeGoal ? ('goal ' + entry.activeGoal) : ''" in trace_body
    assert "meta.textContent = metaParts.join" in trace_body

    assert "_browserRecordActionTrace(state);" in render_body
    assert "if (state.last_action_detail) actionSummaryParts.push(state.last_action_detail);" in render_body
    assert "_browserSetActionSummary(actionSummaryParts.join(' \u00b7 '));" in render_body


def test_browser_titlebar_status_row_collapses_to_icon_menu():
    index_html = Path("web/static/index.html").read_text(encoding="utf-8")
    style_css = Path("web/static/style.css").read_text(encoding="utf-8")
    ui_js = Path("web/static/ui.js").read_text(encoding="utf-8")

    assert 'class="titlebar-actions titlebar-actions--workflow-compact"' in index_html
    assert 'id="workflowHeaderCompactOverride"' in index_html
    assert '.titlebar-status-cluster.titlebar-status-cluster--compact > .titlebar-browser-group' in index_html
    assert '.titlebar-status-cluster.titlebar-status-cluster--compact > .titlebar-browser-group > #browserStatusBadge' in index_html
    assert 'class="titlebar-status-cluster titlebar-status-cluster--compact"' in index_html
    assert 'data-testid="workflow-status-menu-button"' in index_html
    assert 'Status menu. Shortcut Cmd/Ctrl+Shift+K.' in index_html
    assert 'id="workflowHeaderMenuSummary"' in index_html
    assert 'class="workflow-header-menu-summary"' in index_html
    assert '<svg width="16" height="16" viewBox="0 0 24 24"' in index_html
    assert 'aria-label="Workflow status overview"' in index_html
    assert 'data-tooltip="Workflow status overview"' in index_html
    assert '.titlebar-status-cluster.titlebar-status-cluster--compact > :not(.titlebar-workflow-group)' in style_css
    assert '.titlebar-status-cluster.titlebar-status-cluster--compact .titlebar-workflow-badge' in style_css
    assert '.workflow-header-menu-summary' in style_css
    assert 'backdrop-filter: none' in style_css
    assert '.titlebar-status-cluster.titlebar-status-cluster--compact > .titlebar-browser-group' in style_css
    assert '.titlebar-status-cluster.titlebar-status-cluster--compact > .titlebar-browser-group > #browserStatusBadge' in style_css
    assert 'display: inline-flex !important;' in style_css
    assert '.titlebar-actions.titlebar-actions--workflow-compact > #approvalModeBadge' in style_css
    assert "function _workflowHeaderStatusSummaryText()" in ui_js
    assert "function _workflowSetHeaderMenuSummary(summaryText)" in ui_js
    assert "summary.title=label;" in ui_js
    assert "summary.setAttribute('aria-label',label);" in ui_js
    assert "summary.dataset.tooltip=label;" in ui_js
    assert "_workflowSetHeaderMenuSummary(_workflowHeaderStatusSummaryText());" in ui_js
