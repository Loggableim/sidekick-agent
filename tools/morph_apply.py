"""Morph Fast Apply tool — merge edit snippets into files at 10,500 tok/s.

Uses Morph's Fast Apply API (OpenAI-compatible) to merge partial edit snippets
into full files. The agent provides a lazy edit snippet with ``// ... existing code ...``
markers, and Morph returns the fully merged file.

Requires ``MORPH_API_KEY`` environment variable.
"""

import json
import logging
import os
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
    """Execute a Morph Fast Apply edit."""
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

        # Write merged result
        path.write_text(merged_code, encoding="utf-8")
        return json.dumps({
            "success": True,
            "file": str(path),
            "message": f"Applied edit to {path.name}",
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
