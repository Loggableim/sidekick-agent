from __future__ import annotations

from pathlib import Path


FORBIDDEN_PARTS = {
    ".hermes",
    "home",
    "spaces",
    "bewusstsein",
    "datasets",
}

FORBIDDEN_FILENAMES = {
    ".env",
    "auth.json",
    "secrets.json",
    "tokens.json",
    "credentials.json",
}

FORBIDDEN_SUFFIXES = {
    ".pem",
    ".pfx",
    ".p12",
    ".key",
}


def scan_repo(repo_root: Path) -> list[str]:
    findings: list[str] = []
    for path in repo_root.rglob("*"):
        rel = path.relative_to(repo_root)
        rel_parts = {part.lower() for part in rel.parts}
        if rel_parts & FORBIDDEN_PARTS:
            findings.append(str(rel))
            continue
        if path.name.lower() in FORBIDDEN_FILENAMES:
            findings.append(str(rel))
            continue
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            findings.append(str(rel))
    return findings
