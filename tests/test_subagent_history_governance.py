from pathlib import Path
import sqlite3
from web.api.subagent_history import record, list_history

def test_event_retry_is_idempotent_without_sequence_gaps(tmp_path: Path):
    record(tmp_path, subagent_id='sa', session_id='chat', space_slug='s', status='running', event_type='heartbeat', event_payload='same')
    record(tmp_path, subagent_id='sa', session_id='chat', space_slug='s', status='running', event_type='heartbeat', event_payload='same')
    record(tmp_path, subagent_id='sa', session_id='chat', space_slug='s', status='completed', event_type='completed', event_payload='ok')
    events = list_history(tmp_path, session_id='chat')[0]['events']
    assert [(e['sequence'], e['event_type']) for e in events] == [(1, 'heartbeat'), (2, 'completed')]

def test_run_retention_removes_orphan_events(tmp_path: Path):
    for i in range(1001):
        record(tmp_path, subagent_id=f'sa-{i}', session_id='chat', space_slug='s', status='completed', event_type='done', event_payload=str(i))
    with sqlite3.connect(tmp_path / 'subagents.db') as db:
        runs = db.execute('SELECT COUNT(*) FROM runs').fetchone()[0]
        orphan = db.execute('SELECT COUNT(*) FROM events e LEFT JOIN runs r ON r.subagent_id=e.subagent_id WHERE r.subagent_id IS NULL').fetchone()[0]
    assert runs == 1000
    assert orphan == 0
