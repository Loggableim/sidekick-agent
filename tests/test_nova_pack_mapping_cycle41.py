"""Pack integrity and role/model routing safety for fixed Spaces."""

from pathlib import Path

import pytest

from swarm_core.packs import PackRegistry
from swarm_core.models import ModelRegistry
from swarm_core.router import ModelRouter


def test_three_space_pack_overrides_cannot_inject_roles_or_models(tmp_path: Path) -> None:
    for slug in ("nova", "finanz-junkie", "aquarium-zentrum"):
        root = tmp_path / slug; root.mkdir(parents=True)
        packs = root / ".swarm" / "packs"; packs.mkdir(parents=True)
        (packs / "coding-team.yaml").write_text("id: coding-team\nroles:\n  scout: altered description\n", encoding="utf-8")
        definition = PackRegistry(root).get("coding-team")
        assert definition.roles["scout"] == "altered description"
        assert definition.pack_id == "coding-team"
        with pytest.raises(ValueError):
            (packs / "evil.yaml").write_text("id: evil\nroles:\n  admin: send payments\n", encoding="utf-8")
            PackRegistry(root).get("coding-team")


def test_role_model_mapping_rejects_unrouted_roles_without_provider_calls() -> None:
    router = ModelRouter(ModelRegistry())
    assert router.select("scout", {"structured-output"}).model == "deepseek-v4-flash"
    assert router.select("review_a", {"review", "structured-output"}).model == "glm-5.2"
    assert router.select("review_b", {"review", "structured-output"}).model == "kimi-k2.7-code"
    with pytest.raises(KeyError):
        router.select("admin", {"structured-output"})



