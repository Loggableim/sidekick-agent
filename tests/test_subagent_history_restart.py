from pathlib import Path
from web.api.subagent_history import list_history, record, reconcile_stale

def test_restart_reconciles_stale_running_rows(tmp_path: Path):
    record(tmp_path, subagent_id="sa-run", session_id="chat", space_slug="aquarium-zentrum", status="running", summary="working")
    record(tmp_path, subagent_id="sa-wait", session_id="chat", space_slug="aquarium-zentrum", status="waiting", summary="waiting")
    changed = reconcile_stale(tmp_path)
    assert changed == 2
    rows = {row["subagent_id"]: row for row in list_history(tmp_path, session_id="chat")}
    assert rows["sa-run"]["status"] == "abandoned"
    assert rows["sa-run"]["summary"] == "server_restart"
