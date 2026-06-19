from __future__ import annotations

import py_compile
from pathlib import Path


LAUNCHERS = [
    Path(r"C:\sidekick\home\scripts\dashboard_launcher.py"),
    Path(r"C:\sidekick\home\scripts\dashboard_launcher_debug.py"),
]


def test_hub_launchers_are_valid_python():
    for launcher in LAUNCHERS:
        py_compile.compile(str(launcher), doraise=True)


def test_hub_launchers_reexec_into_sidekick_venv_when_runtime_is_missing():
    for launcher in LAUNCHERS:
        source = launcher.read_text(encoding="utf-8")

        assert "REQUIRED_MODULES" in source
        assert '"faster_whisper"' in source
        assert '"vosk"' in source
        assert '"psutil"' in source
        assert 'VENV_PYTHON = REPO_DIR / ".venv" / "Scripts" / "python.exe"' in source
        assert "os.execv(str(VENV_PYTHON)" in source
        assert "_ensure_runtime()" in source
        assert source.index("_ensure_runtime()") < source.index("import dashboard_server")
