"""Argparse and terminal presentation for the project-local Swarm surface."""

from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any, Callable, Mapping

from cli.swarm_host import SidekickSwarmService, get_cli_host_actor
from swarm_core.config import initialize_project


def build_parser(
    parent_subparsers: argparse._SubParsersAction,
) -> argparse.ArgumentParser:
    """Attach ``sidekick swarm`` without leaking CLI concerns into Core."""
    parser = parent_subparsers.add_parser(
        "swarm",
        help="Run the project-local, Ollama Cloud-only Swarm workflow",
        description=(
            "Manage project-local Swarm runs. Model availability is refreshed "
            "only by the explicit `models refresh` command."
        ),
    )
    _add_common_arguments(parser, defaults=True)
    actions = parser.add_subparsers(dest="swarm_action")

    init = actions.add_parser("init", help="Initialize versioned .swarm configuration")
    _add_common_arguments(init)

    run = actions.add_parser("run", help="Create and run a Swarm workflow")
    _add_common_arguments(run)
    run.add_argument("goal", help="Bounded task goal")
    run.add_argument("--pack", default="coding-team", help="Swarm pack id")

    status = actions.add_parser(
        "status", help="Read project run status without mutation"
    )
    _add_common_arguments(status)
    status.add_argument("run_id", nargs="?", help="Optional exact run id")

    approve = actions.add_parser("approve", help="Record a human proposal decision")
    _add_common_arguments(approve)
    approve.add_argument("run_id", help="Run containing the durable proposal")
    approve.add_argument("proposal_id", help="Persisted proposal id")
    approve.add_argument("--deny", action="store_true", help="Record a human denial")

    pause = actions.add_parser("pause", help="Pause a currently running run")
    _add_common_arguments(pause)
    pause.add_argument("run_id")
    resume = actions.add_parser("resume", help="Resume a paused run")
    _add_common_arguments(resume)
    resume.add_argument("run_id")

    models = actions.add_parser("models", help="Inspect or explicitly refresh models")
    _add_common_arguments(models)
    models_actions = models.add_subparsers(dest="swarm_models_action")
    refresh = models_actions.add_parser(
        "refresh", help="Explicitly refresh Ollama Cloud catalog"
    )
    _add_common_arguments(refresh)

    packs = actions.add_parser("packs", help="List declarative Swarm packs")
    _add_common_arguments(packs)
    packs_actions = packs.add_subparsers(dest="swarm_packs_action")
    list_packs = packs_actions.add_parser(
        "list", help="List shipped/project pack metadata"
    )
    _add_common_arguments(list_packs)

    parser.set_defaults(_swarm_parser=parser)
    return parser


def get_swarm_service() -> SidekickSwarmService:
    """Construct a state-free host service; writes happen only in its methods."""
    return SidekickSwarmService()


def _add_common_arguments(
    parser: argparse.ArgumentParser, *, defaults: bool = False
) -> None:
    """Accept shared output/project flags before or after a nested action."""
    parser.add_argument(
        "--project",
        default=str(Path.cwd()) if defaults else argparse.SUPPRESS,
        metavar="PATH",
        help="Project root (default: current directory)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False if defaults else argparse.SUPPRESS,
        help="Emit structured JSON",
    )


def swarm_command(
    args: argparse.Namespace,
    *,
    service: SidekickSwarmService | Any | None = None,
    actor_factory: Callable[[], str] | None = None,
) -> int:
    """Dispatch one command and return a shell-style exit code."""
    action = getattr(args, "swarm_action", None)
    parser = getattr(args, "_swarm_parser", None)
    if not action:
        if parser is not None:
            parser.print_help()
        return 0
    project_root = Path(getattr(args, "project", Path.cwd())).expanduser().resolve()
    json_output = bool(getattr(args, "json", False))
    try:
        if action == "init":
            config = initialize_project(project_root)
            _emit(
                {
                    "project_root": str(config.project_root),
                    "config_path": str(config.config_path),
                    "default_provider": config.default_provider,
                    "default_model": config.default_model,
                    "default_autonomy": config.default_autonomy,
                },
                json_output=json_output,
            )
            return 0

        service = service or get_swarm_service()
        if action == "run":
            _emit(
                service.run(args.goal, project_root, pack=args.pack),
                json_output=json_output,
            )
            return 0
        if action == "status":
            _emit(
                service.status(project_root, getattr(args, "run_id", None)),
                json_output=json_output,
            )
            return 0
        if action == "approve":
            actor_id = (actor_factory or get_cli_host_actor)()
            _emit(
                service.record_human_approval(
                    project_root,
                    args.run_id,
                    args.proposal_id,
                    actor_id=actor_id,
                    approved=not bool(args.deny),
                ),
                json_output=json_output,
            )
            return 0
        if action == "pause":
            _emit(service.pause(project_root, args.run_id), json_output=json_output)
            return 0
        if action == "resume":
            _emit(service.resume(project_root, args.run_id), json_output=json_output)
            return 0
        if action == "models":
            if getattr(args, "swarm_models_action", None) != "refresh":
                _print_subcommand_help(parser, "models")
                return 0
            _emit(service.refresh_models(project_root), json_output=json_output)
            return 0
        if action == "packs":
            if getattr(args, "swarm_packs_action", None) != "list":
                _print_subcommand_help(parser, "packs")
                return 0
            _emit(service.list_packs(project_root), json_output=json_output)
            return 0
    except FileNotFoundError as exc:
        _emit_error(str(exc), json_output=json_output)
        return 1
    except (KeyError, RuntimeError, ValueError, TypeError) as exc:
        _emit_error(str(exc), json_output=json_output)
        return 1
    _emit_error(f"Unknown swarm command: {action}", json_output=json_output)
    return 2


def _emit(value: Any, *, json_output: bool) -> None:
    normalized = _jsonable(value)
    if json_output:
        print(json.dumps(normalized, ensure_ascii=False, sort_keys=True, default=str))
        return
    if isinstance(normalized, Mapping):
        for key, item in normalized.items():
            print(f"{key}: {item}")
        return
    if isinstance(normalized, list):
        for item in normalized:
            print(json.dumps(item, ensure_ascii=False, sort_keys=True, default=str))
        return
    print(normalized)


def _emit_error(message: str, *, json_output: bool) -> None:
    payload = {"ok": False, "error": message}
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=sys.stderr)
    else:
        print(f"swarm: {message}", file=sys.stderr)


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _print_subcommand_help(parser: argparse.ArgumentParser, command: str) -> None:
    # Keep an incomplete nested command (`models`, `packs`) benign and
    # discoverable rather than accidentally treating it as a write.
    print(f"usage: sidekick swarm {command} <action>", file=sys.stderr)
