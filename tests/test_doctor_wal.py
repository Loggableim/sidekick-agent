import sqlite3

from cli.doctor import _checkpoint_wal_truncate


def test_checkpoint_wal_truncate_shrinks_sqlite_wal(tmp_path):
    db_path = tmp_path / "state.db"
    wal_path = tmp_path / "state.db-wal"

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY)")
        conn.executemany(
            "INSERT INTO sessions (id) VALUES (?)",
            [(f"session-{i}",) for i in range(1000)],
        )
        conn.commit()
        assert wal_path.exists()
        assert wal_path.stat().st_size > 0

        old_size, new_size, checkpoint_result = _checkpoint_wal_truncate(db_path, wal_path)

        assert checkpoint_result is not None
        assert old_size > 0
        assert new_size < old_size
    finally:
        conn.close()
