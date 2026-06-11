from __future__ import annotations

import sqlite3


def _make_legacy_home(tmp_path):
    src = tmp_path / "hermes-home"
    src.mkdir()
    (src / "spaces" / "alpha").mkdir(parents=True)
    (src / "spaces" / "beta").mkdir(parents=True)
    (src / "webui").mkdir()
    (src / "webui" / "workspaces.json").write_text(
        '[{"path":"C:\\\\work","name":"Work"}]',
        encoding="utf-8",
    )
    (src / "webui" / "settings.json").write_text('{"bot_name":"Nova"}', encoding="utf-8")
    (src / "auth.json").write_text('{"providers":[]}', encoding="utf-8")
    (src / ".env").write_text("DUMMY_ENV_VALUE=secret\n", encoding="utf-8")
    conn = sqlite3.connect(src / "state.db")
    conn.executescript(
        """
        CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, started_at REAL);
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp REAL
        );
        INSERT INTO sessions (id, title, started_at) VALUES ('s1', NULL, 1);
        INSERT INTO messages (session_id, role, content, timestamp)
        VALUES ('s1', 'user', 'Repair Sidekick state split', 2);
        """
    )
    conn.commit()
    conn.close()
    return src


def test_local_state_repair_dry_run_counts_without_copying(tmp_path):
    from cli.local_state_repair import build_repair_plan

    src = _make_legacy_home(tmp_path)
    dst = tmp_path / "sidekick-home"

    plan = build_repair_plan(src, dst)

    assert plan.apply is False
    assert plan.source == src
    assert plan.target == dst
    assert plan.counts["spaces"] == 2
    assert plan.counts["state_sessions"] == 1
    assert plan.counts["state_messages"] == 1
    assert plan.counts["webui_files"] == 2
    assert plan.counts["secret_files"] == 2
    assert not (dst / "spaces").exists()


def test_local_state_repair_apply_backs_up_and_preserves_conflicts(tmp_path):
    from cli.local_state_repair import apply_repair_plan, build_repair_plan

    src = _make_legacy_home(tmp_path)
    dst = tmp_path / "sidekick-home"
    (dst / "spaces" / "alpha").mkdir(parents=True)
    (dst / "spaces" / "alpha" / "keep.txt").write_text("existing", encoding="utf-8")

    plan = build_repair_plan(src, dst, apply=True)
    result = apply_repair_plan(plan)

    assert result.backup_path is not None
    assert result.backup_path.exists()
    assert (dst / "spaces" / "alpha" / "keep.txt").read_text(encoding="utf-8") == "existing"
    assert (dst / "spaces" / "beta").is_dir()
    assert (dst / "webui" / "workspaces.json").exists()
    assert (dst / "state.db").exists()
    assert any("spaces/alpha" in item for item in result.skipped)


def test_local_state_repair_rejects_known_bad_yaml_without_leaking_secret(tmp_path):
    from cli.local_state_repair import build_repair_plan

    src = _make_legacy_home(tmp_path)
    (src / "config.yaml").write_text(
        "\n".join(
            [
                "custom_providers:",
                "  ollama-cloud:",
                "    base_url: https://ollama.com/v1",
                "    model: deepseek-v4-flash",
                "key_env: SUPER_SECRET_TOKEN",
                "  opencode-go:",
                "    base_url: https://opencode.ai/zen/go/v1",
            ]
        ),
        encoding="utf-8",
    )

    plan = build_repair_plan(src, tmp_path / "sidekick-home")

    assert plan.config_status.startswith("invalid")
    assert "SUPER_SECRET_TOKEN" not in "\n".join(plan.warnings)
    assert any("config.yaml" in warning for warning in plan.warnings)
