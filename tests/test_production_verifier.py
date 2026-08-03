from pathlib import Path
import json

from nova.production_verifier import ProductionReadOnlyVerifier
from swarm_core.verifier import VerificationRequest


def _request(root: Path) -> VerificationRequest:
    return VerificationRequest("run-1", "verify", root, {"x": 1}, {"y": 2})


def test_missing_config_is_unavailable(tmp_path):
    result = ProductionReadOnlyVerifier().verify(_request(tmp_path))
    assert result.decision == "verification_unavailable"


def test_declared_read_only_command_is_positive(tmp_path):
    swarm = tmp_path / ".swarm"
    swarm.mkdir()
    (swarm / "verify.json").write_text(
        json.dumps({"read_only": True, "commands": [["python", "-c", "print('ok')"]]}),
        encoding="utf-8",
    )
    result = ProductionReadOnlyVerifier().verify(_request(tmp_path))
    assert result.decision == "verified"
    assert result.evidence[0].startswith("verify:report:")


def test_failed_command_cannot_be_positive(tmp_path):
    swarm = tmp_path / ".swarm"
    swarm.mkdir()
    (swarm / "verify.json").write_text(
        json.dumps({"read_only": True, "commands": [["python", "-c", "raise SystemExit(3)"]]}),
        encoding="utf-8",
    )
    result = ProductionReadOnlyVerifier().verify(_request(tmp_path))
    assert result.decision == "verification_failed"


def test_non_allowlisted_runner_is_unavailable(tmp_path):
    swarm = tmp_path / ".swarm"
    swarm.mkdir()
    (swarm / "verify.json").write_text(
        json.dumps({"read_only": True, "commands": [["powershell", "-c", "Write-Output ok"]]}),
        encoding="utf-8",
    )
    result = ProductionReadOnlyVerifier().verify(_request(tmp_path))
    assert result.decision == "verification_unavailable"


def test_static_node_project_gets_only_safe_inferred_checks(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "static-site", "scripts": {"build": "node build.js"}}),
        encoding="utf-8",
    )
    (tmp_path / "build.js").write_text("console.log('build');\n", encoding="utf-8")

    result = ProductionReadOnlyVerifier().verify(_request(tmp_path))

    assert result.decision == "verified"
    assert result.provenance["adapter"] == "production-read-only"
