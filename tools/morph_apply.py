"""Morph Fast Apply tool — merge edit snippets into files at 10,500 tok/s.

Uses Morph's Fast Apply API (OpenAI-compatible) to merge partial edit snippets
into full files. The agent provides a lazy edit snippet with ``// ... existing code ...``
markers, and Morph returns the fully merged file.

Requires ``MORPH_API_KEY`` environment variable.
"""

import difflib
import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def _get_morph_api_key() -> str | None:
    """Resolve MORPH_API_KEY: env var first, then auth.json credential pool."""
    key = os.environ.get("MORPH_API_KEY")
    if key:
        return key
    try:
        from cli.config import get_sidekick_home
        auth = json.loads((get_sidekick_home() / "auth.json").read_text())
        pool = auth.get("credential_pool", {}).get("morph", [])
        if pool:
            return pool[0].get("access_token") or None
    except Exception:
        pass
    return None


def _syntax_check(path: Path) -> str | None:
    """Run a syntax check on the file. Returns error message or None if OK."""
    if path.suffix == ".py":
        try:
            result = subprocess.run(
                [sys.executable, "-m", "py_compile", str(path)],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return result.stderr.strip() or result.stdout.strip() or "Syntax error"
        except subprocess.TimeoutExpired:
            return "Syntax check timed out"
        except FileNotFoundError:
            return "Python interpreter not found"
    elif path.suffix in (".json", ".yaml", ".yml", ".toml"):
        # Basic structural check via compile/parse
        try:
            if path.suffix == ".json":
                json.loads(path.read_text())
            elif path.suffix in (".yaml", ".yml"):
                import yaml
                yaml.safe_load(path.read_text())
            elif path.suffix == ".toml":
                import tomllib
                tomllib.loads(path.read_text())
        except (json.JSONDecodeError, ValueError, Exception) as e:
            return str(e)
    return None


def _compute_diff(original: str, merged: str, file_path: str) -> str:
    """Generate a unified diff between original and merged content."""
    diff = difflib.unified_diff(
        original.splitlines(keepends=True),
        merged.splitlines(keepends=True),
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
    )
    return "".join(diff)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

MORPH_APPLY_SCHEMA = {
    "name": "morph_apply",
    "description": (
        "Edit a file by specifying only the changed lines. "
        "Use // ... existing code ... markers for unchanged sections. "
        "The tool reads the original file, sends it to Morph's Fast Apply API, "
        "and writes the merged result back. "
        "Creates a .bak backup before editing and validates syntax after. "
        "Returns a diff of changes. "
        "Faster and more accurate than patch for complex edits."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "target_file": {
                "type": "string",
                "description": "Absolute path of the file to edit",
            },
            "code_edit": {
                "type": "string",
                "description": (
                    "The code changes to apply. Show only the lines that change. "
                    "Use // ... existing code ... (or # ... existing code ... for Python) "
                    "to represent unchanged sections. "
                    "Example for Python:\n"
                    "def divide(a, b):\n"
                    "    if b == 0:\n"
                    "        raise ValueError('Cannot divide by zero')\n"
                    "    # ... existing code ..."
                ),
            },
            "instructions": {
                "type": "string",
                "description": "Brief description of what the edit does (e.g. 'Add error handling for division by zero')",
            },
        },
        "required": ["target_file", "code_edit", "instructions"],
    },
}


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def _morph_apply_handler(args: dict, **kw) -> str:
    """Execute a Morph Fast Apply edit with backup, validation, and diff."""
    target_file = args.get("target_file", "")
    code_edit = args.get("code_edit", "")
    instructions = args.get("instructions", "")

    if not target_file or not code_edit:
        return json.dumps({"error": "target_file and code_edit are required"})

    api_key = _get_morph_api_key()
    if not api_key:
        return json.dumps({"error": "MORPH_API_KEY not set. Set it in your environment or config.yaml."})

    # Resolve path
    path = Path(target_file).expanduser().resolve()
    if not path.exists():
        return json.dumps({"error": f"File not found: {path}"})

    try:
        original_code = path.read_text(encoding="utf-8")
    except Exception as e:
        return json.dumps({"error": f"Failed to read {path}: {e}"})

    # Call Morph Fast Apply API
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url="https://api.morphllm.com/v1")

        response = client.chat.completions.create(
            model="morph-v3-fast",
            messages=[{
                "role": "user",
                "content": (
                    f"<instruction>{instructions}</instruction>\n"
                    f"<code>{original_code}</code>\n"
                    f"<update>{code_edit}</update>"
                ),
            }],
        )

        merged_code = response.choices[0].message.content
        if not merged_code:
            return json.dumps({"error": "Morph returned empty response"})

        # ── Validation 1: Non-empty output ──
        if len(merged_code.strip()) < 10:
            return json.dumps({"error": f"Morph returned suspiciously short output ({len(merged_code.strip())} chars). Aborting."})

        # ── Validation 2: Not identical to input (no change made) ──
        if merged_code == original_code:
            return json.dumps({"warning": "Morph returned identical content — no changes were applied.", "diff": ""})

        # ── Backup ──
        backup_path = path.with_suffix(path.suffix + ".bak")
        try:
            path.rename(backup_path)
        except Exception as e:
            return json.dumps({"error": f"Failed to create backup: {e}"})

        # ── Write merged result ──
        try:
            path.write_text(merged_code, encoding="utf-8")
        except Exception as e:
            # Restore backup on write failure
            backup_path.rename(path)
            return json.dumps({"error": f"Failed to write merged file: {e}. Backup restored."})

        # ── Validation 3: Syntax check ──
        syntax_error = _syntax_check(path)
        if syntax_error:
            # Rollback: restore backup
            path.write_text(original_code, encoding="utf-8")
            backup_path.rename(path)
            return json.dumps({
                "error": f"Syntax check failed after merge. Changes rolled back.\nError: {syntax_error}",
                "backup_restored": True,
            })

        # ── Compute diff ──
        diff = _compute_diff(original_code, merged_code, path.name)

        # Count changes
        added = sum(1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---"))

        # Clean up backup on success
        try:
            backup_path.unlink()
        except Exception:
            pass

        return json.dumps({
            "success": True,
            "file": str(path),
            "message": f"Applied edit to {path.name}",
            "changes": {
                "added": added,
                "removed": removed,
                "total_lines": len(merged_code.splitlines()),
            },
            "diff": diff,
        })

    except Exception as e:
        logger.exception("Morph Fast Apply failed")
        return json.dumps({"error": f"Morph Fast Apply failed: {e}"})


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

from tools.registry import registry

def _morph_available() -> bool:
    """Runtime check: Morph tools are available if we can resolve an API key."""
    return _get_morph_api_key() is not None


registry.register(
    name="morph_apply",
    toolset="morph",
    schema=MORPH_APPLY_SCHEMA,
    handler=_morph_apply_handler,
    check_fn=_morph_available,
    emoji="⚡",
)
