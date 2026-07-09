#!/usr/bin/env python3
"""Legacy WebUI HTTP smoke test for Sidekick.

This script exercises the old stdlib web surface that still backs the
compatibility proxy. It is intentionally importable so ``tests/smoke_all.py``
can call ``main()`` without executing on import.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@dataclass
class Result:
    passed: int = 0
    failed: int = 0


def _bootstrap_repo() -> None:
    sys.path.insert(0, REPO)
    from sidekick_app.__main__ import _bootstrap_aliases, _ensure_self_first

    _ensure_self_first()
    _bootstrap_aliases()


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request_json(port: int, method: str, path: str, body: Any | None = None) -> tuple[int, Any]:
    url = f"http://127.0.0.1:{port}{path}"
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            if "application/json" in ctype:
                return resp.status, json.loads(raw.decode("utf-8"))
            return resp.status, raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw.decode("utf-8"))
        except Exception:
            return exc.code, raw.decode("utf-8", errors="replace")
    except Exception as exc:
        return 0, {"error": str(exc)}


def _mark(result: Result, name: str, ok: bool, detail: str = "") -> None:
    if ok:
        result.passed += 1
        print(f"  [OK] {name}")
    else:
        result.failed += 1
        print(f"  [FAIL] {name}")
        if detail:
            print(f"        {detail}")


def run_smoke() -> Result:
    _bootstrap_repo()
    from web.server import create_server

    result = Result()
    port = _find_free_port()
    os.environ["SIDEKICK_WEBUI_HOST"] = "127.0.0.1"
    os.environ["SIDEKICK_WEBUI_PORT"] = str(port)
    os.environ.setdefault("HERMES_WEBUI_LOG_FILE", os.devnull)

    server = create_server("127.0.0.1", port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        code, data = _request_json(port, "GET", "/health")
        _mark(result, "/health", code == 200 and isinstance(data, dict), f"status={code} data={str(data)[:120]}")

        code, _ = _request_json(port, "GET", "/favicon.ico")
        _mark(result, "favicon", code == 200, f"status={code}")

        code, created = _request_json(port, "POST", "/api/session/new", {"title": "smoke-legacy"})
        session = created.get("session", created) if isinstance(created, dict) else {}
        session_id = session.get("session_id") or session.get("id") or ""
        _mark(result, "session create", code in {200, 201} and bool(session_id), f"status={code} id={session_id}")

        if session_id:
            # Match the production session-switch fast path. Full message/model
            # hydration is covered by targeted tests and can legitimately touch
            # slow external model metadata caches on a developer machine.
            code, loaded = _request_json(
                port,
                "GET",
                f"/api/session?session_id={session_id}&messages=0&resolve_model=0",
            )
            session_data = loaded.get("session", {}) if isinstance(loaded, dict) else {}
            _mark(result, "session load", code == 200 and session_data.get("session_id") == session_id, f"status={code}")

            code, browser_state_payload = _request_json(port, "GET", f"/api/browser/state?session_id={session_id}")
            browser_state = browser_state_payload.get("state", {}) if isinstance(browser_state_payload, dict) else {}
            browser_blank = (
                code == 200
                and browser_state.get("session_id") == session_id
                and str(browser_state.get("url") or "").strip() == "about:blank"
                and int(browser_state.get("frame_rev") or 0) == 0
            )
            _mark(result, "browser blank state", browser_blank, f"status={code} state={browser_state}")

            code, listed = _request_json(port, "GET", "/api/sessions")
            _mark(result, "session list", code == 200, f"status={code}")

        code, _ = _request_json(port, "GET", "/api/sessions")
        _mark(result, "sessions endpoint", code == 200, f"status={code}")

        code, _ = _request_json(port, "GET", "/api/sessions/search?q=smoke")
        _mark(result, "sessions search", code == 200, f"status={code}")

        code, _ = _request_json(port, "GET", "/")
        _mark(result, "root html", code == 200, f"status={code}")

        code, _ = _request_json(port, "GET", "/api/logs?file=agent&lines=5")
        _mark(result, "logs endpoint", code == 200, f"status={code}")

        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            print(f"  [SKIP] desktop browser layout smoke (playwright unavailable: {exc})")
        else:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    page = browser.new_page(viewport={"width": 1900, "height": 1100}, device_scale_factor=1)
                    page.goto(f"http://127.0.0.1:{port}/session/{session_id}", wait_until="networkidle", timeout=15000)
                    page.wait_for_timeout(1000)

                    composer = page.locator("#composerBox").first
                    strip = page.locator("#composerStatusStrip").first
                    action_chips = page.locator("#actionChips").first
                    review_chip = page.locator("#actionChips .action-chip").first
                    space_btn = page.locator("#titlebarSpaceBtn").first
                    game_mode_btn = page.locator("#btnGameModeToggle").first
                    profile_chip = page.locator("#profileChip").first
                    composer_workspace_chip = page.locator("#composerWorkspaceChip").first
                    composer_model_chip = page.locator("#composerModelChip").first
                    composer_reasoning_chip = page.locator("#composerReasoningChip").first
                    attach_btn = page.locator("#composerBox #btnAttach").first
                    goal_btn = page.locator("#composerBox #btnGoalModeToggle").first
                    browser_toggle = page.locator("#composerBox #btnBrowserDrawerToggle").first
                    toolsets_chip = page.locator("#composerBox #composerToolsetsChip").first
                    queue_mode_chip = page.locator("#composerBox .composer-mode-chip[data-mode='queue']").first
                    steer_mode_chip = page.locator("#composerBox .composer-mode-chip[data-mode='steer']").first
                    bg_mode_chip = page.locator("#composerBox .composer-mode-chip[data-mode='bg']").first
                    workspace_panel_btn = page.locator("#composerBox #btnWorkspacePanelToggle").first
                    sandbox_toggle_label = page.locator("#composerBox #sandboxToggleLabel").first
                    terminal_btn = page.locator("#composerBox #btnTerminalToggle").first
                    mobile_config_btn = page.locator("#composerBox #composerMobileConfigBtn").first
                    mobile_workspace_action = page.locator("#composerBox #composerMobileWorkspaceAction").first
                    bg_badge = page.locator("#composerBox #bgBadge").first
                    yolo_pill = page.locator("#composerBox #yoloPill").first
                    mic_btn = page.locator("#composerBox #btnMic").first
                    voice_mode_btn = page.locator("#composerBox #btnVoiceMode").first
                    cast_btn = page.locator("#btnCastToggle").first
                    workflow_badge = page.locator("#composerBox #workflowStatusBadge").first
                    queue_card = page.locator("#queueCard").first
                    browser_empty = page.locator("#browserEmptyState").first
                    browser_empty_title = page.locator("#browserEmptyStateTitle").first
                    browser_frame_image = page.locator("#browserFrameImage").first
                    composer_status = page.locator("#composerStatus").first
                    model_chip = page.locator(".composer-model-chip").first
                    reasoning_chip = page.locator(".composer-reasoning-chip").first
                    app_titlebar_title = page.locator("#appTitlebarTitle").first
                    theme_toggle = page.locator("#titlebarThemeToggle").first
                    reboot_btn = page.locator("#btnRebootSidekick").first
                    shutdown_btn = page.locator("#btnShutdownSidekick").first
                    titlebar_hidden = all(
                        (not page.locator(sel).count()) or not page.locator(sel).first.is_visible()
                        for sel in (
                            ".app-titlebar #browserStatusBadge",
                            ".app-titlebar #browserStatusMenuBtn",
                            ".app-titlebar #browserStatusMenu",
                            ".app-titlebar #btnBrowserDrawerToggle",
                            ".app-titlebar #workflowStatusBadge",
                            ".app-titlebar #modelStatusBadge",
                            ".app-titlebar #reasoningModeBadge",
                        )
                    )

                    boxes = {
                        "composer": composer.bounding_box(),
                        "strip": strip.bounding_box(),
                        "action_chips": action_chips.bounding_box(),
                        "review_chip": review_chip.bounding_box(),
                        "space_btn": space_btn.bounding_box(),
                        "game_mode_btn": game_mode_btn.bounding_box(),
                        "profile_chip": profile_chip.bounding_box(),
                        "composer_workspace_chip": composer_workspace_chip.bounding_box(),
                        "composer_model_chip": composer_model_chip.bounding_box(),
                        "composer_reasoning_chip": composer_reasoning_chip.bounding_box(),
                        "browser_toggle": browser_toggle.bounding_box(),
                        "cast_btn": cast_btn.bounding_box(),
                        "workflow_badge": workflow_badge.bounding_box(),
                        "queue_card": queue_card.bounding_box(),
                        "model_chip": model_chip.bounding_box(),
                        "reasoning_chip": reasoning_chip.bounding_box(),
                        "theme_toggle": theme_toggle.bounding_box(),
                        "reboot_btn": reboot_btn.bounding_box(),
                        "shutdown_btn": shutdown_btn.bounding_box(),
                    }
                    browser_empty_box = browser_empty.bounding_box()
                    browser_frame_box = browser_frame_image.bounding_box()
                    app_titlebar_text = app_titlebar_title.text_content() or ""
                    composer_center_x = boxes["composer"]["x"] + (boxes["composer"]["width"] / 2)
                    required_boxes = {
                        name: box
                        for name, box in boxes.items()
                        if name != "composer_workspace_chip"
                    }
                    ok = (
                        titlebar_hidden
                        and all(required_boxes.values())
                        and boxes["action_chips"]["x"] < boxes["browser_toggle"]["x"] < boxes["cast_btn"]["x"] < boxes["workflow_badge"]["x"]
                        and abs(boxes["action_chips"]["y"] - boxes["strip"]["y"]) <= 8
                        and abs(boxes["browser_toggle"]["y"] - boxes["cast_btn"]["y"]) <= 24
                        and abs(boxes["cast_btn"]["y"] - boxes["workflow_badge"]["y"]) <= 24
                        and boxes["action_chips"]["x"] <= boxes["strip"]["x"] + 20
                        and boxes["action_chips"]["x"] < composer_center_x - 150
                        and boxes["reboot_btn"]["x"] < boxes["theme_toggle"]["x"] < boxes["shutdown_btn"]["x"]
                        and abs(boxes["theme_toggle"]["y"] - boxes["reboot_btn"]["y"]) <= 8
                        and boxes["space_btn"]["x"] < boxes["game_mode_btn"]["x"]
                        and abs(boxes["space_btn"]["y"] - boxes["game_mode_btn"]["y"]) <= 8
                        and boxes["model_chip"]["x"] < boxes["reasoning_chip"]["x"]
                        and abs(boxes["model_chip"]["y"] - boxes["reasoning_chip"]["y"]) <= 24
                        and app_titlebar_text.strip()
                        and "Untitled" not in app_titlebar_text
                    )
                    dark_before = page.evaluate("document.documentElement.classList.contains('dark')")
                    composer_tooltips = {
                        "review_title": review_chip.get_attribute("title"),
                        "profile_title": profile_chip.get_attribute("title"),
                        "workspace_title": composer_workspace_chip.get_attribute("title"),
                        "model_title": composer_model_chip.get_attribute("title"),
                        "reasoning_title": composer_reasoning_chip.get_attribute("title"),
                        "workflow_title": workflow_badge.get_attribute("title"),
                        "attach_title": attach_btn.get_attribute("title"),
                        "attach_tooltip": attach_btn.get_attribute("data-tooltip"),
                        "goal_title": goal_btn.get_attribute("title"),
                        "goal_tooltip": goal_btn.get_attribute("data-tooltip"),
                        "browser_title": browser_toggle.get_attribute("title"),
                        "browser_tooltip": browser_toggle.get_attribute("data-tooltip"),
                        "toolsets_title": toolsets_chip.get_attribute("title"),
                        "queue_mode_title": queue_mode_chip.get_attribute("title"),
                        "steer_mode_title": steer_mode_chip.get_attribute("title"),
                        "bg_mode_title": bg_mode_chip.get_attribute("title"),
                        "workspace_panel_title": workspace_panel_btn.get_attribute("title"),
                        "sandbox_title": sandbox_toggle_label.get_attribute("title"),
                        "sandbox_tooltip": sandbox_toggle_label.get_attribute("data-tooltip"),
                        "terminal_title": terminal_btn.get_attribute("title"),
                        "terminal_tooltip": terminal_btn.get_attribute("data-tooltip"),
                        "mobile_config_title": mobile_config_btn.get_attribute("title"),
                        "mobile_workspace_title": mobile_workspace_action.get_attribute("title"),
                        "bg_badge_title": bg_badge.get_attribute("title"),
                        "yolo_title": yolo_pill.get_attribute("title"),
                        "mic_title": mic_btn.get_attribute("title"),
                        "mic_tooltip": mic_btn.get_attribute("data-tooltip"),
                        "voice_title": voice_mode_btn.get_attribute("title"),
                        "voice_tooltip": voice_mode_btn.get_attribute("data-tooltip"),
                    }
                    topbar_tooltips = {
                        "space_title": space_btn.get_attribute("title"),
                        "space_tooltip": space_btn.get_attribute("data-tooltip"),
                        "game_title": game_mode_btn.get_attribute("title"),
                        "game_tooltip": game_mode_btn.get_attribute("data-tooltip"),
                        "cast_title": cast_btn.get_attribute("title"),
                        "cast_tooltip": cast_btn.get_attribute("data-tooltip"),
                        "theme_title": theme_toggle.get_attribute("title"),
                        "theme_tooltip": theme_toggle.get_attribute("data-tooltip"),
                        "reboot_title": reboot_btn.get_attribute("title"),
                        "reboot_tooltip": reboot_btn.get_attribute("data-tooltip"),
                        "shutdown_title": shutdown_btn.get_attribute("title"),
                        "shutdown_tooltip": shutdown_btn.get_attribute("data-tooltip"),
                    }
                    workflow_before = workflow_badge.get_attribute("aria-label") or ""
                    # The drawer opens by shifting an overlay into the pointer
                    # path, which makes a strict Playwright pointer click flaky.
                    # Trigger the same DOM click path without the actionability
                    # race so this smoke still exercises the real toggle handler.
                    browser_toggle.evaluate("el => el.click()")
                    page.wait_for_timeout(250)
                    workflow_open = workflow_badge.get_attribute("aria-label") or ""
                    browser_empty_visible = browser_empty.is_visible()
                    browser_frame_visible = browser_frame_image.is_visible()
                    browser_empty_title_text = browser_empty_title.text_content() if browser_empty_visible else ""
                    browser_toggle.evaluate("el => el.click()")
                    page.wait_for_timeout(250)
                    workflow_restored = workflow_badge.get_attribute("aria-label") or ""
                    theme_toggle.click()
                    page.wait_for_timeout(250)
                    dark_after = page.evaluate("document.documentElement.classList.contains('dark')")
                    theme_toggle.click()
                    page.wait_for_timeout(250)
                    dark_restored = page.evaluate("document.documentElement.classList.contains('dark')")
                    page.evaluate(
                        """() => {
                          S.busy = true;
                          if (typeof setComposerStatus === 'function') setComposerStatus('');
                          if (typeof updateSendBtn === 'function') updateSendBtn();
                        }"""
                    )
                    page.wait_for_timeout(100)
                    busy_visible = composer_status.is_visible()
                    busy_text = composer_status.text_content() or ""
                    busy_loading = composer_status.get_attribute("class") or ""
                    page.evaluate(
                        """() => {
                          S.busy = false;
                          if (typeof setComposerStatus === 'function') setComposerStatus('');
                          if (typeof updateSendBtn === 'function') updateSendBtn();
                        }"""
                    )
                    page.wait_for_timeout(100)
                    busy_hidden = not composer_status.is_visible()
                    queue_visible = queue_card.is_visible()
                    queue_box = boxes["queue_card"]
                    queue_empty_hidden = (queue_box is None) or (queue_box["height"] <= 1 and not queue_visible)
                    browser_empty_ok = (
                        browser_empty_visible
                        and not browser_frame_visible
                        and "Browser attached" in (browser_empty_title_text or "")
                    )
                    detail = (
                        f"titlebar_hidden={titlebar_hidden} "
                        f"composer={boxes['composer']} strip={boxes['strip']} "
                        f"action={boxes['action_chips']} composer_center={composer_center_x:.1f} "
                        f"space={boxes['space_btn']} game={boxes['game_mode_btn']} "
                        f"review_chip={boxes['review_chip']} profile_chip={boxes['profile_chip']} "
                        f"workspace_chip={boxes['composer_workspace_chip']} model_chip={boxes['composer_model_chip']} reasoning_chip={boxes['composer_reasoning_chip']} "
                        f"browser={boxes['browser_toggle']} cast={boxes['cast_btn']} workflow={boxes['workflow_badge']} "
                        f"browser_empty={browser_empty_box} browser_frame={browser_frame_box} "
                        f"browser_empty_visible={browser_empty_visible} browser_frame_visible={browser_frame_visible} browser_empty_title={browser_empty_title_text!r} "
                        f"titlebar_text={app_titlebar_text!r} "
                        f"queue={queue_box} queue_visible={queue_visible} "
                        f"composer_tooltips={composer_tooltips} "
                        f"topbar_tooltips={topbar_tooltips} "
                        f"composer_status={busy_text!r} visible={busy_visible} hidden_after={busy_hidden} classes={busy_loading!r} "
                        f"workflow_before={workflow_before!r} workflow_open={workflow_open!r} workflow_restored={workflow_restored!r} "
                        f"theme={boxes['theme_toggle']} reboot={boxes['reboot_btn']} shutdown={boxes['shutdown_btn']} model={boxes['model_chip']} "
                        f"reasoning={boxes['reasoning_chip']} "
                        f"theme_before={dark_before} theme_after={dark_after} theme_restored={dark_restored}"
                    )
                    topbar_clean = all(v is None for v in topbar_tooltips.values())
                    composer_clean = all(v is None for v in composer_tooltips.values())
                    _mark(result, "desktop browser layout", ok and topbar_clean and composer_clean and "browser closed" in workflow_before.lower() and "browser open" in workflow_open.lower() and "browser closed" in workflow_restored.lower() and queue_empty_hidden and browser_empty_ok and busy_visible and busy_hidden and bool(busy_text.strip()) and "is-loading" in busy_loading, detail)
                    _mark(result, "theme toggle restores state", dark_after != dark_before and dark_restored == dark_before, f"before={dark_before} after={dark_after} restored={dark_restored}")
                finally:
                    browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    return result


def main() -> int:
    print("=== Legacy WebUI smoke ===\n")
    result = run_smoke()
    print(f"\n=== Ergebnis: {result.passed} passed, {result.failed} failed ===")
    return 0 if result.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
