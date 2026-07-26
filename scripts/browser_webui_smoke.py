#!/usr/bin/env python
"""Smoke-test the Sidekick WebUI browser-facing controls.

This is intentionally small and dependency-light so agents can run it after
browser/UI fixes without constructing fragile one-off Playwright snippets.
It verifies the surfaces agents need for reliable copilot behavior:

- the backend responding with the runtime fingerprint marker
- titlebar space selector is uniquely targetable
- titlebar language selector sits immediately left of the space selector
- approval mode chip is visible, clickable, and restores its starting mode
- /approval slash command switches manual/smart/off and restores its starting mode
- browser drawer opens, accepts local navigation, and exposes visible page state
- browser drawer is visually opaque and receives pointer hits above the app chrome
- browser permission UI cycles locked -> watch -> control and restores state
- browser agent-control endpoint enforces locked/read/control boundaries
- browser QA endpoint produces technical/visual evidence for the rendered page
- browser QA detects known visual/layout/accessibility defects on a broken fixture
- browser QA marks stale evidence after URL changes and gates Fix/Repro behind Retest
- browser agent-control can type, click, scroll, and report visible page state
- browser drawer frame rev updates after agent actions so user and agent share live page state
- browser drawer exposes visible status/action trace after agent actions
- persistent goal reload resumes automatically after refresh
- spaces can be switched and conversations can be opened without UI stalls
- session rows expose stable test IDs
- browser status/actions controls are uniquely targetable
- titlebar space options no longer collide with hidden panel space items
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from urllib.error import HTTPError
from urllib.parse import quote, urlparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


DEFAULT_BASE_URL = "http://127.0.0.1:9119"
DEFAULT_SESSION_ID = "bb94a4a6a3ed"
DEFAULT_WORKSPACE = "nova"
DEFAULT_SWITCH_WORKSPACE = "sidekick"
DEFAULT_ARTIFACT_DIR = "output/browser-webui-smoke"
DEFAULT_MAX_LOAD_MS = 5000
DEFAULT_MAX_SWITCH_MS = 2000


@dataclass
class Check:
    name: str
    ok: bool
    detail: Any = None


def _get_json(url: str, timeout: float = 5.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        data = response.read().decode("utf-8")
    return json.loads(data)


def _post_json(
    url: str,
    payload: dict[str, Any],
    timeout: float = 5.0,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read().decode("utf-8")
    return json.loads(data)


def _workspace_scoped_url(base_url: str, path: str, workspace_slug: str | None = None) -> str:
    if not workspace_slug:
        return f"{base_url}{path}"
    sep = "&" if "?" in path else "?"
    return f"{base_url}{path}{sep}workspace={quote(str(workspace_slug))}"


def _workspace_button_text_matches(workspace_slug: str, text: str) -> bool:
    normalized = str(text or "").strip().lower()
    slug = str(workspace_slug or "").strip().lower()
    if not normalized or not slug:
        return False
    if slug == "default":
        return "root space" in normalized or slug in normalized
    return slug in normalized


def _unique_count(page, selector: str) -> int:
    return page.locator(selector).count()


def _check_unique(checks: list[Check], page, name: str, selector: str) -> None:
    count = _unique_count(page, selector)
    checks.append(Check(name, count == 1, {"selector": selector, "count": count}))


def _check_titlebar_language_space_order(checks: list[Check], page) -> None:
    layout = page.evaluate(
        """
        () => {
            function rect(selector) {
                const el = document.querySelector(selector);
                if (!el) return null;
                const r = el.getBoundingClientRect();
                return {
                    x: Math.round(r.x),
                    y: Math.round(r.y),
                    right: Math.round(r.right),
                    bottom: Math.round(r.bottom),
                    width: Math.round(r.width),
                    height: Math.round(r.height),
                    visible: !!(r.width && r.height),
                    text: (el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 80)
                };
            }
            const lang = rect('#btnLangSelector');
            const space = rect('[data-testid="titlebar-space-button"]');
            const gapPx = lang && space ? Math.round(space.x - lang.right) : null;
            const sameRow = !!(lang && space && Math.abs((space.y + space.height / 2) - (lang.y + lang.height / 2)) <= 8);
            return {
                lang,
                space,
                gapPx,
                sameRow,
                spaceRightOfLanguage: !!(lang && space && space.x >= lang.right - 1),
                compactGap: gapPx !== null && gapPx >= -1 && gapPx <= 12
            };
        }
        """
    )
    checks.append(
        Check(
            "titlebar_language_left_of_space",
            bool(
                layout.get("lang", {}).get("visible")
                and layout.get("space", {}).get("visible")
                and layout.get("sameRow")
                and layout.get("spaceRightOfLanguage")
                and layout.get("compactGap")
            ),
            layout,
        )
    )


def _check_approval_badge_cycle(checks: list[Check], page) -> None:
    modes = ["manual", "smart", "off"]
    badge_selector = "#approvalModeBadge"
    value_selector = "#approvalModeValue"
    detail: dict[str, Any] = {}

    try:
        badge = page.locator(badge_selector)
        value = page.locator(value_selector)
        badge_count = badge.count()
        value_count = value.count()
        detail.update({"badge_count": badge_count, "value_count": value_count})
        if badge_count != 1 or value_count != 1:
            checks.append(Check("approval_badge_click_cycle_restore", False, detail))
            return

        initial = value.inner_text(timeout=3000).strip().lower()
        if initial not in modes:
            detail["initial"] = initial
            checks.append(Check("approval_badge_click_cycle_restore", False, detail))
            return

        expected = modes[(modes.index(initial) + 1) % len(modes)]
        try:
            page.wait_for_function(
                "(selector) => !document.querySelector(selector)?.disabled",
                arg=badge_selector,
                timeout=5000,
            )
        except Exception as wait_exc:
            detail["enabled_wait_error"] = repr(wait_exc)
        badge.click(timeout=3000)
        page.wait_for_function(
            "([selector, mode]) => (document.querySelector(selector)?.textContent || '').trim().toLowerCase() === mode",
            arg=(value_selector, expected),
            timeout=5000,
        )
        cycled = value.inner_text(timeout=3000).strip().lower()

        restored = cycled
        for _ in range(len(modes)):
            if restored == initial:
                break
            badge.click(timeout=3000)
            page.wait_for_timeout(150)
            restored = value.inner_text(timeout=3000).strip().lower()

        if restored != initial:
            try:
                page.evaluate(
                    """
                    async ([mode]) => {
                        await fetch('/api/approval', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ mode })
                        });
                        if (typeof window._setApprovalModeIndicator === 'function') {
                            window._setApprovalModeIndicator(mode);
                        }
                    }
                    """,
                    [initial],
                )
                page.wait_for_function(
                    "([selector, mode]) => (document.querySelector(selector)?.textContent || '').trim().toLowerCase() === mode",
                    arg=(value_selector, initial),
                    timeout=5000,
                )
                restored = value.inner_text(timeout=3000).strip().lower()
            except Exception as restore_exc:
                detail["restore_error"] = repr(restore_exc)

        detail.update({"initial": initial, "expected_after_click": expected, "cycled": cycled, "restored": restored})
        checks.append(
            Check(
                "approval_badge_click_cycle_restore",
                cycled == expected and restored == initial,
                detail,
            )
        )
    except Exception as exc:
        detail["error"] = repr(exc)
        checks.append(Check("approval_badge_click_cycle_restore", False, detail))


def _check_approval_slash_command_cycle(checks: list[Check], page) -> None:
    detail: dict[str, Any] = {}
    try:
        result = page.evaluate(
            """
            async () => {
                const modes = ['manual', 'smart', 'off'];
                const valueText = () => (document.querySelector('#approvalModeValue')?.textContent || '').trim().toLowerCase();
                const getApiMode = async () => {
                    const resp = await fetch('/api/approval', {cache: 'no-store'});
                    const data = await resp.json().catch(() => ({}));
                    if (!resp.ok) throw new Error(data.error || resp.statusText || 'approval status failed');
                    return String(data.mode || '').trim().toLowerCase();
                };
                const waitMode = async (mode) => {
                    const started = Date.now();
                    let apiMode = '';
                    let chipMode = '';
                    while (Date.now() - started < 6000) {
                        apiMode = await getApiMode();
                        chipMode = valueText();
                        if (apiMode === mode && chipMode === mode) {
                            return {ok: true, api_mode: apiMode, chip_mode: chipMode};
                        }
                        await new Promise(resolve => setTimeout(resolve, 120));
                    }
                    return {ok: false, api_mode: apiMode, chip_mode: chipMode};
                };
                const commandRegistered = typeof COMMANDS !== 'undefined'
                    && Array.isArray(COMMANDS)
                    && COMMANDS.some(cmd => cmd && cmd.name === 'approval' && cmd.noEcho === true);
                const executorAvailable = typeof executeCommand === 'function';
                const autocomplete = typeof getSlashAutocompleteMatches === 'function'
                    ? await getSlashAutocompleteMatches('/approval ')
                    : [];
                const autocompleteValues = Array.isArray(autocomplete)
                    ? autocomplete.map(item => String(item && (item.value || item.name || item.insert || '') || '').toLowerCase())
                    : [];
                const initial = modes.includes(await getApiMode()) ? await getApiMode() : 'manual';
                const steps = [];
                if (!commandRegistered || !executorAvailable) {
                    return {command_registered: commandRegistered, executor_available: executorAvailable, initial, steps, autocomplete_values: autocompleteValues};
                }
                for (const mode of modes) {
                    const commandResult = executeCommand('/approval ' + mode);
                    const waited = await waitMode(mode);
                    steps.push({mode, command_result: commandResult, waited});
                }
                const restoreResult = executeCommand('/approval ' + initial);
                const restored = await waitMode(initial);
                return {
                    command_registered: commandRegistered,
                    executor_available: executorAvailable,
                    initial,
                    steps,
                    restore_result: restoreResult,
                    restored,
                    autocomplete_values: autocompleteValues
                };
            }
            """
        )
        detail.update(result or {})
        steps = detail.get("steps") or []
        autocomplete_values = detail.get("autocomplete_values") or []
        checks.append(
            Check(
                "approval_slash_command_cycle_restore",
                bool(
                    detail.get("command_registered")
                    and detail.get("executor_available")
                    and len(steps) == 3
                    and all((step.get("command_result") or {}).get("noEcho") is True for step in steps)
                    and all((step.get("waited") or {}).get("ok") for step in steps)
                    and (detail.get("restore_result") or {}).get("noEcho") is True
                    and (detail.get("restored") or {}).get("ok") is True
                    and all(
                        any(mode in str(value) for value in autocomplete_values)
                        for mode in ("manual", "smart", "off")
                    )
                ),
                detail,
            )
        )
    except Exception as exc:
        detail["error"] = repr(exc)
        checks.append(Check("approval_slash_command_cycle_restore", False, detail))


def _check_persistent_goal_reconciles_server_state(checks: list[Check], page) -> None:
    detail: dict[str, Any] = {}
    try:
        result = page.evaluate(
            """
            async () => {
                const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
                const session = typeof S !== 'undefined' && S && S.session ? S.session : {};
                const sid = String(session.session_id || '').trim()
                    || String((location && location.pathname && location.pathname.split('/').pop()) || '').trim();
                const activeSpace = (typeof _activeSpace !== 'undefined' && _activeSpace)
                    || localStorage.getItem('sidekick-active-workspace')
                    || 'nova';
                const profile = (typeof S !== 'undefined' && S && (S.activeProfile || (S.session && S.session.profile)))
                    || 'default';
                const postGoal = async (args) => {
                    const resp = await fetch('/api/goal', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({
                            session_id: sid,
                            args,
                            workspace: session.workspace || '',
                            profile,
                        })
                    });
                    const data = await resp.json().catch(() => ({}));
                    return {ok: resp.ok, status: resp.status, data};
                };
                const readUi = () => {
                    const banner = document.querySelector('#goalBanner');
                    return {
                        banner_display: banner ? getComputedStyle(banner).display : null,
                        banner_text: document.querySelector('#goalBannerText')?.textContent || '',
                        stored_goal: JSON.parse(localStorage.getItem('sidekick-webui-goal-state') || 'null'),
                        window_goal: window._goalState || null,
                    };
                };
                if (!sid) return {sid, error: 'missing session id'};
                const clear = await postGoal('clear');
                const stale = {
                    goal: 'Smoke stale persistent goal',
                    status: 'active',
                    turns_used: 0,
                    max_turns: 20,
                    session_id: sid,
                    space: activeSpace,
                };
                localStorage.setItem('sidekick-webui-goal-state', JSON.stringify(stale));
                window._goalState = stale;
                if (typeof window._renderGoalBanner === 'function') window._renderGoalBanner();
                const before = readUi();
                if (typeof window._syncGoalStateFromServer === 'function') {
                    await window._syncGoalStateFromServer();
                }
                let after = readUi();
                const deadline = Date.now() + 3000;
                while (Date.now() < deadline) {
                    if (
                        after.banner_display === 'none'
                        && !after.stored_goal
                        && !after.window_goal
                    ) {
                        break;
                    }
                    await sleep(120);
                    after = readUi();
                }
                const status = await postGoal('status');
                return {
                    sid,
                    active_space: activeSpace,
                    clear,
                    before,
                    after,
                    status,
                    sync_available: typeof window._syncGoalStateFromServer === 'function',
                };
            }
            """
        )
        page_sid = ""
        try:
            page_sid = Path(urlparse(page.url).path).name.strip()
        except Exception:
            page_sid = ""
        detail.update(result or {})
        if page_sid and not str(detail.get("sid") or "").strip():
            detail["sid"] = page_sid
        before = detail.get("before") or {}
        after = detail.get("after") or {}
        status = (detail.get("status") or {}).get("data") or {}
        checks.append(
            Check(
                "persistent_goal_reconciles_server_state",
                bool(
                    detail.get("sid")
                    and detail.get("sync_available")
                    and before.get("banner_display") == "flex"
                    and "Smoke stale persistent goal" in str(before.get("banner_text") or "")
                    and after.get("banner_display") == "none"
                    and not after.get("stored_goal")
                    and not after.get("window_goal")
                    and status.get("goal") is None
                ),
                detail,
            )
        )
    except Exception as exc:
        detail["error"] = repr(exc)
        checks.append(Check("persistent_goal_reconciles_server_state", False, detail))


def _check_goal_reload_resume_autostarts(checks: list[Check], browser, base_url: str, workspace: str) -> None:
    detail: dict[str, Any] = {"workspace": workspace}
    resume_page = None
    cleanup_sid = ""
    cleanup_stream_id = ""
    try:
        session = _post_json(
            f"{base_url}/api/session/new",
            {},
            timeout=15.0,
            headers={"X-Hermes-Workspace": workspace},
        )
        created = session.get("session") or {}
        sid = str(created.get("session_id") or "").strip()
        session_workspace = str(created.get("workspace") or "").strip()
        if not sid:
            raise RuntimeError("fresh session id missing")
        cleanup_sid = sid

        workspace_slug = (Path(session_workspace).name or workspace).strip().lower() or workspace
        goal_text = "Smoke reload continuation"
        _post_json(
            f"{base_url}/api/goal",
            {
                "session_id": sid,
                "args": goal_text,
                "workspace": session_workspace,
                "profile": "default",
            },
            timeout=20.0,
        )
        before_snapshot = _get_json(f"{base_url}/api/session?session_id={sid}&messages=0")

        resume_page = browser.new_page(viewport={"width": 1600, "height": 900})
        resume_page.add_init_script(
            f"""
            (() => {{
                const sid = {json.dumps(sid)};
                const goal = {json.dumps(goal_text)};
                const workspaceSlug = {json.dumps(workspace_slug)};
                const key = `sidekick-webui-goal-continuation-${{sid}}`;
                const goalState = {{
                    goal,
                    status: 'active',
                    turns_used: 0,
                    max_turns: 20,
                    last_verdict: null,
                    last_reason: null,
                    paused_reason: null,
                    session_id: sid,
                    space: workspaceSlug,
                }};
                localStorage.setItem('sidekick-webui-goal-state', JSON.stringify(goalState));
                sessionStorage.setItem(key, JSON.stringify({{
                    sid,
                    text: 'please continue',
                    goal,
                    model: '',
                    model_provider: null,
                    profile: 'default',
                    workspace: '',
                    workspace_slug: workspaceSlug,
                    queued_at: Date.now(),
                }}));
            }})();
            """
        )

        target_url = f"{base_url}/session/{sid}?workspace={workspace_slug}&cb={int(time.time() * 1000)}"
        resume_page.goto(target_url, wait_until="commit", timeout=30000)
        resume_page.wait_for_function(
            "() => !!(window.S && (window.S.busy || window.S.activeStreamId || (window.S.session && window.S.session.active_stream_id)))",
            timeout=30000,
        )
        deadline = time.time() + 20.0
        after_snapshot: dict[str, Any] = {}
        while time.time() < deadline:
            after_snapshot = _get_json(f"{base_url}/api/session?session_id={sid}&messages=0")
            after_session = after_snapshot.get("session") or {}
            if after_session.get("active_stream_id") or after_session.get("pending_user_message"):
                break
            time.sleep(0.25)
        resume_state = resume_page.evaluate(
            """
            (sid) => ({
                goal_state: window._goalState || null,
                busy: !!(window.S && window.S.busy),
                active_stream_id: window.S && window.S.activeStreamId || null,
                pending_key: sessionStorage.getItem(`sidekick-webui-goal-continuation-${sid}`),
            })
            """,
            sid,
        )
        cleanup_stream_id = str(
            (after_snapshot.get("session") or {}).get("active_stream_id")
            or (resume_state or {}).get("active_stream_id")
            or (before_snapshot.get("session") or {}).get("active_stream_id")
            or ""
        ).strip()
        detail.update(
            {
                "sid": sid,
                "session_workspace": session_workspace,
                "workspace_slug": workspace_slug,
                "before_snapshot": before_snapshot,
                "after_snapshot": after_snapshot,
                "resume_state": resume_state,
                "target_url": target_url,
            }
        )
        checks.append(
            Check(
                "goal_reload_resume_autostarts",
                bool((before_snapshot.get("session") or {}).get("active_stream_id"))
                and bool((after_snapshot.get("session") or {}).get("active_stream_id"))
                and bool((resume_state or {}).get("busy") or (resume_state or {}).get("active_stream_id")),
                detail,
            )
        )
    except Exception as exc:
        detail["error"] = repr(exc)
        checks.append(Check("goal_reload_resume_autostarts", False, detail))
    finally:
        try:
            if resume_page is not None:
                resume_page.close()
        except Exception:
            pass
        if cleanup_sid:
            cleanup_detail: dict[str, Any] = {}
            if cleanup_stream_id:
                try:
                    cleanup_detail["cancel"] = _get_json(
                        f"{base_url}/api/chat/cancel?stream_id={cleanup_stream_id}"
                    )
                except Exception as cleanup_exc:
                    cleanup_detail["cancel_error"] = repr(cleanup_exc)
                idle_deadline = time.time() + 8.0
                while time.time() < idle_deadline:
                    try:
                        live_snapshot = _get_json(f"{base_url}/api/session?session_id={cleanup_sid}&messages=0")
                    except Exception:
                        break
                    live_session = live_snapshot.get("session") or {}
                    if not live_session.get("active_stream_id") and not live_session.get("pending_user_message"):
                        cleanup_detail["idle_snapshot"] = {
                            "active_stream_id": live_session.get("active_stream_id"),
                            "pending_user_message": live_session.get("pending_user_message"),
                        }
                        break
                    time.sleep(0.2)
            delete_url = _workspace_scoped_url(
                base_url,
                "/api/session/delete",
                workspace_slug,
            )
            verify_url = _workspace_scoped_url(
                base_url,
                f"/api/session?session_id={cleanup_sid}&messages=0",
                workspace_slug,
            )
            delete_deadline = time.time() + 8.0
            delete_attempts = 0
            verified_deleted = False
            while time.time() < delete_deadline:
                delete_attempts += 1
                try:
                    cleanup_detail["delete"] = _post_json(
                        delete_url,
                        {"session_id": cleanup_sid},
                    )
                except Exception as cleanup_exc:
                    cleanup_detail["delete_error"] = repr(cleanup_exc)
                try:
                    for stability_check in range(4):
                        try:
                            _get_json(verify_url)
                            cleanup_detail["delete_reappeared"] = True
                            break
                        except HTTPError as verify_exc:
                            if getattr(verify_exc, "code", None) != 404:
                                cleanup_detail["delete_verify_error"] = repr(verify_exc)
                                break
                            if stability_check < 3:
                                time.sleep(1.0)
                                continue
                            cleanup_detail["deleted_verified"] = True
                            cleanup_detail["delete_attempts"] = delete_attempts
                            verified_deleted = True
                            break
                        except Exception as verify_exc:
                            cleanup_detail["delete_verify_error"] = repr(verify_exc)
                            break
                    if verified_deleted:
                        break
                except HTTPError as verify_exc:
                    cleanup_detail["delete_verify_error"] = repr(verify_exc)
                except Exception as verify_exc:
                    cleanup_detail["delete_verify_error"] = repr(verify_exc)
                if verified_deleted:
                    break
                time.sleep(0.5)
            else:
                cleanup_detail["deleted_verified"] = False
                cleanup_detail["delete_attempts"] = delete_attempts
            if cleanup_detail:
                detail["cleanup"] = cleanup_detail


def _check_browser_drawer_navigation(checks: list[Check], page, target_url: str) -> None:
    detail: dict[str, Any] = {"target_url": target_url}
    try:
        page.locator('[data-testid="composer-browser-drawer-button"]').click(timeout=3000)
        page.wait_for_selector("#browserDrawer", state="visible", timeout=5000)
        page.fill("#browserUrlInput", target_url, timeout=3000)
        before = page.evaluate(
            """
            () => {
                const state = typeof window.browserGetState === 'function' ? window.browserGetState() : {};
                return {
                    frame_rev: Number(state && state.frame_rev || 0),
                    last_action_detail: String(state && state.last_action_detail || ''),
                };
            }
            """
        )
        page.locator("#browserGoBtn").click(timeout=3000)
        page.wait_for_function(
            """
            ([url, priorRev]) => {
                const status = document.querySelector('#browserStatusUrl');
                const statusText = (status && status.textContent || '').trim();
                const state = typeof window.browserGetState === 'function' ? window.browserGetState() : {};
                const stateUrl = String(state && state.url || '').trim();
                const actionText = String(state && state.last_action_detail || '').trim();
                return Number(state.frame_rev || 0) > priorRev && (
                    stateUrl === url
                    || statusText.includes(url)
                    || statusText.includes('/api/runtime/fingerprint')
                    || actionText.includes(url)
                );
            }
            """,
            arg=[target_url, before.get("frame_rev") or 0],
            timeout=10000,
        )
        page.wait_for_function(
            """
            ([priorRev]) => {
                const state = typeof window.browserGetState === 'function' ? window.browserGetState() : {};
                const img = document.querySelector('#browserFrameImage');
                return !!(
                    state
                    && Number(state.frame_rev || 0) > priorRev
                    && state.frame_complete
                    && state.frame_width > 0
                    && state.frame_height > 0
                    && !state.busy
                    && String(state.status || '').toLowerCase() === 'idle'
                    && img
                    && img.complete
                    && img.naturalWidth > 0
                    && img.naturalHeight > 0
                );
            }
            """,
            arg=[before.get("frame_rev") or 0],
            timeout=10000,
        )
        state = page.evaluate(
            """
            () => {
                function rect(selector) {
                    const el = document.querySelector(selector);
                    if (!el) return null;
                    const r = el.getBoundingClientRect();
                    return {
                        x: Math.round(r.x),
                        y: Math.round(r.y),
                        width: Math.round(r.width),
                        height: Math.round(r.height),
                        visible: !!(r.width && r.height),
                        text: (el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 160),
                        value: el.value || ''
                    };
                }
                const drawer = rect('#browserDrawer');
                const input = rect('#browserUrlInput');
                const status = rect('#browserStatusUrl');
                const pill = rect('#browserStatusPill');
                const stage = rect('#browserStage');
                const image = rect('#browserFrameImage');
                const liveState = typeof window.browserGetState === 'function' ? window.browserGetState() : {};
                return {drawer, input, status, pill, stage, image, liveState};
            }
            """
        )
        detail.update(state)
        checks.append(
            Check(
                "browser_drawer_local_navigation_visible",
                bool(
                    state.get("drawer", {}).get("visible")
                    and state.get("input", {}).get("visible")
                    and target_url in state.get("input", {}).get("value", "")
                    and state.get("status", {}).get("visible")
                    and target_url in str(state.get("liveState", {}).get("url", "") or state.get("status", {}).get("text", ""))
                    and state.get("stage", {}).get("visible")
                    and state.get("stage", {}).get("height", 0) >= 120
                    and state.get("image", {}).get("visible")
                    and state.get("liveState", {}).get("frame_complete")
                    and state.get("liveState", {}).get("busy") is False
                    and str(state.get("liveState", {}).get("status") or "").lower() == "idle"
                ),
                detail,
            )
        )
    except Exception as exc:
        detail["error"] = repr(exc)
        checks.append(Check("browser_drawer_local_navigation_visible", False, detail))


def _check_browser_drawer_visual_isolation(checks: list[Check], page) -> None:
    detail: dict[str, Any] = {}
    try:
        state = page.evaluate(
            """
            () => {
                const drawer = document.querySelector('#browserDrawer');
                if (!drawer) return {exists: false};
                const rect = drawer.getBoundingClientRect();
                const style = getComputedStyle(drawer);
                const parseAlpha = (color) => {
                    const match = String(color || '').match(/rgba?\\(([^)]+)\\)/i);
                    if (!match) return null;
                    const parts = match[1].split(',').map((part) => part.trim());
                    if (parts.length < 4) return 1;
                    const alpha = Number(parts[3]);
                    return Number.isFinite(alpha) ? alpha : null;
                };
                const sample = (x, y) => {
                    const el = document.elementFromPoint(x, y);
                    return {
                        x: Math.round(x),
                        y: Math.round(y),
                        tag: el ? el.tagName.toLowerCase() : '',
                        id: el ? el.id || '' : '',
                        className: el ? String(el.className || '') : '',
                        insideDrawer: !!(el && drawer.contains(el))
                    };
                };
                const samples = [
                    sample(rect.left + rect.width / 2, rect.top + 20),
                    sample(rect.left + rect.width / 2, rect.top + rect.height / 2),
                    sample(rect.right - 24, rect.bottom - 24)
                ];
                return {
                    exists: true,
                    rect: {
                        x: Math.round(rect.x),
                        y: Math.round(rect.y),
                        width: Math.round(rect.width),
                        height: Math.round(rect.height)
                    },
                    visible: !!(rect.width && rect.height),
                    backgroundColor: style.backgroundColor,
                    backgroundAlpha: parseAlpha(style.backgroundColor),
                    opacity: Number(style.opacity),
                    pointerEvents: style.pointerEvents,
                    position: style.position,
                    zIndex: style.zIndex,
                    samples
                };
            }
            """
        )
        detail.update(state or {})
        alpha = detail.get("backgroundAlpha")
        opacity = detail.get("opacity")
        samples = detail.get("samples") or []
        checks.append(
            Check(
                "browser_drawer_visual_isolation",
                bool(
                    detail.get("exists")
                    and detail.get("visible")
                    and detail.get("rect", {}).get("width", 0) >= 320
                    and detail.get("rect", {}).get("height", 0) >= 240
                    and (alpha is None or float(alpha) >= 0.96)
                    and (opacity is None or float(opacity) >= 0.96)
                    and detail.get("pointerEvents") != "none"
                    and all(sample.get("insideDrawer") for sample in samples)
                ),
                detail,
            )
        )
    except Exception as exc:
        detail["error"] = repr(exc)
        checks.append(Check("browser_drawer_visual_isolation", False, detail))


def _check_browser_permission_cycle_restore(checks: list[Check], page) -> None:
    detail: dict[str, Any] = {}
    try:
        result = page.evaluate(
            """
            async () => {
                const sid = typeof window._browserCurrentSessionId === 'function'
                    ? window._browserCurrentSessionId()
                    : ((typeof window.browserGetState === 'function' && window.browserGetState().session_id) || '');
                const readUi = () => {
                    const state = typeof window.browserGetState === 'function' ? window.browserGetState() : {};
                    const status = document.querySelector('#browserPermissionStatus');
                    const btn = document.querySelector('#browserPermissionBtn');
                    const badge = document.querySelector('#browserStatusValue');
                    return {
                        mode: String(state.permission_mode || 'none'),
                        status_text: (status && status.textContent || '').trim(),
                        button_text: (btn && btn.textContent || '').replace(/\\s+/g, ' ').trim(),
                        badge_text: (badge && badge.textContent || '').replace(/\\s+/g, ' ').trim()
                    };
                };
                const postPermission = async (body) => {
                    const resp = await fetch('/api/browser/permission', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(Object.assign({session_id: sid}, body || {}))
                    });
                    const data = await resp.json().catch(() => ({}));
                    if (!resp.ok) throw new Error(data.error || resp.statusText || 'permission update failed');
                    if (typeof window.browserRenderPermission === 'function') {
                        window.browserRenderPermission(data.permission || {mode: 'none'});
                    }
                    return data.permission || {mode: 'none'};
                };
                const waitMode = async (mode) => {
                    const started = Date.now();
                    while (Date.now() - started < 5000) {
                        const ui = readUi();
                        if (ui.mode === mode) return ui;
                        await new Promise(resolve => setTimeout(resolve, 100));
                    }
                    return readUi();
                };
                const initialPermission = await postPermission({action: 'status'});
                const initialMode = String(initialPermission.mode || 'none');
                const steps = [];
                await postPermission({mode: 'none', action: 'revoke'});
                steps.push(Object.assign({step: 'locked'}, await waitMode('none')));
                if (typeof window.browserTogglePermission !== 'function') {
                    throw new Error('browserTogglePermission unavailable');
                }
                await window.browserTogglePermission();
                steps.push(Object.assign({step: 'watch'}, await waitMode('read')));
                await window.browserTogglePermission();
                steps.push(Object.assign({step: 'control'}, await waitMode('control')));
                if (typeof window.browserStopPermission === 'function') {
                    await window.browserStopPermission();
                } else {
                    await postPermission({mode: 'none', action: 'revoke'});
                }
                steps.push(Object.assign({step: 'stopped'}, await waitMode('none')));
                if (initialMode === 'read' || initialMode === 'control') {
                    await postPermission({mode: initialMode, enabled: true});
                    steps.push(Object.assign({step: 'restored'}, await waitMode(initialMode)));
                } else {
                    steps.push(Object.assign({step: 'restored'}, await waitMode('none')));
                }
                return {session_id: sid, initial_mode: initialMode, steps, final_ui: readUi()};
            }
            """
        )
        detail.update(result or {})
        steps = detail.get("steps") or []
        by_step = {step.get("step"): step for step in steps if isinstance(step, dict)}
        initial_mode = str(detail.get("initial_mode") or "none")
        final_mode = str((detail.get("final_ui") or {}).get("mode") or "")
        checks.append(
            Check(
                "browser_permission_cycle_restore",
                bool(
                    by_step.get("locked", {}).get("mode") == "none"
                    and by_step.get("watch", {}).get("mode") == "read"
                    and by_step.get("control", {}).get("mode") == "control"
                    and by_step.get("stopped", {}).get("mode") == "none"
                    and final_mode == initial_mode
                ),
                detail,
            )
        )
    except Exception as exc:
        detail["error"] = repr(exc)
        checks.append(Check("browser_permission_cycle_restore", False, detail))


def _check_browser_agent_control_permission_enforcement(checks: list[Check], page, target_url: str) -> None:
    detail: dict[str, Any] = {"target_url": target_url}
    try:
        result = page.evaluate(
            """
            async ([targetUrl]) => {
                const sid = typeof window._browserCurrentSessionId === 'function'
                    ? window._browserCurrentSessionId()
                    : ((typeof window.browserGetState === 'function' && window.browserGetState().session_id) || '');
                const postJson = async (url, body) => {
                    const resp = await fetch(url, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(Object.assign({session_id: sid}, body || {}))
                    });
                    const data = await resp.json().catch(() => ({}));
                    return {http_ok: resp.ok, status: resp.status, data};
                };
                const permission = async (body) => {
                    const res = await postJson('/api/browser/permission', body);
                    if (!res.http_ok) throw new Error((res.data && res.data.error) || 'permission update failed');
                    if (typeof window.browserRenderPermission === 'function') {
                        window.browserRenderPermission((res.data && res.data.permission) || {mode: 'none'});
                    }
                    return (res.data && res.data.permission) || {mode: 'none'};
                };
                const agent = (action, body) => postJson('/api/browser/agent-control', Object.assign({action}, body || {}));
                const waitForFrame = async () => {
                    const started = Date.now();
                    while (Date.now() - started < 6000) {
                        const state = typeof window.browserGetState === 'function' ? window.browserGetState() : {};
                        if (state && state.url === targetUrl && state.frame_complete && state.frame_width > 0 && !state.busy) {
                            return state;
                        }
                        await new Promise(resolve => setTimeout(resolve, 150));
                    }
                    return typeof window.browserGetState === 'function' ? window.browserGetState() : {};
                };
                const initial = await permission({action: 'status'});
                const initialMode = String(initial.mode || 'none');
                const steps = [];

                await permission({mode: 'none', action: 'revoke'});
                const lockedSnapshot = await agent('snapshot');
                const lockedNavigate = await agent('navigate', {url: targetUrl});
                steps.push({step: 'locked', snapshot: lockedSnapshot, navigate: lockedNavigate});

                await permission({mode: 'read', enabled: true});
                const readSnapshot = await agent('snapshot');
                const readNavigate = await agent('navigate', {url: targetUrl});
                steps.push({step: 'read', snapshot: readSnapshot, navigate: readNavigate});

                await permission({mode: 'control', enabled: true});
                const controlNavigate = await agent('navigate', {url: targetUrl});
                const controlState = await waitForFrame();
                const controlSnapshot = await agent('snapshot');
                steps.push({step: 'control', navigate: controlNavigate, state: controlState, snapshot: controlSnapshot});

                if (initialMode === 'read' || initialMode === 'control') {
                    await permission({mode: initialMode, enabled: true});
                } else {
                    await permission({mode: 'none', action: 'revoke'});
                }
                const finalPermission = await permission({action: 'status'});
                return {session_id: sid, initial_mode: initialMode, final_mode: String(finalPermission.mode || 'none'), steps};
            }
            """,
            [target_url],
        )
        detail.update(result or {})
        steps = detail.get("steps") or []
        by_step = {step.get("step"): step for step in steps if isinstance(step, dict)}

        def blocked(response: dict[str, Any] | None, required: str) -> bool:
            data = (response or {}).get("data") or {}
            permission = data.get("permission") or {}
            return (
                data.get("ok") is False
                and data.get("code") == "browser_permission_required"
                and data.get("required_mode") == required
                and isinstance(permission, dict)
            )

        def allowed(response: dict[str, Any] | None) -> bool:
            data = (response or {}).get("data") or {}
            if data.get("ok") is True:
                return True
            if isinstance(data.get("browser"), dict) or isinstance(data.get("snapshot"), dict):
                return True
            return bool((response or {}).get("http_ok") and data and data.get("code") != "browser_permission_required")

        locked_step = by_step.get("locked") or {}
        read_step = by_step.get("read") or {}
        control_step = by_step.get("control") or {}
        control_state = control_step.get("state") or {}
        checks.append(
            Check(
                "browser_agent_control_permission_enforcement",
                bool(
                    blocked(locked_step.get("snapshot"), "read")
                    and blocked(locked_step.get("navigate"), "control")
                    and allowed(read_step.get("snapshot"))
                    and blocked(read_step.get("navigate"), "control")
                    and allowed(control_step.get("navigate"))
                    and allowed(control_step.get("snapshot"))
                    and control_state.get("url") == target_url
                    and control_state.get("frame_complete")
                    and str(detail.get("final_mode") or "none") == str(detail.get("initial_mode") or "none")
                ),
                detail,
            )
        )
    except Exception as exc:
        detail["error"] = repr(exc)
        checks.append(Check("browser_agent_control_permission_enforcement", False, detail))


def _check_browser_qa_report(checks: list[Check], page, target_url: str) -> None:
    detail: dict[str, Any] = {"target_url": target_url}
    try:
        result = page.evaluate(
            """
            async ([targetUrl]) => {
                const sid = typeof window._browserCurrentSessionId === 'function'
                    ? window._browserCurrentSessionId()
                    : ((typeof window.browserGetState === 'function' && window.browserGetState().session_id) || '');
                const postPermission = async (body) => {
                    const resp = await fetch('/api/browser/permission', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(Object.assign({session_id: sid}, body || {}))
                    });
                    const data = await resp.json().catch(() => ({}));
                    if (!resp.ok) throw new Error((data && data.error) || 'permission update failed');
                    if (typeof window.browserRenderPermission === 'function') {
                        window.browserRenderPermission((data && data.permission) || {mode: 'none'});
                    }
                    return (data && data.permission) || {mode: 'none'};
                };
                const waitForReady = async () => {
                    const started = Date.now();
                    while (Date.now() - started < 6000) {
                        const state = typeof window.browserGetState === 'function' ? window.browserGetState() : {};
                        if (state && state.url === targetUrl && state.frame_complete && state.frame_width > 0 && !state.busy) {
                            return state;
                        }
                        await new Promise(resolve => setTimeout(resolve, 150));
                    }
                    return typeof window.browserGetState === 'function' ? window.browserGetState() : {};
                };
                const initial = await postPermission({action: 'status'});
                const initialMode = String(initial.mode || 'none');
                await postPermission({mode: 'read', enabled: true});
                const readyState = await waitForReady();
                const qaResp = await fetch('/api/browser/qa?session_id=' + encodeURIComponent(sid), {cache: 'no-store'});
                const qa = await qaResp.json().catch(() => ({}));
                if (initialMode === 'read' || initialMode === 'control') {
                    await postPermission({mode: initialMode, enabled: true});
                } else {
                    await postPermission({mode: 'none', action: 'revoke'});
                }
                const finalPermission = await postPermission({action: 'status'});
                return {
                    session_id: sid,
                    initial_mode: initialMode,
                    final_mode: String(finalPermission.mode || 'none'),
                    http_ok: qaResp.ok,
                    status: qaResp.status,
                    ready_state: readyState,
                    qa
                };
            }
            """,
            [target_url],
        )
        detail.update(result or {})
        qa = detail.get("qa") or {}
        ready_state = detail.get("ready_state") or {}
        visual = qa.get("visual_analysis") or qa.get("screenshot") or qa.get("visual") or {}
        technical = qa.get("technical_diagnostics") or qa.get("diagnostics") or qa.get("technical") or {}
        checks.append(
            Check(
                "browser_qa_report_available",
                bool(
                    detail.get("http_ok")
                    and detail.get("status") == 200
                    and ready_state.get("url") == target_url
                    and ready_state.get("frame_complete")
                    and (
                        qa.get("ok") is True
                        or qa.get("browser_ready") is True
                        or qa.get("rendered_frame_ready") is True
                    )
                    and (
                        isinstance(visual, dict)
                        or isinstance(qa.get("visual_findings"), list)
                    )
                    and (
                        isinstance(technical, dict)
                        or isinstance(qa.get("technical_findings"), list)
                    )
                    and str(detail.get("final_mode") or "none") == str(detail.get("initial_mode") or "none")
                ),
                detail,
            )
        )
    except Exception as exc:
        detail["error"] = repr(exc)
        checks.append(Check("browser_qa_report_available", False, detail))


def _check_browser_qa_detects_broken_fixture(
    checks: list[Check],
    page,
    fixture_url: str,
    restore_url: str,
) -> None:
    detail: dict[str, Any] = {"fixture_url": fixture_url, "restore_url": restore_url}
    try:
        result = page.evaluate(
            """
            async ([fixtureUrl, restoreUrl]) => {
                const sid = typeof window._browserCurrentSessionId === 'function'
                    ? window._browserCurrentSessionId()
                    : ((typeof window.browserGetState === 'function' && window.browserGetState().session_id) || '');
                const postJson = async (url, body) => {
                    const resp = await fetch(url, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(Object.assign({session_id: sid}, body || {}))
                    });
                    const data = await resp.json().catch(() => ({}));
                    return {http_ok: resp.ok, status: resp.status, data};
                };
                const permission = async (body) => {
                    const res = await postJson('/api/browser/permission', body);
                    if (!res.http_ok) throw new Error((res.data && res.data.error) || 'permission update failed');
                    if (typeof window.browserRenderPermission === 'function') {
                        window.browserRenderPermission((res.data && res.data.permission) || {mode: 'none'});
                    }
                    return (res.data && res.data.permission) || {mode: 'none'};
                };
                const agent = (action, body) => postJson('/api/browser/agent-control', Object.assign({action}, body || {}));
                const waitForState = async (predicate) => {
                    const started = Date.now();
                    let current = {};
                    while (Date.now() - started < 8000) {
                        current = typeof window.browserGetState === 'function' ? window.browserGetState() : {};
                        if (predicate(current)) return current;
                        await new Promise(resolve => setTimeout(resolve, 150));
                    }
                    return current;
                };
                const initial = await permission({action: 'status'});
                const initialMode = String(initial.mode || 'none');
                await permission({mode: 'control', enabled: true});
                const navigate = await agent('navigate', {url: fixtureUrl});
                const ready = await waitForState(state => state && state.url === fixtureUrl && state.frame_complete && !state.busy);
                await permission({mode: 'read', enabled: true});
                const qaResp = await fetch('/api/browser/qa?session_id=' + encodeURIComponent(sid), {cache: 'no-store'});
                const qa = await qaResp.json().catch(() => ({}));

                await permission({mode: 'control', enabled: true});
                const restoredNavigate = await agent('navigate', {url: restoreUrl});
                const restoredReady = await waitForState(state => state && state.url === restoreUrl && state.frame_complete && !state.busy);
                const qaReport = qa && qa.report && typeof qa.report === 'object' ? qa.report : qa;
                const qaText = String((qa && (qa.text || qa.report_text || qa.markdown)) || '');
                let staleUi = {};
                if (typeof window._browserRenderQaCard === 'function') {
                    window._browserLastTestReportText = qaText;
                    window._browserLastTestReport = qaReport;
                    window._browserRenderQaCard(qaText, qaReport);
                    const card = document.querySelector('#browserQaCard');
                    const status = document.querySelector('#browserQaCardStatus');
                    const url = document.querySelector('#browserQaCardUrl');
                    const fix = document.querySelector('#browserQaFixBtn');
                    const repro = document.querySelector('#browserQaReproBtn');
                    const retest = document.querySelector('#browserQaRetestBtn');
                    staleUi = {
                        render_available: true,
                        card_hidden: !!(card && card.hidden),
                        card_stale: card ? card.dataset.stale === '1' : false,
                        card_scope_unknown: card ? card.dataset.scopeUnknown === '1' : false,
                        status_text: String(status && status.textContent || '').trim(),
                        url_text: String(url && url.textContent || '').trim(),
                        url_title: String(url && url.getAttribute('title') || '').trim(),
                        fix_disabled: !!(fix && fix.disabled),
                        fix_text: String(fix && fix.textContent || '').trim(),
                        fix_title: String(fix && fix.getAttribute('title') || '').trim(),
                        repro_disabled: !!(repro && repro.disabled),
                        repro_text: String(repro && repro.textContent || '').trim(),
                        repro_title: String(repro && repro.getAttribute('title') || '').trim(),
                        retest_text: String(retest && retest.textContent || '').trim(),
                        retest_title: String(retest && retest.getAttribute('title') || '').trim(),
                        qa_state: typeof window.browserGetQaState === 'function' ? window.browserGetQaState() : null
                    };
                } else {
                    staleUi = {render_available: false};
                }
                if (initialMode === 'read' || initialMode === 'control') {
                    await permission({mode: initialMode, enabled: true});
                } else {
                    await permission({mode: 'none', action: 'revoke'});
                }
                const finalPermission = await permission({action: 'status'});
                return {
                    session_id: sid,
                    initial_mode: initialMode,
                    final_mode: String(finalPermission.mode || 'none'),
                    navigate,
                    ready,
                    http_ok: qaResp.ok,
                    status: qaResp.status,
                    qa,
                    stale_ui: staleUi,
                    restored_navigate: restoredNavigate,
                    restored_ready: restoredReady
                };
            }
            """,
            [fixture_url, restore_url],
        )
        detail.update(result or {})
        qa = detail.get("qa") or {}
        report = qa.get("report") if isinstance(qa.get("report"), dict) else qa
        findings = [str(item) for item in (report.get("findings") or [])]
        layout_findings = [str(item) for item in (report.get("layout_findings") or [])]
        accessibility_findings = [str(item) for item in (report.get("accessibility_findings") or [])]
        all_findings_text = "\n".join(findings + layout_findings + accessibility_findings)
        restored_ready = detail.get("restored_ready") or {}
        stale_ui = detail.get("stale_ui") or {}
        qa_state = stale_ui.get("qa_state") if isinstance(stale_ui.get("qa_state"), dict) else {}
        qa_actions = qa_state.get("actions") if isinstance(qa_state.get("actions"), dict) else {}
        fix_action = qa_actions.get("fix") if isinstance(qa_actions.get("fix"), dict) else {}
        repro_action = qa_actions.get("repro") if isinstance(qa_actions.get("repro"), dict) else {}
        checks.append(
            Check(
                "browser_qa_detects_broken_fixture",
                bool(
                    detail.get("http_ok")
                    and detail.get("status") == 200
                    and (detail.get("ready") or {}).get("url") == fixture_url
                    and (detail.get("ready") or {}).get("frame_complete")
                    and "horizontal overflow" in all_findings_text.lower()
                    and "large fixed/sticky overlay" in all_findings_text.lower()
                    and "accessible label" in all_findings_text.lower()
                    and "missing alt" in all_findings_text.lower()
                    and "No visible H1 heading was detected." in all_findings_text
                    and restored_ready.get("url") == restore_url
                    and stale_ui.get("render_available")
                    and stale_ui.get("card_stale")
                    and "STALE" in str(stale_ui.get("status_text") or "")
                    and fixture_url in str(stale_ui.get("url_text") or stale_ui.get("url_title") or "")
                    and restore_url in str(stale_ui.get("url_title") or "")
                    and stale_ui.get("fix_disabled")
                    and stale_ui.get("repro_disabled")
                    and str(stale_ui.get("fix_text") or "").lower() == "retest first"
                    and str(stale_ui.get("repro_text") or "").lower() == "retest first"
                    and str(stale_ui.get("retest_text") or "").lower() in {"retest url", "retest"}
                    and (
                        not qa_state.get("has_report")
                        or (
                            qa_state.get("stale")
                            and fix_action.get("disabled")
                            and fix_action.get("reason") == "stale"
                            and repro_action.get("disabled")
                            and repro_action.get("reason") == "stale"
                        )
                    )
                    and str(detail.get("final_mode") or "none") == str(detail.get("initial_mode") or "none")
                ),
                {
                    **detail,
                    "detected_findings": findings,
                    "detected_layout_findings": layout_findings,
                    "detected_accessibility_findings": accessibility_findings,
                },
            )
        )
    except Exception as exc:
        detail["error"] = repr(exc)
        checks.append(Check("browser_qa_detects_broken_fixture", False, detail))


def _check_browser_agent_control_fixture_interaction(
    checks: list[Check],
    page,
    fixture_url: str,
    restore_url: str,
) -> None:
    detail: dict[str, Any] = {"fixture_url": fixture_url, "restore_url": restore_url}
    try:
        result = page.evaluate(
            """
            async ([fixtureUrl, restoreUrl]) => {
                const sid = typeof window._browserCurrentSessionId === 'function'
                    ? window._browserCurrentSessionId()
                    : ((typeof window.browserGetState === 'function' && window.browserGetState().session_id) || '');
                const postJson = async (url, body) => {
                    const resp = await fetch(url, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(Object.assign({session_id: sid}, body || {}))
                    });
                    const data = await resp.json().catch(() => ({}));
                    return {http_ok: resp.ok, status: resp.status, data};
                };
                const permission = async (body) => {
                    const res = await postJson('/api/browser/permission', body);
                    if (!res.http_ok) throw new Error((res.data && res.data.error) || 'permission update failed');
                    if (typeof window.browserRenderPermission === 'function') {
                        window.browserRenderPermission((res.data && res.data.permission) || {mode: 'none'});
                    }
                    return (res.data && res.data.permission) || {mode: 'none'};
                };
                const agent = (action, body) => postJson('/api/browser/agent-control', Object.assign({action}, body || {}));
                const waitForState = async (predicate) => {
                    const started = Date.now();
                    let current = {};
                    while (Date.now() - started < 8000) {
                        current = typeof window.browserGetState === 'function' ? window.browserGetState() : {};
                        if (predicate(current)) return current;
                        await new Promise(resolve => setTimeout(resolve, 150));
                    }
                    return current;
                };
                const initial = await permission({action: 'status'});
                const initialMode = String(initial.mode || 'none');
                await permission({mode: 'control', enabled: true});

                const navigate = await agent('navigate', {url: fixtureUrl});
                const fixtureReady = await waitForState(state => state && state.url === fixtureUrl && state.frame_complete && !state.busy);
                const typed = await agent('type', {selector: '#fixtureInput', text: 'sidekick-agent'});
                const clicked = await agent('click', {selector: '#fixtureButton'});
                const scrolled = await agent('scroll', {direction: 'down'});
                const snapshot = await agent('snapshot');
                const afterInteraction = typeof window.browserGetState === 'function' ? window.browserGetState() : {};
                const visibleTrace = (() => {
                    const textOf = (selector) => (document.querySelector(selector)?.textContent || '').replace(/\\s+/g, ' ').trim();
                    return {
                        action_summary: textOf('#browserActionSummary'),
                        status_url: textOf('#browserStatusUrl'),
                        header_status: textOf('#browserStatusValue'),
                        drawer_excerpt: textOf('#browserDrawer').slice(0, 360)
                    };
                })();
                const visibleFrame = (() => {
                    const img = document.querySelector('#browserFrameImage');
                    const state = typeof window.browserGetState === 'function' ? window.browserGetState() : {};
                    return {
                        state_frame_rev: Number(state && state.frame_rev || 0),
                        state_frame_complete: !!(state && state.frame_complete),
                        image_src: img ? String(img.currentSrc || img.src || '') : '',
                        image_complete: !!(img && img.complete),
                        image_width: Number(img && img.naturalWidth || 0),
                        image_height: Number(img && img.naturalHeight || 0)
                    };
                })();

                const restoredNavigate = await agent('navigate', {url: restoreUrl});
                const restoredReady = await waitForState(state => state && state.url === restoreUrl && state.frame_complete && !state.busy);
                if (initialMode === 'read' || initialMode === 'control') {
                    await permission({mode: initialMode, enabled: true});
                } else {
                    await permission({mode: 'none', action: 'revoke'});
                }
                const finalPermission = await permission({action: 'status'});
                return {
                    session_id: sid,
                    initial_mode: initialMode,
                    final_mode: String(finalPermission.mode || 'none'),
                    navigate,
                    fixture_ready: fixtureReady,
                    typed,
                    clicked,
                    scrolled,
                    snapshot,
                    after_interaction: afterInteraction,
                    visible_trace: visibleTrace,
                    visible_frame: visibleFrame,
                    restored_navigate: restoredNavigate,
                    restored_ready: restoredReady
                };
            }
            """,
            [fixture_url, restore_url],
        )
        detail.update(result or {})
        snapshot_data = ((detail.get("snapshot") or {}).get("data") or {})
        snapshot_text = str(snapshot_data.get("text") or "")
        after_interaction = detail.get("after_interaction") or {}
        snapshot_state = snapshot_data.get("state") or {}
        visible_trace = detail.get("visible_trace") or {}
        visible_frame = detail.get("visible_frame") or {}
        trace_text = " ".join(str(visible_trace.get(key) or "") for key in ("action_summary", "status_url", "header_status", "drawer_excerpt"))
        restored_ready = detail.get("restored_ready") or {}
        fixture_rev = int(float((detail.get("fixture_ready") or {}).get("frame_rev") or 0))
        typed_rev = int(float((((detail.get("typed") or {}).get("data") or {}).get("state") or {}).get("frame_rev") or 0))
        clicked_rev = int(float((((detail.get("clicked") or {}).get("data") or {}).get("state") or {}).get("frame_rev") or 0))
        scrolled_rev = int(float((((detail.get("scrolled") or {}).get("data") or {}).get("state") or {}).get("frame_rev") or 0))
        snapshot_rev = int(float(snapshot_state.get("frame_rev") or 0))
        visible_rev = int(float(visible_frame.get("state_frame_rev") or 0))
        checks.append(
            Check(
                "browser_agent_control_fixture_interaction",
                bool(
                    ((detail.get("navigate") or {}).get("http_ok"))
                    and ((detail.get("typed") or {}).get("http_ok"))
                    and ((detail.get("clicked") or {}).get("http_ok"))
                    and ((detail.get("scrolled") or {}).get("http_ok"))
                    and ((detail.get("snapshot") or {}).get("http_ok"))
                    and "typed: sidekick-agent" in snapshot_text
                    and "clicks: 1" in snapshot_text
                    and int(float(snapshot_state.get("scroll_y") or 0)) > 0
                    and snapshot_state.get("last_action") in {"snapshot", "scroll"}
                    and fixture_rev > 0
                    and typed_rev > fixture_rev
                    and clicked_rev >= typed_rev
                    and scrolled_rev >= clicked_rev
                    and snapshot_rev >= scrolled_rev
                    and visible_rev == snapshot_rev
                    and visible_frame.get("state_frame_complete")
                    and visible_frame.get("image_complete")
                    and int(float(visible_frame.get("image_width") or 0)) > 0
                    and int(float(visible_frame.get("image_height") or 0)) > 0
                    and str(visible_frame.get("image_src") or "").strip() != ""
                    and fixture_url in trace_text
                    and any(token in trace_text for token in ("scroll", "click #fixtureButton", "type #fixtureInput"))
                    and restored_ready.get("url") == restore_url
                    and str(detail.get("final_mode") or "none") == str(detail.get("initial_mode") or "none")
                ),
                detail,
            )
        )
    except Exception as exc:
        detail["error"] = repr(exc)
        checks.append(Check("browser_agent_control_fixture_interaction", False, detail))


def _titlebar_space_slugs(page) -> list[str]:
    try:
        return page.locator('[data-testid="titlebar-space-option"]').evaluate_all(
            "(els) => els.map((el) => el.getAttribute('data-titlebar-space-slug') || '')"
        )
    except Exception:
        return []


def _open_titlebar_space_menu(page, timeout: int = 5000) -> bool:
    selector = '[data-testid="titlebar-space-option"]'
    try:
        visible = page.locator(selector).evaluate_all(
            "(els) => els.filter((el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)).length"
        )
        if visible:
            return True
    except Exception:
        pass
    page.locator('[data-testid="titlebar-space-button"]').click(timeout=3000)
    try:
        page.wait_for_selector(selector, state="visible", timeout=timeout)
        return True
    except PlaywrightTimeoutError:
        try:
            page.locator('[data-testid="titlebar-space-button"]').click(timeout=3000)
            page.wait_for_selector(selector, state="visible", timeout=timeout)
            return True
        except Exception:
            return False


def _check_space_conversation_navigation(
    checks: list[Check],
    page,
    workspace: str,
    max_switch_ms: int,
    max_spaces: int = 3,
) -> None:
    detail: dict[str, Any] = {"workspace": workspace, "max_switch_ms": max_switch_ms, "spaces": []}
    try:
        if not _open_titlebar_space_menu(page):
            checks.append(Check("space_conversation_navigation_responsive", False, {"error": "space menu did not open"}))
            return

        slugs = [slug for slug in _titlebar_space_slugs(page) if slug]
        ordered_slugs: list[str] = []
        if workspace in slugs:
            ordered_slugs.append(workspace)
        for slug in slugs:
            if slug not in ordered_slugs:
                ordered_slugs.append(slug)
            if len(ordered_slugs) >= max_spaces:
                break
        detail["available_slugs"] = slugs
        detail["tested_slugs"] = ordered_slugs

        if not ordered_slugs:
            checks.append(Check("space_conversation_navigation_responsive", False, detail))
            return

        def visible_session_count() -> int:
            return page.locator('[data-testid="session-list-item"]').evaluate_all(
                "(els) => els.filter((el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)).length"
            )

        for slug in ordered_slugs:
            step: dict[str, Any] = {"slug": slug}
            started = time.time()
            current_url = page.url
            if f"workspace={slug}" not in current_url:
                if not _open_titlebar_space_menu(page):
                    step["error"] = "space menu did not open before switch"
                    detail["spaces"].append(step)
                    continue
                selector = f'[data-testid="titlebar-space-option"][data-titlebar-space-slug="{slug}"]'
                count = _unique_count(page, selector)
                step["option_count"] = count
                if count != 1:
                    step["error"] = "space option not unique"
                    detail["spaces"].append(step)
                    continue
                page.locator(selector).click(timeout=3000)
                page.wait_for_url(f"**workspace={slug}**", timeout=10000)
            try:
                page.wait_for_selector('[data-testid="session-list-item"]', timeout=8000)
            except PlaywrightTimeoutError:
                pass
            page.wait_for_timeout(150)
            switch_ms = round((time.time() - started) * 1000)
            step["switch_or_ready_ms"] = switch_ms
            step["session_count"] = visible_session_count()
            step["space_button_text"] = page.locator('[data-testid="titlebar-space-button"]').inner_text(timeout=3000)

            if step["session_count"] > 0:
                before_click_url = page.url
                first_item = page.locator('[data-testid="session-list-item"]').first
                step["first_session_text"] = first_item.inner_text(timeout=3000).strip()[:120]
                click_started = time.time()
                first_item.click(timeout=3000)
                page.wait_for_timeout(350)
                click_ms = round((time.time() - click_started) * 1000)
                step["conversation_click_ms"] = click_ms
                step["url_before_click"] = before_click_url
                step["url_after_click"] = page.url
                step["chat_visible"] = page.locator("#chatMessages, #messages, .chat-main, .messages").count() > 0
            else:
                step["skipped"] = "no visible sessions in this space"
            detail["spaces"].append(step)

        if workspace in ordered_slugs and f"workspace={workspace}" not in page.url:
            if _open_titlebar_space_menu(page):
                restore_selector = (
                    f'[data-testid="titlebar-space-option"]'
                    f'[data-titlebar-space-slug="{workspace}"]'
                )
                if _unique_count(page, restore_selector) == 1:
                    page.locator(restore_selector).click(timeout=3000)
                    page.wait_for_url(f"**workspace={workspace}**", timeout=10000)
                    page.wait_for_timeout(150)

        spaces = detail.get("spaces") or []
        checks.append(
            Check(
                "space_conversation_navigation_responsive",
                bool(
                    spaces
                    and all(
                        not step.get("error")
                        and int(step.get("switch_or_ready_ms") or 999999) <= max(max_switch_ms, 2500)
                        and (
                            "conversation_click_ms" not in step
                            or int(step.get("conversation_click_ms") or 999999) <= 3000
                        )
                        and (step.get("session_count") is None or int(step.get("session_count") or 0) > 0 or step.get("skipped"))
                        for step in spaces
                    )
                ),
                detail,
            )
        )
    except Exception as exc:
        detail["error"] = repr(exc)
        checks.append(Check("space_conversation_navigation_responsive", False, detail))


def run_smoke(
    base_url: str,
    session_id: str,
    workspace: str,
    headless: bool,
    cdp_url: str | None = None,
    switch_workspace: str | None = DEFAULT_SWITCH_WORKSPACE,
    artifact_dir: str = DEFAULT_ARTIFACT_DIR,
    max_load_ms: int = DEFAULT_MAX_LOAD_MS,
    max_switch_ms: int = DEFAULT_MAX_SWITCH_MS,
) -> dict[str, Any]:
    base_url = base_url.rstrip("/")
    session_id = str(session_id or "").strip()
    auto_cleanup_session = False
    checks: list[Check] = []
    timings: dict[str, int] = {}
    console_messages: list[dict[str, str]] = []

    fingerprint_url = f"{base_url}/api/runtime/fingerprint"
    try:
        fingerprint = _get_json(fingerprint_url)
        marker = fingerprint.get("source_marker")
        checks.append(Check("runtime_fingerprint_marker", marker == "sidekick-runtime-fingerprint-v1", fingerprint))
    except Exception as exc:
        fingerprint = {"error": repr(exc)}
        checks.append(Check("runtime_fingerprint_marker", False, fingerprint))

    if not session_id:
        created = _post_json(
            f"{base_url}/api/session/new",
            {},
            headers={"X-Hermes-Workspace": workspace},
        )
        created_session = created.get("session") or {}
        session_id = str(created_session.get("session_id") or "").strip()
        if not session_id:
            raise RuntimeError("smoke session id missing")
        created_workspace = str(created_session.get("workspace") or "").strip()
        created_workspace_slug = Path(created_workspace).name.strip().lower() or workspace
        auto_cleanup_session = True

    page_url = f"{base_url}/session/{session_id}?workspace={workspace}&cb={int(time.time() * 1000)}"

    browser = None
    context = None
    page = None
    try:
        with sync_playwright() as playwright:
            browser = (
                playwright.chromium.connect_over_cdp(cdp_url)
                if cdp_url
                else playwright.chromium.launch(headless=headless)
            )
            try:
                if cdp_url and browser.contexts:
                    context = browser.contexts[0]
                    page = context.pages[0] if context.pages else context.new_page()
                else:
                    context = browser.new_context(viewport={"width": 1600, "height": 900})
                    page = context.new_page()
                page.add_init_script(
                    """
                    try {
                        localStorage.setItem('sidekick-browser-drawer-open', '0');
                        localStorage.setItem('sidekick-browser-fullscreen', '0');
                        localStorage.setItem('sidekick-browser-split', '0');
                    } catch (_) {}
                    """
                )
                page.on(
                    "console",
                    lambda msg: console_messages.append({"type": msg.type, "text": msg.text[:500]}),
                )

                started = time.time()
                # `domcontentloaded` can be delayed by cold external script loads
                # even when the app shell is already present. Commit the navigation
                # first, then let the explicit selector waits prove readiness.
                page.goto(page_url, wait_until="commit", timeout=30000)
                page.wait_for_selector('[data-testid="titlebar-space-button"]', timeout=10000)
                timings["load_ms"] = round((time.time() - started) * 1000)
                try:
                    page.wait_for_selector('[data-testid="session-list-item"]', timeout=20000)
                except PlaywrightTimeoutError:
                    try:
                        page.wait_for_selector('[data-testid="session-list-loading"]', state="detached", timeout=20000)
                    except PlaywrightTimeoutError:
                        pass
                timings["session_list_hydration_ms"] = round((time.time() - started) * 1000)
                page.wait_for_timeout(1000)
                checks.append(
                    Check(
                        "load_within_budget",
                        timings["load_ms"] <= max_load_ms,
                        {"actual_ms": timings["load_ms"], "max_ms": max_load_ms},
                    )
                )

            finally:
                if page is not None and not cdp_url:
                    try:
                        page.close()
                    except Exception:
                        pass
                if context is not None and not cdp_url:
                    try:
                        context.close()
                    except Exception:
                        pass
                if browser is not None and not cdp_url:
                    try:
                        browser.close()
                    except Exception:
                        pass
            current_approval_mode = page.evaluate(
                "() => String(window._approvalMode || '').trim().toLowerCase()"
            )
            if current_approval_mode != "off":
                _post_json(f"{base_url}/api/approval", {"mode": "off"})
                page.evaluate(
                    """
                    () => {
                        if (typeof window._setApprovalModeIndicator === 'function') {
                            window._setApprovalModeIndicator('off');
                        } else {
                            window._approvalMode = 'off';
                        }
                    }
                    """
                )
                page.wait_for_timeout(150)
            _check_unique(checks, page, "titlebar_space_button", '[data-testid="titlebar-space-button"]')
            _check_unique(checks, page, "titlebar_language_button", "#btnLangSelector")
            _check_titlebar_language_space_order(checks, page)
            _check_unique(checks, page, "approval_mode_badge", "#approvalModeBadge")
            _check_approval_badge_cycle(checks, page)
            _check_approval_slash_command_cycle(checks, page)
            _check_persistent_goal_reconciles_server_state(checks, page)
            _check_goal_reload_resume_autostarts(checks, browser, base_url, workspace)
            _check_unique(checks, page, "browser_status_badge", '[data-testid="browser-status-badge"]')
            _check_unique(checks, page, "browser_actions_button", '[data-testid="browser-actions-button"]')
            _check_unique(checks, page, "composer_browser_drawer_button", '[data-testid="composer-browser-drawer-button"]')
            _check_unique(checks, page, "browser_drawer_webui_smoke_button", '[data-testid="browser-drawer-webui-smoke-button"]')
            _check_unique(checks, page, "workflow_browser_webui_smoke_action", '[data-testid="workflow-browser-webui-smoke-action"]')
            _check_browser_drawer_navigation(checks, page, f"{base_url}/api/runtime/fingerprint")
            _check_browser_drawer_visual_isolation(checks, page)
            _check_browser_permission_cycle_restore(checks, page)
            _check_browser_agent_control_permission_enforcement(checks, page, f"{base_url}/api/runtime/fingerprint")
            _check_browser_qa_report(checks, page, f"{base_url}/api/runtime/fingerprint")
            _check_browser_qa_detects_broken_fixture(
                checks,
                page,
                f"{base_url}/static/browser-qa-broken-fixture.html",
                f"{base_url}/api/runtime/fingerprint",
            )
            _check_browser_agent_control_fixture_interaction(
                checks,
                page,
                f"{base_url}/static/browser-control-fixture.html",
                f"{base_url}/api/runtime/fingerprint",
            )

            session_items = _unique_count(page, '[data-testid="session-list-item"]')
            checks.append(Check("session_list_items_present", session_items > 0, {"count": session_items}))
            _check_space_conversation_navigation(checks, page, workspace, max_switch_ms)

            _open_titlebar_space_menu(page)
            _check_unique(
                checks,
                page,
                "titlebar_current_workspace_option",
                f'[data-testid="titlebar-space-option"][data-titlebar-space-slug="{workspace}"]',
            )

            legacy_titlebar_collision = _unique_count(page, f'.titlebar-space-dd-item[data-space-slug="{workspace}"]')
            checks.append(
                Check(
                    "no_titlebar_legacy_space_slug_collision",
                    legacy_titlebar_collision == 0,
                    {"count": legacy_titlebar_collision},
                )
            )
            page.locator('[data-testid="browser-actions-button"]').click(timeout=3000)
            page.wait_for_timeout(100)

            page.locator('[data-testid="browser-actions-button"]').click(timeout=3000)
            page.wait_for_timeout(150)
            _check_unique(checks, page, "browser_header_drawer_action", '[data-testid="browser-header-drawer-action"]')
            _check_unique(
                checks,
                page,
                "browser_header_permission_action",
                '[data-testid="browser-header-permission-action"]',
            )
            _check_unique(
                checks,
                page,
                "browser_header_webui_smoke_action",
                '[data-testid="browser-header-webui-smoke-action"]',
            )
            page.keyboard.press("Escape")
            page.wait_for_timeout(100)

            if switch_workspace and switch_workspace != workspace:
                page.goto(page_url, wait_until="commit", timeout=30000)
                page.wait_for_selector('[data-testid="titlebar-space-button"]', timeout=10000)
                page.wait_for_timeout(500)
                _open_titlebar_space_menu(page)
                switch_selector = (
                    f'[data-testid="titlebar-space-option"]'
                    f'[data-titlebar-space-slug="{switch_workspace}"]'
                )
                switch_count = _unique_count(page, switch_selector)
                checks.append(
                    Check(
                        "switch_workspace_option_unique",
                        switch_count == 1,
                        {
                            "selector": switch_selector,
                            "count": switch_count,
                            "available_slugs": _titlebar_space_slugs(page),
                        },
                    )
                )
                if switch_count == 1:
                    started = time.time()
                    page.locator(switch_selector).click(timeout=3000)
                    page.wait_for_url(f"**workspace={switch_workspace}**", timeout=10000)
                    page.wait_for_timeout(300)
                    timings["switch_workspace_ms"] = round((time.time() - started) * 1000)
                    checks.append(
                        Check(
                            "switch_workspace_within_budget",
                            timings["switch_workspace_ms"] <= max_switch_ms,
                            {"actual_ms": timings["switch_workspace_ms"], "max_ms": max_switch_ms},
                        )
                    )
                    switched_space_text = page.locator('[data-testid="titlebar-space-button"]').inner_text(timeout=3000)
                    checks.append(
                        Check(
                            "switch_workspace_visible",
                            _workspace_button_text_matches(switch_workspace, switched_space_text),
                            {"space_button_text": switched_space_text},
                        )
                    )

                    _open_titlebar_space_menu(page)
                    restore_selector = (
                        f'[data-testid="titlebar-space-option"]'
                        f'[data-titlebar-space-slug="{workspace}"]'
                    )
                    restore_count = _unique_count(page, restore_selector)
                    checks.append(
                        Check(
                            "restore_workspace_option_unique",
                            restore_count == 1,
                            {"selector": restore_selector, "count": restore_count},
                        )
                    )
                    if restore_count == 1:
                        started = time.time()
                        page.locator(restore_selector).click(timeout=3000)
                        page.wait_for_url(f"**workspace={workspace}**", timeout=10000)
                        page.wait_for_timeout(300)
                        timings["restore_workspace_ms"] = round((time.time() - started) * 1000)
                        checks.append(
                            Check(
                                "restore_workspace_within_budget",
                                timings["restore_workspace_ms"] <= max_switch_ms,
                                {"actual_ms": timings["restore_workspace_ms"], "max_ms": max_switch_ms},
                            )
                        )
                        restored_space_text = page.locator('[data-testid="titlebar-space-button"]').inner_text(
                            timeout=3000
                        )
                        checks.append(
                        Check(
                            "restore_workspace_visible",
                            _workspace_button_text_matches(workspace, restored_space_text),
                            {"space_button_text": restored_space_text},
                        )
                    )

            expected_agent_control_403 = any(
                check.name == "browser_agent_control_permission_enforcement" and check.ok
                for check in checks
            )
            expected_browser_fixture_favicon_noise = any(
                check.name == "browser_agent_control_fixture_interaction" and check.ok
                for check in checks
            )
            console_errors = []
            for message in console_messages:
                if message.get("type") not in {"error", "warning"}:
                    continue
                text = str(message.get("text") or "")
                if (
                    expected_agent_control_403
                    and message.get("type") == "error"
                    and "Failed to load resource" in text
                    and "403" in text
                ):
                    continue
                if (
                    expected_browser_fixture_favicon_noise
                    and message.get("type") == "error"
                    and text.strip() == "Failed to load resource: net::ERR_FILE_NOT_FOUND"
                ):
                    continue
                if message.get("type") == "warning" and (
                    text.startswith("space session metadata unavailable, falling back to full list render")
                    or text.startswith("renderSessionList projects unavailable, continuing without projects")
                    or text.startswith("renderSessionList projects unavailable after sessions render")
                ):
                    continue
                console_errors.append(message)
            checks.append(Check("no_console_errors_or_warnings", not console_errors, console_errors[:20]))

            artifacts: dict[str, str] = {}
            artifact_root = Path(artifact_dir)
            artifact_root.mkdir(parents=True, exist_ok=True)
            screenshot_prefix = "failure" if any(not check.ok for check in checks) else "pass"
            screenshot_path = artifact_root / f"{screenshot_prefix}-{int(time.time() * 1000)}.png"
            page.screenshot(path=str(screenshot_path), full_page=True)
            artifacts["screenshot"] = str(screenshot_path.resolve())
            if any(not check.ok for check in checks):
                artifacts["failure_screenshot"] = artifacts["screenshot"]

    finally:
        try:
            if browser is not None:
                browser.close()
        except Exception:
            pass
        if auto_cleanup_session and session_id:
            try:
                _post_json(
                    _workspace_scoped_url(base_url, "/api/session/delete", created_workspace_slug),
                    {"session_id": session_id},
                )
            except Exception:
                pass

    result = {
        "ok": all(check.ok for check in checks),
        "url": page_url,
        "timings": timings,
        "artifacts": artifacts,
        "checks": [check.__dict__ for check in checks],
    }
    return result


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Smoke-test Sidekick WebUI browser controls.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--session-id", default=DEFAULT_SESSION_ID)
    parser.add_argument("--workspace", default=DEFAULT_WORKSPACE)
    parser.add_argument("--switch-workspace", default=DEFAULT_SWITCH_WORKSPACE)
    parser.add_argument("--no-switch", action="store_true", help="Skip the workspace switch/restore check.")
    parser.add_argument("--artifact-dir", default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--max-load-ms", type=int, default=DEFAULT_MAX_LOAD_MS)
    parser.add_argument("--max-switch-ms", type=int, default=DEFAULT_MAX_SWITCH_MS)
    parser.add_argument("--headed", action="store_true", help="Run headed Chromium instead of headless.")
    parser.add_argument("--cdp-url", default="", help="Connect to an existing Chromium/Chrome DevTools endpoint instead of launching a fresh browser.")
    args = parser.parse_args()

    try:
        result = run_smoke(
            base_url=args.base_url,
            session_id=args.session_id,
            workspace=args.workspace,
            headless=not args.headed,
            cdp_url=(args.cdp_url.strip() or None),
            switch_workspace=None if args.no_switch else args.switch_workspace,
            artifact_dir=args.artifact_dir,
            max_load_ms=args.max_load_ms,
            max_switch_ms=args.max_switch_ms,
        )
    except PlaywrightTimeoutError as exc:
        result = {"ok": False, "error": f"playwright_timeout: {exc}"}
    except Exception as exc:
        result = {"ok": False, "error": repr(exc)}

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
