"""Morph WarpGrep tool — semantic code search subagent.

Uses Morph's WarpGrep model (morph-warp-grep-v2.1) to search codebases
via a multi-turn tool-calling protocol. The model has built-in tools
(grep_search, read, list_directory, glob, finish) — no tools array needed.

Requires ``MORPH_API_KEY`` and ``ripgrep`` (rg) installed on PATH.
"""

import json
import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────────────────────

MODEL = "morph-warp-grep-v2.1"
MAX_TURNS = 6
MAX_GREP_LINES = 200
MAX_READ_LINES = 800
MAX_CONTEXT_CHARS = 540_000

# ── Schema ──────────────────────────────────────────────────────────────────

MORPH_WARP_GREP_SCHEMA = {
    "name": "morph_codebase_search",
    "description": (
        "Search the codebase for code relevant to a natural language query. "
        "Uses Morph's WarpGrep subagent which explores the repository in ~6 seconds "
        "using ripgrep, file reads, and directory listing. "
        "Returns file paths with their content and line ranges."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language description of what to find (e.g. 'Find where user authentication is implemented')",
            },
            "target_directories": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional: limit search scope to specific subdirectories (relative to repo root)",
            },
        },
        "required": ["query"],
    },
}


# ── Tool Executors ──────────────────────────────────────────────────────────


def _resolve_path(root: str, path: str) -> str:
    """Resolve a path relative to the repo root, stripping leading slashes."""
    path = path.lstrip("/")
    return str(Path(root) / path)


def run_grep(root: str, pattern: str, path: str = ".", glob: str | None = None) -> str:
    """Run ripgrep and return formatted output."""
    search_path = _resolve_path(root, path)
    cmd = ["rg", "--line-number", "--no-heading", "--color", "never", "-C", "1"]
    if glob:
        cmd.extend(["--glob", glob])
    cmd.extend([pattern, search_path])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10, cwd=root)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return f"Error: {e}"
    output = r.stdout.strip()
    if not output:
        return "no matches"
    # Strip absolute root prefix from grep output paths
    root_prefix = root.rstrip("/") + "/"
    output = output.replace(root_prefix, "")
    lines = output.split("\n")
    if len(lines) > MAX_GREP_LINES:
        return "query not specific enough, tool call tried to return too much context and failed"
    return output


def run_read(root: str, path: str, start: int = 1, end: int | None = None) -> str:
    """Read file contents with optional line range."""
    fp = Path(_resolve_path(root, path))
    if not fp.exists():
        return f"Error: file not found: {path}"
    try:
        all_lines = fp.read_text().splitlines()
    except Exception as e:
        return f"Error: {e}"
    if end is None:
        end = len(all_lines)
    selected = all_lines[start - 1 : end]
    out = [f"{start + i}|{line}" for i, line in enumerate(selected)]
    if len(out) > MAX_READ_LINES:
        out = out[:MAX_READ_LINES] + [f"... truncated ({len(all_lines)} total lines)"]
    return "\n".join(out)


def run_list_dir(root: str, path: str, max_depth: int = 3) -> str:
    """List directory tree with paths relative to repo root."""
    dp = Path(_resolve_path(root, path))
    if not dp.exists():
        return f"Error: directory not found: {path}"
    try:
        r = subprocess.run(
            ["find", str(dp), "-maxdepth", str(max_depth),
             "-not", "-path", "*/.git/*",
             "-not", "-path", "*/node_modules/*",
             "-not", "-path", "*/__pycache__/*"],
            capture_output=True, text=True, timeout=5, cwd=root,
        )
        if not r.stdout.strip():
            return "empty directory"
        root_prefix = root.rstrip("/") + "/"
        lines = []
        for line in r.stdout.strip().split("\n"):
            if line.startswith(root_prefix):
                lines.append(line[len(root_prefix):])
            elif line == root.rstrip("/"):
                lines.append(".")
            else:
                lines.append(line)
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


# ── Tool Dispatcher ─────────────────────────────────────────────────────────


def dispatch_tool(name: str, args: dict, repo_root: str) -> str:
    """Execute a tool call and return the output string."""
    if name == "grep_search":
        return run_grep(
            repo_root,
            args["pattern"],
            args.get("path", "."),
            args.get("glob"),
        )
    elif name == "read":
        lines_str = args.get("lines")
        if lines_str:
            ranges = _parse_line_ranges(lines_str)
            if ranges and len(ranges) == 1:
                return run_read(repo_root, args["path"], ranges[0][0], ranges[0][1])
            elif ranges:
                chunks = [run_read(repo_root, args["path"], s, e) for s, e in ranges]
                return "\n...\n".join(chunks)
        return run_read(repo_root, args["path"])
    elif name == "list_directory":
        return run_list_dir(repo_root, args.get("path", args.get("command", ".")))
    elif name == "glob":
        return run_grep(repo_root, "", args.get("path", "."), args["pattern"])
    else:
        return f"Unknown tool: {name}"


# ── Agent Loop ──────────────────────────────────────────────────────────────


def _parse_line_ranges(lines_str: str) -> list[tuple[int, int]]:
    """Parse line range string like '1-50,75-80' into [(1,50),(75,80)]."""
    ranges: list[tuple[int, int]] = []
    for part in lines_str.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            pieces = part.split("-", 1)
            try:
                s, e = int(pieces[0]), int(pieces[1])
                ranges.append((s, e))
            except ValueError:
                continue
        else:
            try:
                n = int(part)
                ranges.append((n, n))
            except ValueError:
                continue
    return ranges


def _resolve_finish(root: str, args: dict) -> list[dict]:
    """Parse finish tool call and read the referenced files."""
    files_raw = args.get("files", "")
    if not files_raw:
        return []

    results: list[dict] = []
    for line in files_raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        colon_idx = line.find(":")
        if colon_idx == -1:
            content = run_read(root, line)
            results.append({"path": line, "content": content})
            continue

        path = line[:colon_idx]
        range_str = line[colon_idx + 1:]

        if range_str.strip() == "*" or not range_str.strip():
            content = run_read(root, path)
            results.append({"path": path, "content": content})
        else:
            ranges = _parse_line_ranges(range_str)
            if ranges:
                chunks = [run_read(root, path, s, e) for s, e in ranges]
                results.append({"path": path, "content": "\n...\n".join(chunks)})
            else:
                content = run_read(root, path)
                results.append({"path": path, "content": content})

    return results


def search(query: str, repo_root: str) -> list[dict]:
    """Run the WarpGrep agent loop. Returns list of {path, content} dicts."""
    api_key = os.environ.get("MORPH_API_KEY")
    if not api_key:
        return [{"error": "MORPH_API_KEY not set"}]

    repo_root = str(Path(repo_root).resolve())

    # Build initial repo structure
    structure = run_list_dir(repo_root, ".")
    initial_msg = (
        f"<repo_structure>\n{structure}\n</repo_structure>\n\n"
        f"<search_string>\n{query}\n</search_string>"
    )

    messages: list[dict] = [
        {"role": "user", "content": initial_msg},
    ]

    # Lazy import to avoid circular deps at module level
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url="https://api.morphllm.com/v1")

    for turn in range(1, MAX_TURNS + 1):
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            temperature=0.0,
            max_tokens=2048,
        )
        msg = response.choices[0].message
        messages.append(msg.model_dump())

        tool_calls = msg.tool_calls or []
        if not tool_calls:
            break

        # Check for finish
        finish_call = next((tc for tc in tool_calls if tc.function.name == "finish"), None)
        if finish_call:
            args = json.loads(finish_call.function.arguments)
            return _resolve_finish(repo_root, args)

        # Execute all tool calls and send results back
        for tc in tool_calls:
            args = json.loads(tc.function.arguments)
            result = dispatch_tool(tc.function.name, args, repo_root)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })

        # Add turn counter
        remaining = MAX_TURNS - turn
        if remaining <= 1:
            turn_msg = (
                f"You have used {turn} turns, you only have 1 turn remaining. "
                f"You have run out of turns to explore the code base and "
                f"MUST call the finish tool now"
            )
        else:
            turn_msg = f"You have used {turn} turn{'s' if turn > 1 else ''} and have {remaining} remaining"

        total_chars = sum(len(m.get("content", "") or "") for m in messages)
        percent = round((total_chars / MAX_CONTEXT_CHARS) * 100)
        used_k = total_chars // 1000
        max_k = MAX_CONTEXT_CHARS // 1000

        messages.append({
            "role": "user",
            "content": f"{turn_msg}\n<context_budget>{percent}% ({used_k}K/{max_k}K chars)</context_budget>",
        })

    return []


# ── Handler ─────────────────────────────────────────────────────────────────


def _morph_warp_grep_handler(args: dict, **kw) -> str:
    """Execute a WarpGrep codebase search."""
    query = args.get("query", "")
    target_dirs = args.get("target_directories", [])

    if not query:
        return json.dumps({"error": "query is required"})

    api_key = os.environ.get("MORPH_API_KEY")
    if not api_key:
        return json.dumps({"error": "MORPH_API_KEY not set. Set it in your environment or config.yaml."})

    # Determine repo root — use workspace root or cwd
    repo_root = os.environ.get("SIDEKICK_WORKSPACE_DIR") or os.getcwd()
    if target_dirs:
        repo_root = str(Path(repo_root) / target_dirs[0])

    try:
        results = search(query, repo_root)
        if not results:
            return json.dumps({"results": [], "message": "No relevant code found."})

        # Format results compactly
        formatted = []
        for r in results:
            if "error" in r:
                formatted.append(r)
            else:
                formatted.append({
                    "path": r["path"],
                    "content": r["content"][:2000] if len(r.get("content", "")) > 2000 else r.get("content", ""),
                })

        return json.dumps({"results": formatted, "count": len(formatted)})

    except Exception as e:
        logger.exception("Morph WarpGrep failed")
        return json.dumps({"error": f"Morph WarpGrep failed: {e}"})


# ── Registry ────────────────────────────────────────────────────────────────

from tools.registry import registry

registry.register(
    name="morph_codebase_search",
    toolset="morph",
    schema=MORPH_WARP_GREP_SCHEMA,
    handler=_morph_warp_grep_handler,
    requires_env=["MORPH_API_KEY"],
    emoji="🔍",
)
