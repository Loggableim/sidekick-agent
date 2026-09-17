"""Probe: verify tags/related-skill chips + sort modes in the Skills panel.

Usage: python probe_skills_chips.py http://127.0.0.1:9121
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
        page.evaluate("switchPanel('skills', {fromRailClick: true})")
        page.wait_for_timeout(2500)

        # sidekick-agent has related_skills in its frontmatter
        page.evaluate("openSkill('sidekick-agent', null)")
        page.wait_for_timeout(2500)
        results["chips_section"] = page.evaluate(
            "!!document.querySelector('#skillDetailBody .skill-chips-section')"
        )
        results["related_chips"] = page.evaluate(
            "document.querySelectorAll('#skillDetailBody .skill-chip[data-skill-related]').length"
        )
        results["tag_chips"] = page.evaluate(
            "document.querySelectorAll('#skillDetailBody .skill-chip[data-skill-tag]').length"
        )

        # Click a related chip -> should open that skill
        chip = page.query_selector("#skillDetailBody .skill-chip[data-skill-related]")
        if chip:
            target = chip.get_attribute("data-skill-related")
            chip.click()
            page.wait_for_timeout(2500)
            results["chip_click_target"] = target
            results["chip_click_opened"] = page.evaluate(
                "(document.getElementById('skillDetailTitle')||{}).textContent || ''"
            )

        # Sort modes: switch to "most used" and verify the first item changes
        page.evaluate("switchPanel('skills', {fromRailClick: true})")
        page.wait_for_timeout(1200)
        first_by_name = page.evaluate(
            "(document.querySelector('#skillsList .skill-item .skill-name')||{}).textContent || ''"
        )
        page.select_option("#skillsSort", "most")
        page.wait_for_timeout(1200)
        first_by_most = page.evaluate(
            "(document.querySelector('#skillsList .skill-item .skill-name')||{}).textContent || ''"
        )
        results["first_by_name"] = first_by_name
        results["first_by_most"] = first_by_most
        results["sort_changes_order"] = first_by_name != first_by_most

        # Search filter still works
        page.fill("#skillsSearch", "sidekick")
        page.wait_for_timeout(1000)
        results["search_filtered_count"] = page.evaluate(
            "document.querySelectorAll('#skillsList .skill-item').length"
        )

        browser.close()

    print(json.dumps(results, indent=2))
    ok = (
        results.get("related_chips", 0) > 0
        and results.get("sort_changes_order")
        and 0 < results.get("search_filtered_count", 0) < 128
    )
    print("\nRESULT:", "PASS" if ok else "GAPS FOUND")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
