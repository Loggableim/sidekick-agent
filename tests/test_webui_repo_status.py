from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "web" / "static" / "index.html"
WORKSPACE_JS = ROOT / "web" / "static" / "workspace.js"
STYLE_CSS = ROOT / "web" / "static" / "style.css"
PANELS_JS = ROOT / "web" / "static" / "panels.js"


_TITLEBAR_ACTION_IDS = {
    "btnGameModeToggle",
    "btnCastToggle",
    "btnRebootSidekick",
    "btnShutdownSidekick",
}


class _TitlebarPlacementParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.inside_status_cluster: dict[str, bool] = {}
        self.seen_ids: set[str] = set()
        self.strip_parent_id: str | None = None
        self._stack: list[tuple[str, set[str], str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key: value for key, value in attrs}
        classes = set((attr_map.get("class") or "").split())
        element_id = attr_map.get("id")
        self._stack.append((tag, classes, element_id))
        if element_id == "composerStatusStrip":
            for _, _, ancestor_id in reversed(self._stack[:-1]):
                if ancestor_id == "composerBox":
                    self.strip_parent_id = "composerBox"
                    break
            else:
                self.strip_parent_id = None
        if tag == "button" and element_id in _TITLEBAR_ACTION_IDS:
            self.seen_ids.add(element_id)
            self.inside_status_cluster[element_id] = any(
                "titlebar-status-cluster" in ancestor_classes for _, ancestor_classes, _ in self._stack
            )

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                del self._stack[index]
                break


def test_composer_workspace_controls_include_a_git_badge_contract() -> None:
    index_html = INDEX_HTML.read_text(encoding="utf-8")
    workspace_js = WORKSPACE_JS.read_text(encoding="utf-8")

    assert 'id="composerGitBadge"' in index_html
    assert "$('composerGitBadge')" in workspace_js
    assert "badges.forEach(badge=>" in workspace_js


def test_workspace_friendly_name_handles_windows_paths() -> None:
    panels_js = PANELS_JS.read_text(encoding="utf-8")

    assert "replace(/\\\\/g,'/')" in panels_js


def test_titlebar_does_not_surface_a_redundant_git_badge() -> None:
    index_html = INDEX_HTML.read_text(encoding="utf-8")

    assert 'id="gitStatusBadge"' not in index_html
    assert 'id="gitStatusValue"' not in index_html


def test_titlebar_does_not_surface_a_redundant_browser_badge() -> None:
    index_html = INDEX_HTML.read_text(encoding="utf-8")

    assert 'id="browserStatusBadge"' not in index_html
    assert 'id="browserStatusMenuBtn"' not in index_html
    assert 'id="browserStatusMenu"' not in index_html


def test_titlebar_secondary_status_chips_are_hidden_by_default() -> None:
    index_html = INDEX_HTML.read_text(encoding="utf-8")

    assert re.search(r'id="researchModeBadge"[^>]*hidden[^>]*data-titlebar-secondary="1"', index_html)
    assert re.search(r'id="mcpStatusBadge"[^>]*hidden[^>]*data-titlebar-secondary="1"', index_html)
    assert re.search(r'id="subagentsStatusBadge"[^>]*hidden[^>]*data-titlebar-secondary="1"', index_html)


def test_titlebar_model_chip_keeps_thinking_state_out_of_the_visible_header() -> None:
    ui_js = (ROOT / "web" / "static" / "ui.js").read_text(encoding="utf-8")

    assert "if(headerValue) headerValue.textContent=displayText;" in ui_js
    assert "const headerTitle=badgeTitle+(isThinking ? ' · thinking' : '');" in ui_js


def test_titlebar_workflow_chip_is_compacted() -> None:
    ui_js = (ROOT / "web" / "static" / "ui.js").read_text(encoding="utf-8")

    assert "value.textContent=approval+' · '+reasoning+(review.visible ? ' · review' : '');" in ui_js
    assert "researchLabel" in ui_js
    assert "label='Workflow: approval " in ui_js


def test_titlebar_status_controls_are_relocated_into_the_composer_strip() -> None:
    index_html = INDEX_HTML.read_text(encoding="utf-8")

    assert 'id="composerStatusStrip" class="composer-status-strip"' in index_html
    assert "function relocateHeaderControlsToComposer()" in index_html
    assert "cluster.id = 'composerStatusStripCluster';" in index_html
    assert "move(document.getElementById('btnBrowserDrawerToggle'));" in index_html
    assert "move(document.querySelector('.titlebar-workflow-group'));" in index_html
    assert "langSelector.hidden = true;" in index_html
    assert "topbarCluster.hidden = true;" in index_html
    assert "Language</div>" in index_html
    assert "switchLang('en')" in index_html


def test_titlebar_status_cluster_is_flat_in_the_app_bar() -> None:
    style_css = STYLE_CSS.read_text(encoding="utf-8")

    assert ".app-titlebar .titlebar-status-cluster {" in style_css
    assert "padding: 0;" in style_css
    assert "border: 0;" in style_css
    assert "background: transparent;" in style_css
    assert "box-shadow: none;" in style_css


def test_composer_status_chip_surfaces_busy_feedback_without_hover() -> None:
    ui_js = (ROOT / "web" / "static" / "ui.js").read_text(encoding="utf-8")
    style_css = STYLE_CSS.read_text(encoding="utf-8")

    assert "function _composerBusyLabel()" in ui_js
    assert "setComposerStatus(_composerBusyLabel(), true);" in ui_js
    assert "voice_thinking" in ui_js
    assert ".composer-status{" in style_css
    assert ".composer-status.is-loading{color:var(--text);border-color:var(--accent-bg-strong);background:var(--accent-bg);}" in style_css


def test_titlebar_model_and_reasoning_pills_are_icon_only_in_the_app_bar() -> None:
    index_html = INDEX_HTML.read_text(encoding="utf-8")
    style_css = STYLE_CSS.read_text(encoding="utf-8")

    assert 'id="modelStatusBadge"' in index_html
    assert 'id="reasoningModeBadge"' in index_html
    assert 'class="titlebar-approval-icon" aria-hidden="true"' in index_html
    assert ".app-titlebar #modelStatusBadge .titlebar-approval-value" in style_css
    assert ".app-titlebar #reasoningModeBadge .titlebar-approval-value" in style_css


def test_titlebar_message_count_is_hidden_for_empty_sessions() -> None:
    panels_js = (ROOT / "web" / "static" / "panels.js").read_text(encoding="utf-8")
    sessions_js = (ROOT / "web" / "static" / "sessions.js").read_text(encoding="utf-8")

    assert "if (vis.length && typeof t === 'function') subText = t('n_messages', vis.length);" in panels_js
    assert "if (typeof t === 'function') subText = t('n_messages', vis.length);" not in panels_js
    assert "mainText = rawTitle && rawTitle !== 'Untitled'" in panels_js
    assert "t('new_chat')" in panels_js
    assert "inp.value = (S.session.title && S.session.title !== 'Untitled') ? S.session.title : '';" in panels_js
    assert "function _sessionListDisplayTitle(rawTitle)" in sessions_js
    assert "function _sessionListSearchText(session)" in sessions_js
    assert "const titleMatches=q?_allSessions.filter(s=>_sessionListSearchText(s).includes(q)):_allSessions;" in sessions_js
    assert "const cleanTitleText=_sessionListDisplayTitle(cleanTitle);" in sessions_js
    assert "const oldTitle=_sessionListRenameValue(s.title);" in sessions_js


def test_composer_status_strip_is_hidden_when_empty() -> None:
    style_css = STYLE_CSS.read_text(encoding="utf-8")

    assert re.search(r"\.composer-status-strip:empty\s*\{\s*display:\s*none;\s*\}", style_css)
    assert ".composer-status-strip {\n  display: grid;\n  grid-template-columns: auto minmax(0, 1fr) auto;" in style_css
    assert ".composer-box > .composer-status-strip .titlebar-status-cluster {\n  grid-column: 3;\n  justify-self: end;\n  align-self: start;" in style_css
    assert ".composer-status-strip > .action-chips{\n  grid-column: 1;\n  justify-self: start;\n  align-self: start;" in style_css
    assert ".composer-box > .composer-status-strip .titlebar-status-cluster > :not(.titlebar-workflow-group)" not in style_css


def test_queue_flyout_collapses_when_empty() -> None:
    style_css = STYLE_CSS.read_text(encoding="utf-8")

    assert ".queue-card:not(.visible) .queue-card-inner{max-height:0;padding:0;border:none;}" in style_css


def test_session_rows_keep_a_fixed_right_gutter_without_hover_shift() -> None:
    style_css = STYLE_CSS.read_text(encoding="utf-8")

    assert ".session-item{padding:8px 40px 8px 8px;" in style_css
    assert ".session-item.streaming,.session-item.unread,.session-item:focus-within,.session-item.menu-open{padding-right:40px;}" in style_css
    assert ".session-item:hover .session-time,.session-item:focus-within .session-time,.session-item.menu-open .session-time{display:none;}" not in style_css
    assert "@media (hover:hover){.session-item:hover{padding-right:40px;}}" not in style_css
    assert ".session-actions{position:absolute;right:6px;top:50%;transform:translateY(-50%);display:flex;align-items:center;justify-content:center;opacity:.78;pointer-events:auto;transition:opacity .15s ease;}" in style_css
    assert ".session-item:hover .session-actions,.session-item:focus-within .session-actions,.session-item.menu-open .session-actions{opacity:1;}" not in style_css


def test_message_footers_stay_visible_without_hover_reveal() -> None:
    style_css = STYLE_CSS.read_text(encoding="utf-8")

    assert ".msg-actions { opacity: .35; }" in style_css
    assert ".msg-actions{display:flex;align-items:center;gap:2px;transition:opacity .15s;margin-left:auto;}" in style_css
    assert ".msg-row[data-role=\"user\"] .msg-foot {" in style_css
    assert "opacity: .78;" in style_css
    assert ".msg-row[data-role=\"user\"]:hover .msg-foot" not in style_css
    assert ".msg-row[data-role=\"assistant\"]:hover .msg-foot" not in style_css
    assert ".assistant-turn:hover .msg-foot" not in style_css
    assert ".assistant-turn:hover .msg-foot-with-usage .msg-time" not in style_css
    assert ".msg-row[data-role=\"assistant\"]:hover .msg-timeline" not in style_css
    assert ".msg-role:hover .msg-time" not in style_css
    assert ".msg-row:hover .msg-actions" not in style_css
    assert ".msg-foot-with-usage .msg-time,\n.msg-foot-with-usage .msg-actions {" in style_css
    assert "opacity: .85;" in style_css
    assert ".msg-usage:hover{opacity:1;}" not in style_css


def test_session_lineage_indicators_stay_visible_without_hover_reveal() -> None:
    style_css = STYLE_CSS.read_text(encoding="utf-8")

    assert ".session-branch-indicator,\n.session-worktree-indicator{" in style_css
    assert "opacity:.6;" in style_css
    assert ".session-item:hover .session-branch-indicator" not in style_css
    assert ".session-item:hover .session-worktree-indicator" not in style_css
    assert ".session-item:focus-within .session-branch-indicator" not in style_css
    assert ".session-item:focus-within .session-worktree-indicator" not in style_css


def test_active_session_preview_is_kept_visible_in_compact_sidebar_density() -> None:
    sessions_js = (ROOT / "web" / "static" / "sessions.js").read_text(encoding="utf-8")
    style_css = STYLE_CSS.read_text(encoding="utf-8")

    assert "if(density==='detailed' || isActive){" in sessions_js
    assert ".session-item.active .session-preview{max-height:18px;opacity:.7;margin:2px 0 0}" in style_css


def test_titlebar_workflow_strip_uses_the_pill_as_its_only_trigger() -> None:
    index_html = INDEX_HTML.read_text(encoding="utf-8")

    assert "id=\"workflowStatusMenuBtn\"" not in index_html
    assert re.search(
        r'id="workflowStatusBadge"[^>]*aria-haspopup="menu"[^>]*aria-controls="workflowStatusMenu"[^>]*aria-expanded="false"',
        index_html,
    )


def test_workflow_palette_and_new_chat_shortcuts_are_canonical() -> None:
    index_html = INDEX_HTML.read_text(encoding="utf-8")
    boot_js = (ROOT / "web" / "static" / "boot.js").read_text(encoding="utf-8")

    assert 'data-tooltip="New conversation (Cmd/Ctrl+N)"' in index_html
    assert "Cmd/Ctrl+K opens the workflow palette; Cmd/Ctrl+N creates a new chat." in boot_js
    assert "if((e.metaKey||e.ctrlKey)&&!e.shiftKey&&!e.altKey&&(e.key==='k'||e.key==='K')){" in boot_js
    assert "if((e.metaKey||e.ctrlKey)&&!e.shiftKey&&!e.altKey&&(e.key==='n'||e.key==='N')){" in boot_js
    assert "Cmd/Ctrl+Shift+K remains a legacy alias for the workflow palette." in boot_js


def test_titlebar_workflow_palette_can_copy_the_conversation_transcript() -> None:
    index_html = INDEX_HTML.read_text(encoding="utf-8")
    ui_js = (ROOT / "web" / "static" / "ui.js").read_text(encoding="utf-8")

    assert 'id="workflowHeaderCopyConversationAction"' in index_html
    assert "workflowRunHeaderAction('copy-conversation')" in index_html
    assert "case 'copy-conversation':" in ui_js
    assert "const text=typeof transcript==='function' ? transcript() : '';" in ui_js
    assert "showToast(typeof t==='function' ? t('copied') : 'Copied!');" in ui_js


def test_titlebar_workflow_palette_does_not_offer_a_dead_browser_menu_action() -> None:
    index_html = INDEX_HTML.read_text(encoding="utf-8")
    ui_js = (ROOT / "web" / "static" / "ui.js").read_text(encoding="utf-8")

    assert 'id="workflowHeaderBrowserMenuAction"' not in index_html
    assert "case 'browser-menu':" not in ui_js
    assert "browserStatusValue" not in ui_js
    assert "browserPermissionBtn" in ui_js


def test_titlebar_actions_stay_out_of_the_status_cluster() -> None:
    index_html = INDEX_HTML.read_text(encoding="utf-8")
    parser = _TitlebarPlacementParser()
    parser.feed(index_html)

    assert _TITLEBAR_ACTION_IDS <= parser.seen_ids
    assert parser.inside_status_cluster == {
        "btnGameModeToggle": False,
        "btnCastToggle": False,
        "btnRebootSidekick": False,
        "btnShutdownSidekick": False,
    }


def test_titlebar_actions_do_not_expand_hidden_admin_buttons_on_focus() -> None:
    style_css = STYLE_CSS.read_text(encoding="utf-8")

    assert ".titlebar-actions #btnCastToggle," in style_css
    assert ".titlebar-actions:focus-within #btnCastToggle" not in style_css
    assert ".titlebar-actions:focus-within #btnRebootSidekick" not in style_css
    assert ".titlebar-actions:focus-within #btnShutdownSidekick" not in style_css


def test_titlebar_center_is_centered_in_the_header() -> None:
    style_css = STYLE_CSS.read_text(encoding="utf-8")

    assert ".app-titlebar-center{position:absolute;" in style_css
    assert "top:50%;" in style_css
    assert "transform:translate(-50%,-50%);" in style_css


def test_titlebar_action_plan_area_tracks_the_workspace_center() -> None:
    style_css = STYLE_CSS.read_text(encoding="utf-8")
    ui_js = (ROOT / "web" / "static" / "ui.js").read_text(encoding="utf-8")

    assert "--workspace-rightpanel-width:320px;" in style_css
    assert 'html[data-workspace-panel="closed"]{--workspace-rightpanel-width:0px;}' in style_css
    assert ".app-titlebar-center{position:absolute;left:calc(50% + (var(--workspace-sidebar-width) - var(--workspace-rightpanel-width)) / 2);top:50%;transform:translate(-50%,-50%);" in style_css
    assert "function syncWorkspaceRightpanelWidth()" in ui_js
    assert "document.querySelector('.rightpanel')" in ui_js
    assert "root.style.setProperty('--workspace-rightpanel-width'" in ui_js


def test_titlebar_sub_is_compact_and_always_visible() -> None:
    style_css = STYLE_CSS.read_text(encoding="utf-8")

    assert ".app-titlebar-sub{font-size:9px;color:var(--muted);background:var(--hover-bg);padding:1px 5px;border-radius:999px;font-family:'SF Mono',ui-monospace,monospace;white-space:nowrap;flex-shrink:0;opacity:.82;max-width:120px;overflow:hidden;line-height:1.3;transition:max-width .18s ease,opacity .12s ease,padding .18s ease,margin .18s ease;margin-left:2px;}" in style_css
    assert ".app-titlebar-inner:hover .app-titlebar-sub," not in style_css
    assert ".app-titlebar-inner:focus-within .app-titlebar-sub" not in style_css


def test_titlebar_workspace_switch_is_stable_and_always_visible() -> None:
    style_css = STYLE_CSS.read_text(encoding="utf-8")

    assert ".titlebar-space {" in style_css
    assert "--space-color: var(--accent, #7c5cfc);" in style_css
    assert "max-width: 184px;" in style_css
    assert "overflow: visible;" in style_css
    assert ".titlebar-space:hover," not in style_css
    assert ".titlebar-space:focus-within {" not in style_css
    assert ".titlebar-space-name {" in style_css
    assert "max-width: 120px;" in style_css
    assert "opacity: 1;" in style_css
    assert "overflow: hidden;" in style_css
    assert "font-weight: 500;" in style_css
    assert "color: var(--space-color);" in style_css
    assert "transition: max-width .18s ease, opacity .12s ease, margin .18s ease;" in style_css
    assert ".titlebar-space:hover .titlebar-space-name," not in style_css
    assert ".titlebar-space-name{margin-left:2px;}" in style_css
    assert ".titlebar-space-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0;}" not in style_css
    assert ".titlebar-space-name{max-width:96px;opacity:1;margin-left:0;}" not in style_css


def test_titlebar_destructive_actions_do_not_depend_on_hover() -> None:
    style_css = STYLE_CSS.read_text(encoding="utf-8")

    assert ".titlebar-actions #btnCastToggle," in style_css
    assert ".titlebar-actions #btnRebootSidekick," in style_css
    assert ".titlebar-actions #btnShutdownSidekick{display:none!important;}" in style_css
    assert ".titlebar-actions:hover #btnCastToggle," not in style_css
    assert ".titlebar-actions:hover #btnRebootSidekick," not in style_css
    assert ".titlebar-actions:focus-within #btnCastToggle" not in style_css
    assert ".titlebar-actions:focus-within #btnRebootSidekick" not in style_css
    assert ".titlebar-actions:focus-within #btnShutdownSidekick" not in style_css


def test_composer_action_chips_are_pulled_closer_to_the_top_edge() -> None:
    index_html = INDEX_HTML.read_text(encoding="utf-8")
    style_css = STYLE_CSS.read_text(encoding="utf-8")

    assert "strip.insertBefore(actionChips, cluster);" in index_html
    assert ".composer-status-strip > .action-chips{\n  grid-column: 1;\n  justify-self: start;\n  align-self: start;" in style_css
    assert ".composer-status-strip > .action-chips{\n  grid-column: 1;\n  justify-self: start;\n  align-self: start;\n  width: max-content;\n  max-width: 100%;\n  flex-wrap: nowrap;\n  justify-content: flex-start;\n}" in style_css
    assert ".composer-status-strip > .action-chips .action-chip{white-space:nowrap;}" in style_css


def test_sidebar_theme_toggle_keeps_night_mode_accessible() -> None:
    index_html = INDEX_HTML.read_text(encoding="utf-8")
    boot_js = (ROOT / "web" / "static" / "boot.js").read_text(encoding="utf-8")

    assert 'id="titlebarThemeToggle"' in index_html
    assert 'id="railThemeToggle"' not in index_html
    assert 'onclick="toggleThemeMode()"' in index_html
    assert 'aria-label="Tag-/Nachtmodus umschalten"' in index_html
    assert 'data-i18n-title="game_mode_toggle"' not in index_html
    assert 'data-tooltip="Game Mode"' not in index_html
    assert 'data-tooltip="Nachtmodus aktivieren"' not in index_html
    assert 'data-tooltip="Tagmodus aktivieren"' not in index_html
    assert 'id="titlebarSpaceBtn" type="button" title="Switch space"' not in index_html
    assert 'id="titlebarSpaceBtn" type="button" aria-label="Switch space"' in index_html
    assert "function _syncThemeToggleButton()" in boot_js
    assert "function toggleThemeMode()" in boot_js
    assert "railThemeToggle" not in boot_js
    assert "aria-pressed" in boot_js
    assert "Tagmodus aktivieren" in boot_js
    assert "Nachtmodus aktivieren" in boot_js
    assert index_html.index('id="btnRebootSidekick"') < index_html.index('id="titlebarThemeToggle"') < index_html.index('id="btnShutdownSidekick"')


def test_active_session_preview_prefers_real_message_context() -> None:
    sessions_js = (ROOT / "web" / "static" / "sessions.js").read_text(encoding="utf-8")

    assert "function _sessionListPreviewText(session, isActive)" in sessions_js
    assert "S.messages" in sessions_js
    assert "compression_anchor_summary" in sessions_js
    assert "pending_user_message" in sessions_js
    assert "preview.textContent=previewText || '[No messages]';" in sessions_js


def test_browser_empty_state_handles_attached_blank_sessions() -> None:
    index_html = INDEX_HTML.read_text(encoding="utf-8")
    browser_js = (ROOT / "web" / "static" / "browser.js").read_text(encoding="utf-8")
    style_css = STYLE_CSS.read_text(encoding="utf-8")

    assert 'id="browserEmptyStateTitle"' in index_html
    assert 'id="browserEmptyStateText"' in index_html
    assert "function _browserIsBlankState(state)" in browser_js
    assert "function _browserSyncEmptyStateText(state)" in browser_js
    assert "startsWith('about:blank')" in browser_js
    assert "_browserSetEmptyVisible(isBlankState);" in browser_js
    assert "Open a URL or run a browser action to show the live viewport." in browser_js
    assert ".browser-empty-state {\n  display: none;\n  position: absolute;\n  inset: 10px;\n  flex-direction: column;" in style_css

def test_composer_action_chips_do_not_duplicate_the_titlebar_plan_toggle() -> None:
    index_html = INDEX_HTML.read_text(encoding="utf-8")
    messages_js = (ROOT / "web" / "static" / "messages.js").read_text(encoding="utf-8")
    style_css = STYLE_CSS.read_text(encoding="utf-8")

    assert 'class="action-chip plan-mode-toggle"' not in index_html
    assert "document.querySelector('.plan-mode-toggle')" not in messages_js
    assert ".plan-mode-toggle.active" not in style_css


def test_visible_composer_chips_do_not_depend_on_hover_tooltips() -> None:
    index_html = INDEX_HTML.read_text(encoding="utf-8")

    assert 'title="Review current state"' not in index_html
    assert 'title="Run tests"' not in index_html
    assert 'title="Install packages"' not in index_html
    assert 'title="Deploy to production"' not in index_html
    assert 'id="profileChip" type="button" onclick="toggleProfileDropdown()" title="Switch profile"' not in index_html
    assert 'id="composerWorkspaceChip" type="button" onclick="toggleComposerWsDropdown()" title="Switch space"' not in index_html
    assert 'id="composerModelChip" type="button" onclick="toggleModelDropdown()" title="Conversation model"' not in index_html
    assert 'id="composerReasoningChip" type="button" onclick="toggleReasoningDropdown()" title="Reasoning effort level"' not in index_html
    assert 'id="btnAttach" data-tooltip="Attach files"' not in index_html
    assert 'id="btnGoalModeToggle" onclick="_toggleGoalMode()" title="Set a persistent goal"' not in index_html
    assert 'id="btnBrowserDrawerToggle" onclick="browserToggleDrawer()" data-tooltip="Browser drawer"' not in index_html
    assert 'id="btnWorkspacePanelToggle" type="button" onclick="toggleFileTreePanel()" title="Show file tree panel"' not in index_html
    assert 'id="sandboxToggleLabel" data-tooltip="Sandbox-Einschränkung aktiv" title="Sandbox-Einschränkung aktiv"' not in index_html
    assert 'id="btnTerminalToggle" type="button" onclick="toggleComposerTerminal()" title="Open workspace terminal"' not in index_html
    assert 'id="composerMobileConfigBtn" type="button" onclick="toggleMobileComposerConfig()" title="Workspace and context settings"' not in index_html
    assert 'id="yoloPill" type="button" onclick="cmdYolo()" style="display:none" title="YOLO mode — click to disable"' not in index_html
    assert 'id="modelSelect" class="composer-model-select" title="Conversation model"' not in index_html
    assert 'id="composerToolsetsChip" type="button" onclick="toggleToolsetsDropdown()" title="Session toolsets"' not in index_html
    assert 'data-mode="queue" type="button" onclick="setComposerBusyMode(\'queue\')" title="Queue: messages wait until the agent is free"' not in index_html
    assert 'data-mode="steer" type="button" onclick="setComposerBusyMode(\'steer\')" title="Steer: inject a mid-turn correction without interrupting"' not in index_html
    assert 'data-mode="bg" type="button" onclick="toggleBgMode()" title="Background: run a task in the background without blocking the chat"' not in index_html
    assert 'id="composerMobileWorkspaceAction" type="button" onclick="toggleComposerWsDropdown()" title="Switch space"' not in index_html
    assert 'id="bgBadge" style="display:none" title="Background tasks running"' not in index_html


def test_composer_shell_uses_a_broader_desktop_width() -> None:
    style_css = STYLE_CSS.read_text(encoding="utf-8")

    assert ".composer-box{max-width:960px;" in style_css
    assert ".goal-banner{max-width:960px;" in style_css
    assert ".plan-banner{max-width:960px;" in style_css


def test_composer_mobile_config_button_is_hidden_by_default() -> None:
    style_css = STYLE_CSS.read_text(encoding="utf-8")

    assert ".icon-btn.composer-mobile-config-btn{display:none;width:34px;height:34px;min-width:34px;min-height:34px;border-radius:999px;}" in style_css


def test_composer_workspace_chip_is_hidden_on_wide_desktop() -> None:
    style_css = STYLE_CSS.read_text(encoding="utf-8")

    assert ".composer-ws-wrap{position:relative;flex:0 1 auto;min-width:0;display:none;align-items:center;gap:4px;}" in style_css


def test_browser_drawer_button_is_visible_in_the_composer_bar() -> None:
    style_css = STYLE_CSS.read_text(encoding="utf-8")
    index_html = INDEX_HTML.read_text(encoding="utf-8")

    assert 'id="btnBrowserDrawerToggle"' in index_html
    assert ".icon-btn.browser-drawer-toggle-btn{display:inline-flex!important;}" in style_css


def test_profile_chip_is_icon_only_and_titlebar_sub_shows_non_default_profiles() -> None:
    style_css = STYLE_CSS.read_text(encoding="utf-8")
    panels_js = PANELS_JS.read_text(encoding="utf-8")

    assert ".composer-profile-label{display:none;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}" in style_css
    assert "function syncAppTitlebar()" in panels_js
    assert "const subEl = document.getElementById('appTitlebarSub');" in panels_js
    assert "badge.textContent = sourceLabel + (S.session && S.session.read_only ? ' · read-only' : '');" in panels_js
    assert "else if (panel === 'chat')" in panels_js
    assert "mainText = typeof t === 'function' ? t('new_chat') : 'New chat';" in panels_js


def test_titlebar_workflow_strip_is_click_only_and_stable() -> None:
    index_html = INDEX_HTML.read_text(encoding="utf-8")
    ui_js = (ROOT / "web" / "static" / "ui.js").read_text(encoding="utf-8")
    style_css = STYLE_CSS.read_text(encoding="utf-8")

    assert 'class="titlebar-approval-value titlebar-workflow-value"' in index_html
    assert 'onmouseenter="workflowOpenHeaderMenu(event)"' not in index_html
    assert 'onmouseleave="workflowMaybeCloseHeaderMenu(event)"' not in index_html
    assert 'Click to show workflow status' in index_html
    assert 'Hover to show workflow status' not in ui_js
    assert 'Click to show workflow status' in ui_js
    assert 'bottom:calc(100% + 6px);top:auto;right:0;' in index_html
    assert 'max-height:min(60vh,420px);overflow:auto;overscroll-behavior:contain;' in index_html
    assert "function workflowOpenHeaderMenu(event)" in ui_js
    assert "function workflowMaybeCloseHeaderMenu(event)" in ui_js
    assert re.search(r"\.titlebar-workflow-badge\s+\.titlebar-workflow-value\s*\{\s*display:inline-block;\s*max-width:220px;\s*opacity:1;", style_css)
    assert '.titlebar-workflow-badge[aria-expanded="true"]' in style_css


def test_titlebar_inline_onclick_fallbacks_are_programmatically_bound() -> None:
    ui_js = (ROOT / "web" / "static" / "ui.js").read_text(encoding="utf-8")

    assert "function _bindInlineClickFallbacks(root)" in ui_js
    assert "function _bindModelAndReasoningTriggers()" in ui_js
    assert "querySelectorAll('[onclick]')" in ui_js
    assert "if(typeof el.onclick==='function') continue;" in ui_js
    assert "const fallback=new Function('event', code);" in ui_js
    assert "_bindInlineClickFallbacks(document);" in ui_js
    assert "['modelStatusBadge', toggleModelDropdown]" in ui_js
    assert "['reasoningModeBadge', toggleReasoningDropdown]" in ui_js
    assert "window._bindInlineClickFallbacks=_bindInlineClickFallbacks;" in ui_js


def test_titlebar_workflow_palette_keeps_settings_controls_out_of_the_visible_menu() -> None:
    index_html = INDEX_HTML.read_text(encoding="utf-8")
    ui_js = (ROOT / "web" / "static" / "ui.js").read_text(encoding="utf-8")

    assert 'data-workflow-preset="model"' not in index_html
    assert 'data-workflow-preset="subagents"' not in index_html
    assert 'id="workflowHeaderModelAction" data-workflow-sticky-hidden="1"' in index_html
    assert 'id="workflowHeaderThinkingAction" data-workflow-sticky-hidden="1"' in index_html
    assert 'id="workflowHeaderMcpAction" data-workflow-sticky-hidden="1"' in index_html
    assert 'id="workflowHeaderSubagentsAction" data-workflow-sticky-hidden="1"' in index_html
    assert "function _workflowHeaderMenuIsStickyHidden(btn)" in ui_js
    assert "if(_workflowHeaderMenuIsStickyHidden(btn)){" in ui_js
    assert "if(btn && !_workflowHeaderMenuIsStickyHidden(btn)) btn.hidden=false;" in ui_js
    assert "const available=!!subagentState.count;" in ui_js
    assert "primarySubagent.setAttribute('data-workflow-sticky-hidden','1');" in ui_js


def test_boot_marks_recreated_missing_sessions_ready_before_new_session_sync() -> None:
    boot_js = (ROOT / "web" / "static" / "boot.js").read_text(encoding="utf-8")

    assert re.search(
        r"if \(_bootMissingSession && urlSession && saved\) \{\s*if \(typeof newSession === 'function'\) \{\s*S\._bootReady=true;\s*await newSession\(\);",
        boot_js,
        re.S,
    )


def test_boot_missing_session_does_not_pop_a_new_session_toast() -> None:
    boot_js = (ROOT / "web" / "static" / "boot.js").read_text(encoding="utf-8")

    assert "Previous session was missing. Started a new one." not in boot_js


def test_mobile_composer_config_panel_does_not_duplicate_workflow_model_controls() -> None:
    index_html = INDEX_HTML.read_text(encoding="utf-8")
    ui_js = (ROOT / "web" / "static" / "ui.js").read_text(encoding="utf-8")

    assert 'title="Workspace and context settings"' not in index_html
    assert 'aria-label="Workspace and context settings"' in index_html
    assert 'id="composerMobileWorkspaceAction"' in index_html
    assert 'id="composerMobileContextAction"' in index_html
    assert 'id="composerMobileModelAction"' not in index_html
    assert 'id="composerMobileReasoningAction"' not in index_html
    assert 'id="composerAdvancedPanel"' not in index_html
    assert "const _MOBILE_CONFIG_BASE_LABEL='Workspace and context settings';" in ui_js


def test_removed_mobile_model_and_reasoning_hooks_are_not_referenced_in_ui_js() -> None:
    ui_js = (ROOT / "web" / "static" / "ui.js").read_text(encoding="utf-8")

    assert "if(mobileLabel)" not in ui_js
    assert "if(mobileAction)" not in ui_js
    assert "const mobileAction=$('composerMobileModelAction');" not in ui_js
    assert "composerMobileReasoningAction" not in ui_js
