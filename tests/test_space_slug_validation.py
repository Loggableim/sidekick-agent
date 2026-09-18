"""Regression tests for space slug validation on creation.

Bug (verified live on master 2026-09-14): ``delete_space`` validates the
slug (``_is_valid_space_slug``) but ``create_space`` and
``get_or_create_space`` did not - a slug like ``../../evil`` flowed into
``Space.root`` (``_spaces_root() / slug``) and ``mkdir(parents=True)``
created directories outside the Spaces root. The API layer regex-checks
``/api/space/create`` slugs, but both creators are also reachable from
``get_or_create_space`` callers without that check.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def isolated_spaces(monkeypatch, tmp_path):
    import web.api.space_engine as space_engine

    spaces_root = tmp_path / "spaces"
    monkeypatch.setattr(space_engine, "SPACES_ROOT", spaces_root, raising=False)
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "legacy", raising=False)
    monkeypatch.setattr(space_engine, "_SPACE_CACHE", None, raising=False)
    monkeypatch.setattr(space_engine, "_SPACE_CACHE_ROOTS", None, raising=False)
    monkeypatch.setattr(space_engine, "_SPACE_CACHE_TS", 0.0, raising=False)
    return space_engine, spaces_root


@pytest.mark.parametrize("bad", ["../../evil", "..", ".", "a/b", "a\\b", ".hidden"])
def test_create_space_rejects_traversal_slug(isolated_spaces, bad):
    space_engine, spaces_root = isolated_spaces
    from web.api.space_engine import SpaceError

    with pytest.raises(SpaceError, match="invalid space slug"):
        space_engine.create_space(bad, "Evil")
    # Nothing may be created outside the Spaces root.
    assert not (spaces_root.parent / "evil").exists()


@pytest.mark.parametrize("bad", ["../../evil", "..", ".", "a/b", "a\\b", ".hidden"])
def test_get_or_create_space_rejects_traversal_slug(isolated_spaces, bad):
    space_engine, spaces_root = isolated_spaces
    from web.api.space_engine import SpaceError

    with pytest.raises(SpaceError, match="invalid space slug"):
        space_engine.get_or_create_space(bad, "Evil")
    assert not (spaces_root.parent / "evil").exists()


def test_create_space_still_accepts_valid_slugs(isolated_spaces):
    space_engine, spaces_root = isolated_spaces

    space = space_engine.create_space("valid-slug_1", "Valid")

    assert space.root == spaces_root / "valid-slug_1"
    assert (spaces_root / "valid-slug_1" / "space.yaml").exists()


def test_get_or_create_space_still_accepts_valid_slugs(isolated_spaces):
    space_engine, spaces_root = isolated_spaces

    space = space_engine.get_or_create_space("valid-slug_2", "Valid Two")

    assert space.root == spaces_root / "valid-slug_2"
    assert (spaces_root / "valid-slug_2" / "space.yaml").exists()


def test_fs_scan_skips_invalid_slug_dirs(isolated_spaces):
    """Directories whose names are not valid slugs must not be advertised as spaces.

    Bug (verified live 2026-09-19): ``_scan_fs_for_spaces()`` turned every
    directory under ``spaces/`` into a Space without validating the slug, so
    ``_bewusstsein_archived_20260605`` (archive) and ``_bridge``
    (inter-space infrastructure) appeared in the UI space list while every
    per-space API call for them raised ``SpaceError: invalid space slug``
    -> HTTP 400 -> console errors on every page load (browser_webui_smoke
    ``no_console_errors_or_warnings`` failure, run-9121 2026-09-19 00:51).
    """
    space_engine, spaces_root = isolated_spaces
    (spaces_root / "_bridge").mkdir(parents=True)
    (spaces_root / "_bewusstsein_archived_20260605").mkdir()
    (spaces_root / "nova").mkdir()

    slugs = [s.slug for s in space_engine.get_all_spaces()]

    assert "nova" in slugs
    assert "_bridge" not in slugs
    assert "_bewusstsein_archived_20260605" not in slugs