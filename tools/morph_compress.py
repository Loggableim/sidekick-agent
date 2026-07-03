"""Morph Compactor tool — compress large contexts at 33,000 tok/s.

Uses Morph's Compactor model to compress conversation history, codebase context,
or any large text while preserving critical information. Supports ``<keepContext>``
tags to mark sections that must survive compression unchanged.

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

MORPH_COMPRESS_SCHEMA = {
    "name": "morph_compress",
    "description": (
        "Compress a large text (conversation, codebase context, logs) "
        "using Morph's Compactor model at 33,000 tok/s. "
        "Use <keepContext> tags around sections that must survive compression unchanged. "
        "Provide a query to focus compression on what's relevant for the next step. "
        "Returns the compressed text, original size, compressed size, and ratio."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The text to compress. Can include <keepContext>...</keepContext> tags for sections that must survive unchanged.",
            },
            "query": {
                "type": "string",
                "description": "What the compressed text will be used for. Guides what information to preserve (e.g. 'What is relevant for debugging the auth middleware?')",
            },
        },
        "required": ["text", "query"],
    },
}


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def _morph_compress_handler(args: dict, **kw) -> str:
    """Execute a Morph Compactor compression."""
    text = args.get("text", "")
    query = args.get("query", "")

    if not text or not query:
        return json.dumps({"error": "text and query are required"})

    api_key = _get_morph_api_key()
    if not api_key:
        return json.dumps({"error": "MORPH_API_KEY not set. Set it in your environment or config.yaml."})

    # Skip compression for small texts — not worth the API call
    if len(text) < 2000:
        return json.dumps({
            "compressed": text,
            "original_size": len(text),
            "compressed_size": len(text),
            "ratio": 100.0,
            "skipped": True,
            "message": "Text too small for compression (< 2000 chars). Returned as-is.",
        })

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url="https://api.morphllm.com/v1")

        prompt = (
            f"<query>{query}</query>\n"
            f"<input>\n{text}\n</input>\n\n"
            "Compress the input text to its essential information. "
            "Preserve all content inside <keepContext> tags verbatim. "
            "Remove redundancy, filler words, and irrelevant details. "
            "Keep technical details (numbers, paths, commands, error messages, code snippets) intact. "
            "Return only the compressed text, no explanations."
        )

        response = client.chat.completions.create(
            model="morph-compactor",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=4096,
        )

        compressed = response.choices[0].message.content
        if not compressed:
            return json.dumps({"error": "Morph Compactor returned empty response"})

        original_size = len(text)
        compressed_size = len(compressed)
        ratio = round((compressed_size / original_size) * 100, 1) if original_size > 0 else 100.0

        return json.dumps({
            "compressed": compressed,
            "original_size": original_size,
            "compressed_size": compressed_size,
            "ratio": ratio,
            "savings": f"{original_size - compressed_size} chars ({100 - ratio}%)",
        })

    except Exception as e:
        logger.exception("Morph Compactor failed")
        return json.dumps({"error": f"Morph Compactor failed: {e}"})


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

from tools.registry import registry


def _morph_available() -> bool:
    """Runtime check: Morph tools are available if we can resolve an API key."""
    return _get_morph_api_key() is not None


registry.register(
    name="morph_compress",
    toolset="morph",
    schema=MORPH_COMPRESS_SCHEMA,
    handler=_morph_compress_handler,
    check_fn=_morph_available,
    emoji="🗜️",
)
