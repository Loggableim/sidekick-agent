from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


WEBUI_FILES = ("workspaces.json", "settings.json", "last_workspace.txt", "projects.json", "agents.db")
SECRET_FILES = (".env", "auth.json")
STATE_FILES = ("state.db", "state.db-wal", "state.db-shm")
SECRET_MARKERS = ("key", "token", "secret", "password", "auth")


@dataclass
class RepairPlan:
    source: Path
    target: Path
    apply: bool = False
    counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    config_status: str = "missing"


@dataclass
class RepairResult:
    plan: RepairPlan
    copied: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    backup_path: Path | None = None
    user_env_set: bool = False


def _count_sqlite_rows(db_path: Path, table: str) -> int:
    if not db_path.exists() or db_path.stat().st_size == 0:
        return 0
    if table not in {"sessions", "messages"}:
        return 0
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
        try:
            if table == "sessions":
                row = con.execute("SELECT COUNT(*) FROM sessions").fetchone()
            else:
                row = con.execute("SELECT COUNT(*) FROM messages").fetchone()
            return int(row[0])
        finally:
            con.close()
    except Exception:
        return 0


def _redact(text: str) -> str:
    safe_lines: list[str] = []
    for line in text.splitlines():
        low = line.lower()
        if any(marker in low for marker in SECRET_MARKERS):
            if ":" in line:
                safe_lines.append(line.split(":", 1)[0] + ": <redacted>")
            elif "=" in line:
                safe_lines.append(line.split("=", 1)[0] + "=<redacted>")
            else:
                safe_lines.append("<redacted>")
        else:
            safe_lines.append(line)
    return "\n".join(safe_lines)


def repair_known_config_yaml(text: str) -> tuple[str, bool]:
    """Repair a known ``custom_providers`` indentation slip.

    A root-level ``key_env`` between two provider entries makes the whole YAML
    invalid. The safe interpretation is that the key belongs to the previous
    provider, so indent it to provider-field level.
    """
    lines = text.splitlines()
    repaired: list[str] = []
    in_provider_map = False
    last_provider_seen = False
    changed = False
    for line in lines:
        stripped = line.strip()
        if stripped in {"custom_providers:", "providers:"}:
            in_provider_map = True
            last_provider_seen = False
            repaired.append(line)
            continue
        if in_provider_map:
            if stripped and not line.startswith((" ", "\t")):
                if stripped.startswith("key_env:") and last_provider_seen:
                    repaired.append("    " + stripped)
                    changed = True
                    continue
                if not stripped.startswith("#"):
                    in_provider_map = False
                    last_provider_seen = False
            elif line.startswith("  ") and not line.startswith("    ") and stripped.endswith(":"):
                last_provider_seen = True
        repaired.append(line)
    suffix = "\n" if text.endswith("\n") else ""
    return "\n".join(repaired) + suffix, changed


def _load_config_status(config_path: Path, warnings: list[str]) -> str:
    if not config_path.exists():
        return "missing"
    raw = config_path.read_text(encoding="utf-8-sig", errors="replace")
    try:
        yaml.safe_load(raw)
        return "valid"
    except Exception as exc:
        repaired, changed = repair_known_config_yaml(raw)
        if changed:
            try:
                yaml.safe_load(repaired)
                warnings.append(f"config.yaml invalid but repairable: {_redact(str(exc))}")
                return "invalid_repairable"
            except Exception:
                pass
        warnings.append(f"config.yaml invalid; not copying without repair: {_redact(str(exc))}")
        return "invalid"


def build_repair_plan(source: str | Path, target: str | Path, *, apply: bool = False) -> RepairPlan:
    src = Path(source).expanduser().resolve()
    dst = Path(target).expanduser().resolve()
    warnings: list[str] = []
    counts = {
        "spaces": 0,
        "state_sessions": 0,
        "state_messages": 0,
        "webui_files": 0,
        "secret_files": 0,
        "state_files": 0,
    }

    if not src.exists():
        warnings.append(f"source does not exist: {src}")
    spaces_dir = src / "spaces"
    if spaces_dir.is_dir():
        counts["spaces"] = sum(1 for child in spaces_dir.iterdir() if child.is_dir())
    webui = src / "webui"
    if webui.is_dir():
        counts["webui_files"] = sum(1 for name in WEBUI_FILES if (webui / name).exists())
    counts["secret_files"] = sum(1 for name in SECRET_FILES if (src / name).exists())
    counts["state_files"] = sum(1 for name in STATE_FILES if (src / name).exists())
    counts["state_sessions"] = _count_sqlite_rows(src / "state.db", "sessions")
    counts["state_messages"] = _count_sqlite_rows(src / "state.db", "messages")
    config_status = _load_config_status(src / "config.yaml", warnings)

    return RepairPlan(source=src, target=dst, apply=apply, counts=counts, warnings=warnings, config_status=config_status)


def _add_tree_to_zip(zf: zipfile.ZipFile, root: Path, *, exclude: Path | None = None) -> None:
    if not root.exists():
        return
    exclude_resolved = exclude.resolve() if exclude else None
    for item in root.rglob("*"):
        if exclude_resolved and item.resolve() == exclude_resolved:
            continue
        if item.is_file():
            zf.write(item, item.relative_to(root.parent))


def _create_backup(target: Path) -> Path | None:
    if not target.exists():
        return None
    backup_dir = target / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"pre-local-state-repair-{stamp}.zip"
    with zipfile.ZipFile(backup_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        _add_tree_to_zip(zf, target, exclude=backup_path)
    return backup_path


def _copy_file(src: Path, dst: Path, result: RepairResult, rel: str, *, allow_empty_state_replace: bool = True) -> None:
    if not src.exists():
        return
    if dst.exists():
        if dst.name.startswith("state.db") and allow_empty_state_replace:
            pass
        else:
            result.skipped.append(rel)
            return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    result.copied.append(rel)


def _copy_tree_no_overwrite(src: Path, dst: Path, result: RepairResult, rel_root: str) -> None:
    if not src.is_dir():
        return
    for child in sorted(src.iterdir()):
        rel = f"{rel_root}/{child.name}"
        target = dst / child.name
        if target.exists():
            result.skipped.append(rel)
            continue
        if child.is_dir():
            shutil.copytree(child, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(child, target)
        result.copied.append(rel)


def _copy_config(plan: RepairPlan, result: RepairResult) -> None:
    src = plan.source / "config.yaml"
    if not src.exists():
        return
    dst = plan.target / "config.yaml"
    if dst.exists():
        result.skipped.append("config.yaml")
        return
    raw = src.read_text(encoding="utf-8-sig", errors="replace")
    if plan.config_status == "valid":
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        result.copied.append("config.yaml")
        return
    if plan.config_status == "invalid_repairable":
        repaired, _ = repair_known_config_yaml(raw)
        yaml.safe_load(repaired)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(repaired, encoding="utf-8")
        result.copied.append("config.yaml")
        result.warnings.append("config.yaml copied with known indentation repair")
        return
    result.skipped.append("config.yaml")


def _set_user_sidekick_home(target: Path) -> bool:
    if os.name != "nt":
        return False
    try:
        completed = subprocess.run(
            ["setx", "SIDEKICK_HOME", str(target)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return completed.returncode == 0
    except Exception:
        return False


def apply_repair_plan(plan: RepairPlan, *, set_user_env: bool = False) -> RepairResult:
    result = RepairResult(plan=plan, warnings=list(plan.warnings))
    if not plan.apply:
        return result
    if not plan.source.exists():
        result.warnings.append(f"source missing: {plan.source}")
        return result

    plan.target.mkdir(parents=True, exist_ok=True)
    result.backup_path = _create_backup(plan.target)

    _copy_tree_no_overwrite(plan.source / "spaces", plan.target / "spaces", result, "spaces")
    for name in WEBUI_FILES:
        _copy_file(plan.source / "webui" / name, plan.target / "webui" / name, result, f"webui/{name}")
    for name in SECRET_FILES:
        _copy_file(plan.source / name, plan.target / name, result, name)
    target_state_db = plan.target / "state.db"
    replace_state_files = (not target_state_db.exists()) or target_state_db.stat().st_size <= 4096
    for name in STATE_FILES:
        _copy_file(
            plan.source / name,
            plan.target / name,
            result,
            name,
            allow_empty_state_replace=replace_state_files,
        )
    _copy_config(plan, result)

    if set_user_env:
        result.user_env_set = _set_user_sidekick_home(plan.target)
        if not result.user_env_set:
            result.warnings.append("could not set User env SIDEKICK_HOME")
    return result


def _print_plan(plan: RepairPlan) -> None:
    print("Sidekick local-state repair")
    print(f"  source : {plan.source}")
    print(f"  target : {plan.target}")
    print(f"  mode   : {'apply' if plan.apply else 'dry-run'}")
    print(f"  config : {plan.config_status}")
    for key, value in plan.counts.items():
        print(f"  {key:15} {value}")
    for warning in plan.warnings:
        print(f"  WARNING: {warning}")


def _print_result(result: RepairResult) -> None:
    if result.backup_path:
        print(f"  backup : {result.backup_path}")
    print(f"  copied : {len(result.copied)}")
    print(f"  skipped: {len(result.skipped)}")
    if result.skipped:
        for item in result.skipped[:20]:
            print(f"    skip {item}")
    for warning in result.warnings:
        print(f"  WARNING: {warning}")
    if result.user_env_set:
        print(f"  env    : SIDEKICK_HOME={result.plan.target}")


def run_local_state_repair(args: Any) -> int:
    from shared.constants import get_sidekick_home

    source_value = str(getattr(args, "source", "") or "").strip()
    if not source_value:
        print("--from PATH is required; state import is always explicit", file=sys.stderr)
        return 2
    source = Path(source_value)
    target = Path(getattr(args, "target", None) or get_sidekick_home())
    apply = bool(getattr(args, "apply", False))
    set_user_env = apply and not bool(getattr(args, "no_user_env", False))
    plan = build_repair_plan(source, target, apply=apply)
    _print_plan(plan)
    source_missing = any(w.startswith("source does not exist:") for w in plan.warnings)
    if not apply:
        print("  next   : re-run with --apply to copy local state")
        return 2 if source_missing else 0
    result = apply_repair_plan(plan, set_user_env=set_user_env)
    _print_result(result)
    return 0 if not any(w.startswith("source missing") for w in result.warnings) else 2


__all__ = [
    "RepairPlan",
    "RepairResult",
    "apply_repair_plan",
    "build_repair_plan",
    "repair_known_config_yaml",
    "run_local_state_repair",
]
