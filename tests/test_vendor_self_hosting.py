"""Tests for the self-hosted vendor assets (backlog item 14).

index.html loaded 9 assets from cdn.jsdelivr.net (Prism, xterm, KaTeX) and the
runtime loaders in ui.js/terminal.js/boot.js fetched more (js-yaml, pdfjs,
mermaid, Prism themes). A CDN outage killed terminal, highlighting and math.

Everything now lives under web/static/vendor/ and is served by the dashboard.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "web" / "static"
INDEX_HTML = STATIC / "index.html"

VENDOR_FILES = (
    "vendor/katex/katex.min.css",
    "vendor/katex/katex.min.js",
    "vendor/prism/prism-core.min.js",
    "vendor/prism/prism-autoloader.min.js",
    "vendor/prism/prism-line-numbers.min.js",
    "vendor/prism/prism-tomorrow.min.css",
    "vendor/prism/prism-one-dark.min.css",
    "vendor/prism/prism-ghcolors.min.css",
    "vendor/prism/prism.min.css",
    "vendor/xterm/xterm.js",
    "vendor/xterm/xterm-addon-fit.js",
    "vendor/xterm/xterm-addon-web-links.js",
    "vendor/js-yaml/js-yaml.min.js",
    "vendor/mermaid/mermaid.min.js",
    "vendor/pdfjs/pdf.min.mjs",
    "vendor/pdfjs/pdf.worker.min.mjs",
)

# Files that must not reference the CDN anymore.
RUNTIME_SCRIPTS = ("index.html", "ui.js", "terminal.js", "boot.js")


def test_vendor_assets_exist_and_are_not_empty() -> None:
    for rel in VENDOR_FILES:
        path = STATIC / rel
        assert path.is_file(), f"missing vendor asset: {rel}"
        assert path.stat().st_size > 0, f"empty vendor asset: {rel}"


def test_katex_fonts_are_present() -> None:
    """katex.min.css references 60 font files relative to fonts/."""
    css = (STATIC / "vendor/katex/katex.min.css").read_text(encoding="utf-8")
    fonts = sorted(set(re.findall(r"url\(fonts/([^)]+)\)", css)))
    assert fonts, "no font references found in katex.min.css"

    missing = [f for f in fonts if not (STATIC / "vendor/katex/fonts" / f).is_file()]
    assert not missing, f"missing KaTeX fonts: {missing[:5]}"


def test_prism_components_are_present() -> None:
    """The autoloader resolves languages from its own directory."""
    components = STATIC / "vendor/prism/components"
    assert components.is_dir(), "prism components directory missing"
    files = list(components.glob("prism-*.min.js"))
    assert len(files) >= 30, f"too few prism languages vendored: {len(files)}"
    # The languages the app's own output uses must be there.
    for lang in ("python", "javascript", "bash", "json", "diff"):
        assert (components / f"prism-{lang}.min.js").is_file(), f"missing {lang}"


def test_no_cdn_reference_remains_in_runtime_scripts() -> None:
    """Comments may mention the CDN; real references must be gone."""
    for name in RUNTIME_SCRIPTS:
        source = (STATIC / name).read_text(encoding="utf-8")
        in_comment = False
        offenders = []
        for line in source.splitlines():
            stripped = line.strip()
            if in_comment:
                if "-->" in stripped or "*/" in stripped:
                    in_comment = False
                continue
            if stripped.startswith(("//", "/*")):
                continue
            if stripped.startswith("<!--"):
                if "-->" not in stripped:
                    in_comment = True
                continue
            if "cdn.jsdelivr.net" in stripped:
                offenders.append(stripped)
        assert not offenders, f"{name} still references the CDN: {offenders}"


def test_index_uses_local_vendor_paths() -> None:
    index_html = INDEX_HTML.read_text(encoding="utf-8")
    for rel in (
        "vendor/katex/katex.min.css",
        "vendor/prism/prism-core.min.js",
        "vendor/prism/prism-autoloader.min.js",
        "vendor/xterm/xterm.js",
    ):
        assert f"/static/{rel}" in index_html, f"index.html does not use {rel}"


def test_preconnect_hint_is_gone() -> None:
    """The hint existed only to warm the CDN connection."""
    index_html = INDEX_HTML.read_text(encoding="utf-8")
    assert 'rel="preconnect" href="https://cdn.jsdelivr.net"' not in index_html
    assert 'rel="dns-prefetch" href="https://cdn.jsdelivr.net"' not in index_html
