from __future__ import annotations

import argparse
import logging
import json
from pathlib import Path

from sidekick_constants import display_sidekick_home, get_config_path, get_env_path, get_skills_dir
from shared.agent_bridge import run_assistant_once
from shared.config import (
    ensure_sidekick_home,
    get_config_value,
    runtime_summary,
    set_config_value,
)
from shared.paths import build_runtime_snapshot, runtime_warnings
from shared.repo_safety import scan_repo
from shared.runtime import build_runtime_report
from shared.logging_setup import get_logs_dir, setup_logging
from shared.sessions import list_sessions
from web.server import serve_forever


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sidekick")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("paths", help="show canonical Sidekick paths")
    subparsers.add_parser("doctor", help="show repo/runtime migration status")
    subparsers.add_parser("config-summary", help="show merged config and env metadata")
    subparsers.add_parser("logs-path", help="show canonical logs directory")
    chat_once = subparsers.add_parser("chat-once", help="run a one-shot assistant bridge call")
    chat_once.add_argument("prompt")
    web = subparsers.add_parser("web", help="describe or start the new Sidekick web surface")
    web_subparsers = web.add_subparsers(dest="web_command")
    web_subparsers.add_parser("info", help="show web runtime info")
    web_subparsers.add_parser("serve", help="start the minimal Sidekick web server")
    web_subparsers.add_parser("sessions", help="show persisted web sessions")

    config = subparsers.add_parser("config", help="read or update Sidekick config")
    config_subparsers = config.add_subparsers(dest="config_command")
    config_subparsers.add_parser("show", help="show merged config")
    config_subparsers.add_parser("path", help="show config.yaml path")
    config_subparsers.add_parser("env-path", help="show .env path")
    config_get = config_subparsers.add_parser("get", help="get a config value by dotted key")
    config_get.add_argument("key")
    config_set = config_subparsers.add_parser("set", help="set a config value by dotted key")
    config_set.add_argument("key")
    config_set.add_argument("value")

    audit = subparsers.add_parser("audit-repo", help="scan repo for unsafe files")
    audit.add_argument(
        "--repo-root",
        default=str(Path(__file__).resolve().parents[1]),
        help="path to the repo root to scan",
    )
    return parser


def _cmd_paths() -> int:
    print(json.dumps(build_runtime_snapshot(), indent=2))
    return 0


def _cmd_doctor() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    ensure_sidekick_home()
    logs_dir = setup_logging()
    snapshot = build_runtime_snapshot()
    runtime = build_runtime_report(repo_root)
    logging.getLogger("sidekick.cli").info("doctor command invoked")
    print("Sidekick consolidation baseline")
    print(f"repo_root: {repo_root}")
    print(f"sidekick_home: {snapshot['sidekick_home']}")
    print(f"display_home: {display_sidekick_home()}")
    print(f"state_dir: {snapshot['state_dir']}")
    print(f"config_path: {get_config_path()}")
    print(f"env_path: {get_env_path()}")
    print(f"skills_dir: {get_skills_dir()}")
    print(f"logs_dir: {logs_dir}")
    print(f"legacy_env_detected: {snapshot['legacy_env_detected']}")
    print(
        "webui: "
        f"host={runtime['web']['host']} "
        f"port={runtime['web']['port']} "
        f"state_dir={runtime['web']['state_dir']}"
    )
    print(
        "agent: "
        f"dir={runtime['web']['agent_dir']} "
        f"python={runtime['web']['python_exe']}"
    )
    print("surfaces: cli=bootstrap-ready tui=foundation webui=foundation")
    warnings = runtime_warnings(repo_root)
    if warnings:
        print("warnings:")
        for warning in warnings:
            print(f"  - {warning}")
    return 0


def _cmd_config_summary() -> int:
    ensure_sidekick_home()
    setup_logging()
    print(json.dumps(runtime_summary(), indent=2))
    return 0


def _cmd_logs_path() -> int:
    ensure_sidekick_home()
    print(get_logs_dir())
    return 0


def _cmd_chat_once(prompt: str) -> int:
    ensure_sidekick_home()
    setup_logging()
    result = run_assistant_once(prompt)
    print(result.reply)
    if result.error:
        print(f"[bridge] {result.error}")
    return 0 if result.ok else 1


def _cmd_web_info() -> int:
    ensure_sidekick_home()
    setup_logging()
    repo_root = Path(__file__).resolve().parents[1]
    print(json.dumps(build_runtime_report(repo_root)["web"], indent=2))
    return 0


def _cmd_web_serve() -> int:
    ensure_sidekick_home()
    setup_logging()
    serve_forever()
    return 0


def _cmd_web_sessions() -> int:
    ensure_sidekick_home()
    print(json.dumps(list_sessions(), indent=2))
    return 0


def _cmd_config_show() -> int:
    ensure_sidekick_home()
    print(json.dumps(runtime_summary()["config"], indent=2))
    return 0


def _cmd_config_path() -> int:
    print(get_config_path())
    return 0


def _cmd_config_env_path() -> int:
    print(get_env_path())
    return 0


def _cmd_config_get(key: str) -> int:
    ensure_sidekick_home()
    value = get_config_value(key)
    if value is None:
        print(f"Key not found: {key}")
        return 1
    print(json.dumps(value, indent=2) if isinstance(value, (dict, list)) else value)
    return 0


def _cmd_config_set(key: str, value: str) -> int:
    ensure_sidekick_home()
    path, parsed = set_config_value(key, value)
    rendered = json.dumps(parsed, indent=2) if isinstance(parsed, (dict, list)) else parsed
    print(f"Updated {key} in {path}")
    print(f"value: {rendered}")
    return 0


def _cmd_audit_repo(repo_root: str) -> int:
    findings = scan_repo(Path(repo_root))
    if findings:
        for finding in findings:
            print(f"BLOCKED: {finding}")
        return 1
    print("No unsafe tracked files detected by the baseline scanner.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "paths":
        return _cmd_paths()
    if args.command == "doctor":
        return _cmd_doctor()
    if args.command == "config-summary":
        return _cmd_config_summary()
    if args.command == "logs-path":
        return _cmd_logs_path()
    if args.command == "chat-once":
        return _cmd_chat_once(args.prompt)
    if args.command == "web":
        if args.web_command == "info":
            return _cmd_web_info()
        if args.web_command == "serve":
            return _cmd_web_serve()
        if args.web_command == "sessions":
            return _cmd_web_sessions()
        parser.print_help()
        return 1
    if args.command == "config":
        if args.config_command == "show":
            return _cmd_config_show()
        if args.config_command == "path":
            return _cmd_config_path()
        if args.config_command == "env-path":
            return _cmd_config_env_path()
        if args.config_command == "get":
            return _cmd_config_get(args.key)
        if args.config_command == "set":
            return _cmd_config_set(args.key, args.value)
        parser.print_help()
        return 1
    if args.command == "audit-repo":
        return _cmd_audit_repo(args.repo_root)

    parser.print_help()
    return 0
