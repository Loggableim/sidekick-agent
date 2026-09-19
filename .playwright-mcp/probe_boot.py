"""One-shot probe: replicate the smoke's first wait and capture why it fails."""
import json, sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:9119"
out = {}

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(service_workers="block")
    page = ctx.new_page()
    errors = []
    page.on("console", lambda m: errors.append(f"[{m.type}] {m.text}") if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: errors.append(f"[pageerror] {e}"))

    t0 = page.evaluate("() => performance.timeOrigin")
    page.goto(BASE + "/", wait_until="commit", timeout=30000)

    # Poll for the button every second up to 20s, record when it appears
    appeared_at = None
    for i in range(1, 21):
        try:
            if page.locator('[data-testid="titlebar-space-button"]').is_visible(timeout=500):
                appeared_at = i
                break
        except Exception:
            pass
        page.wait_for_timeout(1000)

    out["button_appeared_after_s"] = appeared_at
    out["console_errors"] = errors[:15]
    out["boot_failed_flag"] = page.evaluate("() => document.querySelector('.boot-error,[data-testid=\"boot-error\"]')?.textContent || null")
    out["login_gate"] = page.evaluate("() => !!document.querySelector('input[type=\"password\"]')")
    out["body_text_head"] = (page.evaluate("() => document.body.innerText") or "")[:400]
    out["btn_html"] = page.evaluate("() => { const b = document.querySelector('[data-testid=\"titlebar-space-button\"]'); return b ? b.outerHTML.slice(0, 300) : null; }")
    page.screenshot(path=".playwright-mcp/probe-boot.png", full_page=False)
    browser.close()

print(json.dumps(out, indent=2, ensure_ascii=False))