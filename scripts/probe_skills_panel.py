"""Headless probe: verify the Skills panel renders with the new features.

Checks (against a running dashboard):
  1. Panel opens and lists skills (not empty)
  2. Sort dropdown + disabled filter exist
  3. Usage badges render on items
  4. Detail view shows action row (pin/disable/use-in-chat)
  5. Insights panel shows the skill usage card

Usage: python probe_skills_panel.py http://127.0.0.1:9121
"""
import json
import sys

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:9121"


def main() -> int:
    results = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(BASE, wait_until="commit")
        page.wait_for_selector("#skillsList", state="attached", timeout=30000)
        page.wait_for_timeout(1500)

        # Open the skills panel via the rail button
        page.evaluate("switchPanel('skills', {fromRailClick: true})")
        page.wait_for_timeout(2500)

        results["panel_visible"] = page.evaluate(
            "!!document.querySelector('#panelSkills') && getComputedStyle(document.querySelector('#panelSkills')).display !== 'none'"
        )
        results["skill_items"] = page.evaluate(
            "document.querySelectorAll('#skillsList .skill-item').length"
        )
        results["categories"] = page.evaluate(
            "document.querySelectorAll('#skillsList .skills-category').length"
        )
        results["sort_select"] = page.evaluate("!!document.getElementById('skillsSort')")
        results["disabled_filter"] = page.evaluate(
            "!!document.getElementById('skillsShowDisabled')"
        )
        results["usage_badges"] = page.evaluate(
            "document.querySelectorAll('#skillsList .skill-usage-badge').length"
        )
        results["state_badges"] = page.evaluate(
            "document.querySelectorAll('#skillsList .skill-badge').length"
        )
        results["refresh_btn"] = page.evaluate(
            "!!document.querySelector('#panelSkills .panel-head-actions button[onclick*=\"loadSkills(true)\"]')"
        )

        # Open a skill detail and check the action row
        first = page.query_selector("#skillsList .skill-item")
        if first:
            first.click()
            page.wait_for_timeout(2000)
            results["detail_action_btns"] = page.evaluate(
                "document.querySelectorAll('#skillDetailBody .skill-action-btn').length"
            )
            results["detail_info_box"] = page.evaluate(
                "!!document.querySelector('#skillDetailBody .skill-info-box')"
            )
            results["detail_title"] = page.evaluate(
                "(document.getElementById('skillDetailTitle')||{}).textContent || ''"
            )

        # Insights panel: skill usage card
        page.evaluate("switchPanel('insights')")
        page.wait_for_timeout(4000)
        results["insights_skill_card"] = page.evaluate(
            "Array.from(document.querySelectorAll('.insights-card-title')).some(e => /skill/i.test(e.textContent||''))"
        )

        # Console errors
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.wait_for_timeout(500)
        results["console_errors"] = errors

        browser.close()

    print(json.dumps(results, indent=2))
    ok = (
        results.get("panel_visible")
        and results.get("skill_items", 0) > 0
        and results.get("sort_select")
        and results.get("disabled_filter")
        and results.get("detail_action_btns", 0) >= 2
        and results.get("insights_skill_card")
    )
    print("\nRESULT:", "PASS" if ok else "GAPS FOUND")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
