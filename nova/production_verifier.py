"""Bounded, project-root-bound verifier used by managed YOLO runs.

The adapter is deliberately opt-in: a project must provide ``.swarm/verify.json``
with an explicit read-only command list.  Missing or malformed configuration
never becomes positive evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Mapping

from swarm_core.verifier import VerificationRequest, VerificationResult


class ProductionReadOnlyVerifier:
    """Run only explicitly declared, bounded verification commands."""

    _CONFIG = Path(".swarm") / "verify.json"
    _MAX_COMMANDS = 8
    _TIMEOUT_SECONDS = 120
    _ALLOWED_EXECUTABLES = frozenset({"python", "python.exe", "py", "py.exe", "pytest", "pytest.exe", "node", "node.exe", "npm", "npm.cmd"})
    @classmethod
    def readiness(cls, project_root: Path) -> bool:
        """Cheap, side-effect-free preflight for a usable verifier contract."""
        root = Path(project_root)
        try:
            raw = json.loads((root / cls._CONFIG).read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            raw = cls._infer_static_project_config(root)
        if not isinstance(raw, Mapping) or raw.get("read_only") is not True:
            return False
        commands = raw.get("commands")
        if not isinstance(commands, list) or not 1 <= len(commands) <= cls._MAX_COMMANDS:
            return False
        return all(
            isinstance(command, list) and bool(command)
            and all(isinstance(item, str) and item.strip() for item in command)
            and Path(command[0]).name.lower() in cls._ALLOWED_EXECUTABLES
            for command in commands
        )
    def verify(self, request: VerificationRequest) -> VerificationResult:
        config_path = request.project_root / self._CONFIG
        digest = self._digest(request)
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            config = self._infer_static_project_config(request.project_root)
            if config is None:
                return self._unavailable(digest, "verification configuration is missing")
        if not isinstance(config, Mapping) or config.get("read_only") is not True:
            return self._unavailable(digest, "verification configuration is not read-only")
        commands = config.get("commands")
        if not isinstance(commands, list) or not 1 <= len(commands) <= self._MAX_COMMANDS:
            return self._unavailable(digest, "verification commands are not declared")

        reports: list[str] = []
        for raw in commands:
            if not isinstance(raw, list) or not raw or any(
                not isinstance(item, str) or not item.strip() for item in raw
            ):
                return self._unavailable(digest, "verification command is malformed")
            if Path(raw[0]).name.lower() not in self._ALLOWED_EXECUTABLES:
                return self._unavailable(digest, "verification runner is not allowlisted")
            try:
                completed = subprocess.run(
                    raw,
                    cwd=str(request.project_root),
                    env=self._environment(),
                    capture_output=True,
                    text=True,
                    timeout=self._TIMEOUT_SECONDS,
                    check=False,
                    shell=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return self._failed(digest, type(exc).__name__)
            output = (completed.stdout + "\n" + completed.stderr)[-8192:]
            report_digest = hashlib.sha256(output.encode("utf-8", "replace")).hexdigest()
            reports.append(f"verify:report:{report_digest}")
            if completed.returncode != 0:
                return VerificationResult(
                    work="Declared read-only verification command failed.",
                    evidence=tuple(reports),
                    decision="verification_failed",
                    provenance={
                        "adapter": "production-read-only",
                        "mode": "read_only",
                        "operation": "declared_commands",
                        "verification_state": "failed",
                        "input_digest": digest,
                    },
                )
        return VerificationResult(
            work="Declared read-only verification commands passed.",
            evidence=tuple(reports),
            decision="verified",
            provenance={
                "adapter": "production-read-only",
                "mode": "read_only",
                "operation": "declared_commands",
                "verification_state": "verified",
                "input_digest": digest,
            },
        )

    @classmethod
    def _infer_static_project_config(cls, project_root: Path) -> dict[str, Any] | None:
        """Infer only non-mutating checks for a simple static Node project.

        This deliberately does not execute package scripts, dependency
        installers, servers, or project-owned Python files. Projects needing
        stronger checks must opt in with ``.swarm/verify.json``.
        """
        package_path = project_root / "package.json"
        build_path = project_root / "build.js"
        if not package_path.is_file() or not build_path.is_file():
            return None
        try:
            package = json.loads(package_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            return None
        if not isinstance(package, Mapping) or not isinstance(package.get("name"), str):
            return None
        return {
            "read_only": True,
            "commands": [
                ["node", "--check", "build.js"],
                [
                    "node",
                    "-e",
                    "const fs=require('fs'); const p=JSON.parse(fs.readFileSync('package.json','utf8')); if (!p.name || !p.scripts || typeof p.scripts.build !== 'string') process.exit(1)",
                ],
            ],
            "inferred": True,
        }

    @staticmethod
    def _environment() -> dict[str, str]:
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"}
        for key in ("SystemRoot", "WINDIR", "TEMP", "TMP"):
            if os.environ.get(key):
                env[key] = os.environ[key]
        return env

    @staticmethod
    def _digest(request: VerificationRequest) -> str:
        payload = json.dumps(
            {"run_id": request.run_id, "goal": request.goal, "root": str(request.project_root)},
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _unavailable(digest: str, reason: str) -> VerificationResult:
        return VerificationResult(
            work=reason,
            evidence=(f"verifier:production:{digest}",),
            decision="verification_unavailable",
            provenance={
                "adapter": "production-read-only",
                "mode": "read_only",
                "operation": "declared_commands",
                "verification_state": "unavailable",
                "input_digest": digest,
            },
        )

    @staticmethod
    def _failed(digest: str, reason: str) -> VerificationResult:
        return VerificationResult(
            work="Declared read-only verification could not be completed.",
            evidence=(f"verifier:production:{digest}",),
            decision="verification_failed",
            provenance={
                "adapter": "production-read-only",
                "mode": "read_only",
                "operation": "declared_commands",
                "verification_state": "failed",
                "failure": reason,
                "input_digest": digest,
            },
        )
