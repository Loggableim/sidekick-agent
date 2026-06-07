#!/usr/bin/env python3
"""
Copy dependent agent modules that import from agent/* or compat modules.
These are modules that depend on the base modules already copied to runtime/.

Import rewrite rules match those in copy_runtime_modules.py plus:
  - 'from agent import' → 'from runtime import'  (bare agent, no dot)

This version handles BOTH module-level and lazy/indented imports inside
function bodies.
"""
import os
import re

SRC_BASE = r"C:\HermesPortable\cids-hermes-agent"
DST_BASE = r"C:\HermesPortable\sidekick"

SRC_AGENT = os.path.join(SRC_BASE, "agent")
DST_RUNTIME = os.path.join(DST_BASE, "runtime")
DST_COMPAT = os.path.join(DST_RUNTIME, "_compat")

# Files to copy (in order as listed in the task)
FILES_TO_COPY = [
    "image_gen_registry.py",
    "image_routing.py",
    "memory_manager.py",
    "context_references.py",
    "account_usage.py",
    "skill_utils.py",
    "usage_pricing.py",
    "insights.py",
    "prompt_builder.py",
    "skill_commands.py",
    "subdirectory_hints.py",
    "copilot_acp_client.py",
    "codex_responses_adapter.py",
    "gemini_native_adapter.py",
    "gemini_cloudcode_adapter.py",
    "title_generator.py",
    "plugin_llm.py",
]

# Import rewriting rules — applied as string replacements (re.sub), NOT line-start anchored.
# The patterns match anywhere in a line so they work for both module-level and indented/lazy imports.
REWRITE_RULES = [
    # 'from agent.' → 'from runtime.'
    (r'from agent\.', 'from runtime.'),
    # 'from agent import' → 'from runtime import' (bare agent, no dot)
    (r'from agent import', 'from runtime import'),
    # 'import agent' → 'import runtime' (bare agent import, standalone line)
    (r'^[ \t]*import agent$', 'import runtime'),
    # 'from sidekick_constants import' → 'from runtime._compat.shim_constants import'
    (r'from sidekick_constants import', 'from runtime._compat.shim_constants import'),
    # 'from hermes_constants import' → 'from runtime._compat.shim_constants import'
    (r'from hermes_constants import', 'from runtime._compat.shim_constants import'),
    # 'from utils import' → 'from shared.utils import'
    (r'from utils import', 'from shared.utils import'),
    # 'import utils' → 'from shared import utils'
    (r'^[ \t]*import utils$', 'from shared import utils'),
    # 'from sidekick_logging import' → 'from runtime._compat.shim_logging import'
    (r'from sidekick_logging import', 'from runtime._compat.shim_logging import'),
    # 'from sidekick_state import' → 'from runtime._compat.shim_state import'
    (r'from sidekick_state import', 'from runtime._compat.shim_state import'),
    # 'from sidekick_bootstrap import' → 'from runtime._compat.shim_bootstrap import'
    (r'from sidekick_bootstrap import', 'from runtime._compat.shim_bootstrap import'),
    # 'import sidekick_bootstrap' → remove line
    (r'^[ \t]*import sidekick_bootstrap[ \t]*(#.*)?$', None),
]

# Also rewrite any remaining 'agent.' references that might be in import-like context
# but NOT inside string literals or comments (handled by same mechanism above)


def rewrite_file(content: str) -> tuple[str, list[str]]:
    """Apply import rewrites. Returns (new_content, pending_imports)."""
    lines = content.splitlines(keepends=True)
    result_lines = []
    pending = []

    for line in lines:
        stripped = line.strip()

        # Check for sidekick_cli imports to flag
        if re.search(r'(?:from|import)\s+sidekick_cli', stripped):
            pending.append(stripped)

        # Apply rewrite rules
        rewritten = False
        for old_pattern, new_pattern in REWRITE_RULES:
            if re.search(old_pattern, line):
                if new_pattern is None:
                    # Remove the line
                    line = re.sub(old_pattern, '', line)
                    # If the whole line is now empty (or just whitespace), make it truly empty
                    if not line.strip():
                        line = '\n'
                    rewritten = True
                else:
                    line = re.sub(old_pattern, new_pattern, line)
                    rewritten = True
                break  # first matching rule per line

        result_lines.append(line)

    return ''.join(result_lines), pending


def main():
    os.makedirs(DST_RUNTIME, exist_ok=True)
    os.makedirs(DST_COMPAT, exist_ok=True)

    copied = []
    errors = []
    all_pending = {}

    for filename in FILES_TO_COPY:
        src_path = os.path.join(SRC_AGENT, filename)
        dst_path = os.path.join(DST_RUNTIME, filename)

        if not os.path.isfile(src_path):
            errors.append(f"SOURCE NOT FOUND: {src_path}")
            continue

        with open(src_path, 'r', encoding='utf-8') as f:
            content = f.read()

        original = content
        content, pending = rewrite_file(content)
        changed = content != original

        if pending:
            all_pending[filename] = pending

        with open(dst_path, 'w', encoding='utf-8') as f:
            f.write(content)

        status = "REWRITTEN" if changed else "COPIED (no internal imports)"
        if pending:
            status += " [HAS PENDING sidekick_cli IMPORTS]"
        copied.append(f"  {filename}: {status}")
        print(f"  {status}")

    # Write PENDING_IMPORTS.txt
    pending_path = os.path.join(DST_COMPAT, "PENDING_IMPORTS.txt")
    with open(pending_path, 'w', encoding='utf-8') as f:
        f.write("Modules with sidekick_cli imports that still need porting\n")
        f.write("=" * 60 + "\n\n")
        if all_pending:
            for filename, imports in sorted(all_pending.items()):
                f.write(f"{filename}:\n")
                for imp in imports:
                    f.write(f"    {imp}\n")
                f.write("\n")
        else:
            f.write("(none found)\n\n")

        f.write("Known modules that import sidekick_cli directly (not copied, need manual porting):\n")
        f.write("-" * 60 + "\n")
        f.write("""
These were identified as importing sidekick_cli directly and were skipped:
  - credential_pool.py     (imports sidekick_cli.auth, sidekick_cli.config)
  - auxiliary_client.py    (imports sidekick_cli, credential_pool)
  - context_compressor.py  (imports agent.auxiliary_client -> needs credential_pool)
  - curator.py             (imports 'agent' as a package)

Additional notes:
  - account_usage.py was COPIED but has sidekick_cli imports flagged above.
  - title_generator.py and plugin_llm.py were copied but import
    auxiliary_client, which itself depends on credential_pool.
    These need shims for auxiliary_client before they can work.
""")
        f.write("\n")

    # Summary
    print("\n" + "=" * 60)
    print(f"COPIED: {len(copied)} files")
    if all_pending:
        print(f"PENDING sidekick_cli imports in: {len(all_pending)} file(s)")
        for fname in all_pending:
            print(f"  - {fname}")
    if errors:
        print(f"ERRORS: {len(errors)}")
        for e in errors:
            print(f"  - {e}")
    print(f"\nPending imports written to: {pending_path}")


if __name__ == "__main__":
    main()
