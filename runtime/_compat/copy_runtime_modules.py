#!/usr/bin/env python3
"""
Copy zero-dependency agent modules from cids-sidekick-agent/agent/ to sidekick/runtime/,
rewriting their internal imports to match the new package structure.

Modules that import NOTHING from the agent package (stdlib/external only) are copied
as-is. Modules importing from 'utils', 'sidekick_constants', etc. get their imports
rewritten per the mapping below.

Also copies sidekick_constants.py and sidekick_constants.py as reference files into
runtime/_compat/.
"""

import os
import shutil
import re

SRC_BASE = r"C:\SidekickPortable\cids-sidekick-agent"
DST_BASE = r"C:\SidekickPortable\sidekick"

SRC_AGENT = os.path.join(SRC_BASE, "agent")
DST_RUNTIME = os.path.join(DST_BASE, "runtime")
DST_COMPAT = os.path.join(DST_RUNTIME, "_compat")
DST_SHARED = os.path.join(DST_BASE, "shared")

# Files to copy from agent/ to runtime/
AGENT_FILES = [
    "error_classifier.py",
    "file_safety.py",
    "redact.py",
    "retry_utils.py",
    "markdown_tables.py",
    "context_engine.py",
    "i18n.py",
    "image_gen_provider.py",
    "models_dev.py",
    "moonshot_schema.py",
    "prompt_caching.py",
    "rate_limit_tracker.py",
    "shell_hooks.py",
    "skill_preprocessing.py",
    "think_scrubber.py",
    "tool_guardrails.py",
    "trajectory.py",
    "display.py",
    "memory_provider.py",
    "curator_backup.py",
    "credential_sources.py",
    "gemini_schema.py",
    "google_code_assist.py",
    "google_oauth.py",
    "lmstudio_reasoning.py",
    "manual_compression_feedback.py",
    "onboarding.py",
    "bedrock_adapter.py",
    "anthropic_adapter.py",
    "model_metadata.py",
]

# Import rewriting rules — applied in order, each as (old_pattern, new_pattern)
# These handle both `from X import Y` and `import X` styles.
REWRITE_RULES = [
    # 1. 'from agent.' → 'from runtime.'  (cross-module refs within the package)
    (r'^from agent\.', 'from runtime.'),
    # 2. 'from sidekick_constants import' → 'from runtime._compat.shim_constants import'
    (r'^from sidekick_constants import', 'from runtime._compat.shim_constants import'),
    # 3. 'from sidekick_logging import' → 'from runtime._compat.shim_logging import'
    (r'^from sidekick_logging import', 'from runtime._compat.shim_logging import'),
    # 4. 'from sidekick_state import' → 'from runtime._compat.shim_state import'
    (r'^from sidekick_state import', 'from runtime._compat.shim_state import'),
    # 5. 'from sidekick_constants import' → 'from runtime._compat.shim_constants import'
    (r'^from sidekick_constants import', 'from runtime._compat.shim_constants import'),
    # 6. 'from sidekick_bootstrap import' → 'from runtime._compat.shim_bootstrap import'
    (r'^from sidekick_bootstrap import', 'from runtime._compat.shim_bootstrap import'),
    # 7. 'from utils import' → 'from shared.utils import'
    (r'^from utils import', 'from shared.utils import'),
    # 8. 'import utils' → 'from shared import utils'
    (r'^import utils$', 'from shared import utils'),
    # 9. 'from sidekick_cli' → 'from runtime._compat.shim_cli'
    (r'^from sidekick_cli', 'from runtime._compat.shim_cli'),
]


def rewrite_imports(content: str) -> str:
    """Apply all import rewrite rules to file content."""
    lines = content.splitlines(keepends=True)
    result = []
    for line in lines:
        for old_pattern, new_pattern in REWRITE_RULES:
            if re.match(old_pattern, line):
                line = re.sub(old_pattern, new_pattern, line)
                break  # only first matching rule per line
        result.append(line)
    return ''.join(result)


def main():
    # Ensure destination directories exist
    os.makedirs(DST_RUNTIME, exist_ok=True)
    os.makedirs(DST_COMPAT, exist_ok=True)
    os.makedirs(DST_SHARED, exist_ok=True)

    copied = []
    errors = []

    for filename in AGENT_FILES:
        src_path = os.path.join(SRC_AGENT, filename)
        dst_path = os.path.join(DST_RUNTIME, filename)

        if not os.path.isfile(src_path):
            errors.append(f"SOURCE NOT FOUND: {src_path}")
            continue

        with open(src_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Detect if any rewrites are needed
        original = content
        content = rewrite_imports(content)

        changed = content != original

        with open(dst_path, 'w', encoding='utf-8') as f:
            f.write(content)

        status = "REWRITTEN" if changed else "COPIED (no internal imports)"
        copied.append(f"  {filename}: {status}")
        print(f"  {status}: {filename}")

    # Copy reference files: sidekick_constants.py → shim_constants_v2.py
    ref_files = [
        ("sidekick_constants.py", "shim_constants_v2.py"),
        ("sidekick_constants.py", "shim_constants_v1.py"),
    ]
    for src_name, dst_name in ref_files:
        src_path = os.path.join(SRC_BASE, src_name)
        dst_path = os.path.join(DST_COMPAT, dst_name)
        if os.path.isfile(src_path):
            shutil.copy2(src_path, dst_path)
            copied.append(f"  {src_name} → {dst_name}: COPIED (reference)")
            print(f"  REFERENCE COPY: {src_name} → {dst_name}")
        else:
            errors.append(f"SOURCE NOT FOUND: {src_path}")

    # Summary
    print("\n" + "=" * 60)
    print(f"TOTAL: {len(copied)} files copied, {len(errors)} errors")
    if errors:
        print("ERRORS:")
        for e in errors:
            print(f"  - {e}")


if __name__ == "__main__":
    main()
