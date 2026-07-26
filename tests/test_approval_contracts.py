from pathlib import Path


def test_approval_mode_normalization_keeps_manual_smart_off_contract():
    from web.api import routes

    assert routes._normalize_approval_mode_value("manual") == "manual"
    assert routes._normalize_approval_mode_value("smart") == "smart"
    assert routes._normalize_approval_mode_value("off") == "off"
    assert routes._normalize_approval_mode_value("ask") == "manual"
    assert routes._normalize_approval_mode_value("deny") == "smart"
    assert routes._normalize_approval_mode_value("yolo") == "off"
    assert routes._normalize_approval_mode_value(True) == "manual"
    assert routes._normalize_approval_mode_value(False) == "off"
    assert routes._normalize_approval_mode_value("") == "manual"


def test_approval_slash_command_is_local_noecho_and_autocomplete_safe():
    commands_js = Path("web/static/commands.js").read_text(encoding="utf-8")

    assert "{name:'approval',  desc:'Set approval mode (manual/smart/off)', fn:cmdApproval" in commands_js
    assert "subArgs:['manual','smart','off','status'], noEcho:true" in commands_js
    assert "async function cmdApproval(args)" in commands_js
    assert "const MODES=['manual','smart','off'];" in commands_js
    assert "if(!MODES.includes(normalizeMode))" in commands_js
    assert "await api('/api/approval'," in commands_js
    assert "if(typeof window._setApprovalModeIndicator==='function')" in commands_js
    assert "return window._setApprovalModeIndicator(normalized)||normalized;" in commands_js


def test_approval_header_chip_is_clickable_and_stateful():
    index_html = Path("web/static/index.html").read_text(encoding="utf-8")

    assert 'id="approvalModeBadge"' in index_html
    assert 'id="approvalModeValue"' in index_html
    assert "window._setApprovalModeIndicator = applyApprovalMode;" in index_html
    assert "async function cycleApprovalMode()" in index_html
    assert "var approvalModes = ['manual', 'smart', 'off'];" in index_html
    assert "var next = approvalModes[(index < 0 ? 0 : index + 1) % approvalModes.length];" in index_html
    assert "fetch('/api/approval', {" in index_html
    assert "badgeEl.addEventListener('click', cycleApprovalMode);" in index_html
    assert "Approval mode manual. Click to cycle." in index_html
    assert "if (typeof _browserUpdateHeaderBadge === 'function') _browserUpdateHeaderBadge();" in index_html
    assert "if (typeof browserRefreshAgentContext === 'function') browserRefreshAgentContext();" in index_html


def test_workflow_chip_includes_current_approval_mode():
    ui_js = Path("web/static/ui.js").read_text(encoding="utf-8")

    sync_start = ui_js.index("function syncWorkflowChip()")
    sync_end = ui_js.index("function _workflowApplyBrowserQaAction", sync_start)
    sync_body = ui_js[sync_start:sync_end]

    assert "const normalizeApproval=(mode)=>{" in sync_body
    assert "if(text==='ask') return 'manual';" in sync_body
    assert "if(text==='deny') return 'smart';" in sync_body
    assert "if(text==='yolo') return 'off';" in sync_body
    assert "const approval=normalizeApproval(window._approvalMode" in sync_body
    assert "value.textContent=approval+" in sync_body
    assert "const label='Workflow: approval '+approval" in sync_body
    assert "badge.setAttribute('aria-label',label);" in sync_body
