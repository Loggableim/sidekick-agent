"""
sidekick_memory.py — Sidekick's vector memory tool.

Wraps the nova-space vector_memory.py (ChromaDB + sentence-transformers)
as a Sidekick tool so I can store/recall semantically from any context.

Usage from agent:
  sidekick_memory_store(query="...", content="...", tags="credential,api_key")
  sidekick_memory_recall(query="discord bot token")
  sidekick_memory_recent(n=5)
"""

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Path to the nova space vector_memory.py script
NOVA_SPACE = Path(os.path.dirname(os.path.abspath(__file__))).parent / "home" / "spaces" / "nova"
VECTOR_MEMORY_SCRIPT = NOVA_SPACE / "vector_memory.py"

# Check if the script exists (fallback for dev environments)
if not VECTOR_MEMORY_SCRIPT.exists():
    VECTOR_MEMORY_SCRIPT = None


def _run_vector_memory(args: list) -> dict:
    """Run vector_memory.py CLI with given args, return parsed JSON."""
    if VECTOR_MEMORY_SCRIPT is None:
        return {"error": "vector_memory.py not found", "success": False}
    
    cmd = [sys.executable, str(VECTOR_MEMORY_SCRIPT)] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=30,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        if result.returncode != 0:
            logger.warning("vector_memory stderr: %s", result.stderr[:500])
            return {"error": result.stderr[:500], "success": False}
        
        # Parse JSON from stdout (last line that starts with {)
        for line in reversed(result.stdout.strip().split("\n")):
            line = line.strip()
            if line.startswith("{"):
                data = json.loads(line)
                data["success"] = True
                return data
        
        return {"error": "No JSON in output", "raw": result.stdout[:500], "success": False}
    except subprocess.TimeoutExpired:
        return {"error": "vector_memory timed out after 30s", "success": False}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "success": False}


def sidekick_memory_store(
    query: str,
    thinking: str = "",
    response: str = "",
    tags: str = "",
) -> str:
    """
    Store an entry in Sidekick's vector memory.
    
    Args:
        query: The user's question/input that triggered this
        thinking: My internal thoughts about it
        response: What I responded (or the fact being stored)
        tags: Comma-separated tags: credential,api_key,decision,learning,config,project,preference
    
    Returns:
        JSON string with result
    """
    cmd = ["store", "--query", query]
    if thinking:
        cmd += ["--thinking", thinking]
    if response:
        cmd += ["--response", response]
    if tags:
        cmd += ["--tags", tags]
    
    result = _run_vector_memory(cmd)
    return json.dumps(result, ensure_ascii=False)


def sidekick_memory_recall(query: str, n: int = 3) -> str:
    """
    Search vector memory by semantic similarity.
    
    Args:
        query: What to search for (e.g., "discord bot token", "deployment settings")
        n: Max results (default: 3)
    
    Returns:
        JSON string with matching memories
    """
    cmd = ["recall", "--query", query, "--n", str(n)]
    result = _run_vector_memory(cmd)
    return json.dumps(result, ensure_ascii=False)


def sidekick_memory_recent(n: int = 5, tags: str = "") -> str:
    """
    Get most recent entries from vector memory.
    
    Args:
        n: Number of recent entries (default: 5)
        tags: Optional comma-separated tag filter
    
    Returns:
        JSON string with recent memories
    """
    cmd = ["recent", "--n", str(n)]
    if tags:
        cmd += ["--tags", tags]
    result = _run_vector_memory(cmd)
    return json.dumps(result, ensure_ascii=False)


def sidekick_memory_status() -> str:
    """Get vector memory status (count, db size, last entry)."""
    result = _run_vector_memory(["status"])
    return json.dumps(result, ensure_ascii=False)


def check_requirements() -> bool:
    """Vector memory is available when the nova space script exists."""
    return VECTOR_MEMORY_SCRIPT is not None


# =============================================================================
# OpenAI Function-Calling Schemas
# =============================================================================

SIDEKICK_MEMORY_STORE_SCHEMA = {
    "name": "sidekick_memory_store",
    "description": (
        "Store a fact, credential, decision, or learning in Sidekick's vector memory. "
        "Use this for anything you want to remember semantically: API keys, credentials, "
        "project decisions, user preferences, environment facts. "
        "Tags help categorize: use 'credential' for keys, 'decision' for architectural choices, "
        "'config' for settings, 'project' for project-specific facts, 'learning' for lessons. "
        "The memory persists across sessions and is searchable by meaning."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What triggered this memory (e.g., 'User mentioned Discord token', 'Setup project X'). Required."
            },
            "thinking": {
                "type": "string",
                "description": "Internal context or reasoning about why this matters."
            },
            "response": {
                "type": "string",
                "description": "The actual content to remember: API key, config value, decision description."
            },
            "tags": {
                "type": "string",
                "description": "Comma-separated tags: credential,api_key,decision,learning,config,project,preference,env,workflow"
            },
        },
        "required": ["query", "response"],
    },
}

SIDEKICK_MEMORY_RECALL_SCHEMA = {
    "name": "sidekick_memory_recall",
    "description": (
        "Search Sidekick's vector memory by meaning. Use this when you need to recall "
        "something you learned or stored before: API keys, config values, project decisions, "
        "user preferences. Works like semantic search — 'discord bot setup' finds entries "
        "about Discord tokens even if you don't remember the exact name."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to search for, in natural language."
            },
            "n": {
                "type": "integer",
                "description": "Max results (default: 3, max: 10).",
                "default": 3,
            },
        },
        "required": ["query"],
    },
}

SIDEKICK_MEMORY_RECENT_SCHEMA = {
    "name": "sidekick_memory_recent",
    "description": (
        "Get the most recent entries from Sidekick's vector memory. "
        "Optionally filter by tags — 'credential' shows recent credentials, "
        "'decision' shows recent decisions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "n": {
                "type": "integer",
                "description": "Number of recent entries (default: 5, max: 20).",
                "default": 5,
            },
            "tags": {
                "type": "string",
                "description": "Optional tag filter (comma-separated). Only entries matching any tag are returned."
            },
        },
    },
}

SIDEKICK_MEMORY_STATUS_SCHEMA = {
    "name": "sidekick_memory_status",
    "description": "Check Sidekick's vector memory status — total entries, database size, storage health.",
    "parameters": {
        "type": "object",
        "properties": {},
    },
}


# =============================================================================
# Registry
# =============================================================================
from tools.registry import registry

registry.register(
    name="sidekick_memory_store",
    toolset="memory",
    schema=SIDEKICK_MEMORY_STORE_SCHEMA,
    handler=lambda args, **kw: sidekick_memory_store(
        query=args.get("query", ""),
        thinking=args.get("thinking", ""),
        response=args.get("response", ""),
        tags=args.get("tags", ""),
    ),
    check_fn=check_requirements,
    emoji="🧠",
)

registry.register(
    name="sidekick_memory_recall",
    toolset="memory",
    schema=SIDEKICK_MEMORY_RECALL_SCHEMA,
    handler=lambda args, **kw: sidekick_memory_recall(
        query=args.get("query", ""),
        n=args.get("n", 3),
    ),
    check_fn=check_requirements,
    emoji="🔍",
)

registry.register(
    name="sidekick_memory_recent",
    toolset="memory",
    schema=SIDEKICK_MEMORY_RECENT_SCHEMA,
    handler=lambda args, **kw: sidekick_memory_recent(
        n=args.get("n", 5),
        tags=args.get("tags", ""),
    ),
    check_fn=check_requirements,
    emoji="📋",
)

registry.register(
    name="sidekick_memory_status",
    toolset="memory",
    schema=SIDEKICK_MEMORY_STATUS_SCHEMA,
    handler=lambda args, **kw: sidekick_memory_status(),
    check_fn=check_requirements,
    emoji="📊",
)