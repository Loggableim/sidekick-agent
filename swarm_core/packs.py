"""Strict declarative Swarm pack loading with safe project metadata overrides."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import yaml


DEFAULT_PACK_IDS = (
    "coding-team",
    "bug-hunt",
    "research-team",
    "release-audit",
)
_PACK_KEYS = frozenset({"id", "description", "workflow", "roles"})


@dataclass(frozen=True)
class PackDefinition:
    """Role/workflow metadata only; never a source of model or policy authority."""

    pack_id: str
    description: str
    workflow: str
    roles: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "roles", MappingProxyType(dict(self.roles)))


class PackRegistry:
    """Load exactly the shipped packs, then apply narrow project-local metadata."""

    def __init__(self, project_root: Path | None = None) -> None:
        definitions = {
            pack_id: _load_default_pack(pack_id) for pack_id in DEFAULT_PACK_IDS
        }
        if project_root is not None:
            root = Path(project_root).resolve()
            override_dir = root / ".swarm" / "packs"
            if override_dir.exists():
                if not override_dir.is_dir():
                    raise ValueError("Swarm pack override path must be a directory")
                for path in sorted(override_dir.glob("*.yaml")):
                    pack_id = path.stem
                    if pack_id not in definitions:
                        raise ValueError(f"Unknown Swarm pack override: {pack_id}")
                    definitions[pack_id] = _apply_project_override(
                        definitions[pack_id],
                        _load_yaml(path),
                    )
        self._definitions = definitions

    def list(self) -> tuple[PackDefinition, ...]:
        return tuple(self._definitions[pack_id] for pack_id in DEFAULT_PACK_IDS)

    def get(self, pack_id: str) -> PackDefinition:
        if not isinstance(pack_id, str) or not pack_id.strip():
            raise ValueError("Swarm pack id must be a non-empty string")
        try:
            return self._definitions[pack_id.strip()]
        except KeyError as exc:
            raise ValueError(f"Unknown Swarm pack: {pack_id}") from exc


def _load_default_pack(pack_id: str) -> PackDefinition:
    resource = resources.files("swarm_core").joinpath("packs", f"{pack_id}.yaml")
    if not resource.is_file():
        raise ValueError(f"Missing packaged Swarm pack: {pack_id}")
    with resource.open("r", encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    if not isinstance(document, dict) or set(document) != _PACK_KEYS:
        raise ValueError(f"Malformed packaged Swarm pack: {pack_id}")
    if document.get("id") != pack_id:
        raise ValueError(f"Pack id does not match packaged file: {pack_id}")
    return PackDefinition(
        pack_id=pack_id,
        description=_require_text(document["description"], "Pack description"),
        workflow=_require_text(document["workflow"], "Pack workflow"),
        roles=_parse_roles(document["roles"]),
    )


def _load_yaml(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ValueError(f"Malformed Swarm pack YAML: {path.name}") from exc


def _apply_project_override(
    base: PackDefinition,
    document: Any,
) -> PackDefinition:
    if not isinstance(document, dict):
        raise ValueError(f"Malformed Swarm pack override: {base.pack_id}")
    unknown_keys = set(document) - _PACK_KEYS
    if unknown_keys:
        raise ValueError(
            f"Unsafe Swarm pack override keys: {', '.join(sorted(unknown_keys))}"
        )
    if not document or not (set(document) - {"id"}):
        raise ValueError(f"Malformed Swarm pack override: {base.pack_id}")
    if "id" in document and document["id"] != base.pack_id:
        raise ValueError(f"Swarm pack override id mismatch: {base.pack_id}")

    description = base.description
    if "description" in document:
        description = _require_text(document["description"], "Pack description")
    workflow = base.workflow
    if "workflow" in document:
        workflow = _require_text(document["workflow"], "Pack workflow")
    roles = dict(base.roles)
    if "roles" in document:
        roles.update(_parse_roles(document["roles"]))
    return PackDefinition(
        pack_id=base.pack_id,
        description=description,
        workflow=workflow,
        roles=roles,
    )


def _parse_roles(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or not value:
        raise ValueError("Swarm pack roles must be a non-empty mapping")
    parsed: dict[str, str] = {}
    for role, description in value.items():
        parsed[_require_text(role, "Pack role")] = _require_text(
            description,
            "Pack role description",
        )
    return parsed


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()
