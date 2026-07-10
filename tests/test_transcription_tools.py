import sys
from types import SimpleNamespace
from pathlib import Path


def test_local_stt_command_handles_windows_paths_with_spaces(monkeypatch, tmp_path):
    from tools import transcription_tools

    audio_path = tmp_path / "audio sample.wav"
    audio_path.write_bytes(b"RIFF")
    script = (
        "from pathlib import Path; import sys; "
        "(Path(sys.argv[2]) / 'transcript.txt').write_text("
        "Path(sys.argv[1]).name, encoding='utf-8')"
    )

    monkeypatch.setenv(
        "SIDEKICK_LOCAL_STT_COMMAND",
        f'"{sys.executable}" -c "{script}" {{input_path}} {{output_dir}}',
    )

    result = transcription_tools._transcribe_local_command(str(audio_path), "base")

    assert result == {
        "success": True,
        "transcript": "audio sample.wav",
        "provider": "local_command",
    }


def test_prepare_local_audio_uses_utf8_text_mode(monkeypatch, tmp_path):
    from tools import transcription_tools

    audio_path = tmp_path / "clip.flac"
    audio_path.write_bytes(b"fLaC")
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        converted = command[-1]
        Path(converted).write_bytes(b"RIFF")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(transcription_tools, "_find_ffmpeg_binary", lambda: "ffmpeg")
    monkeypatch.setattr(transcription_tools.subprocess, "run", fake_run)

    converted_path, error = transcription_tools._prepare_local_audio(str(audio_path), str(tmp_path))

    assert error is None
    assert converted_path and converted_path.endswith(".wav")
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "replace"


def test_transcribe_local_command_uses_utf8_text_mode(monkeypatch, tmp_path):
    from tools import transcription_tools

    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"RIFF")
    captured = {}
    output_dir = tmp_path / "stt-output"

    class _TempDir:
        def __init__(self, prefix: str):
            self.name = str(output_dir)

        def __enter__(self):
            output_dir.mkdir(parents=True, exist_ok=True)
            return str(output_dir)

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        if kwargs.get("shell"):
            (output_dir / "transcript.txt").write_text("hello from stt", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setenv(
        "SIDEKICK_LOCAL_STT_COMMAND",
        f'"{sys.executable}" -c "print(1)" {{input_path}} {{output_dir}}',
    )
    monkeypatch.setattr(transcription_tools.tempfile, "TemporaryDirectory", _TempDir)
    monkeypatch.setattr(transcription_tools.subprocess, "run", fake_run)

    result = transcription_tools._transcribe_local_command(str(audio_path), "base")

    assert result == {
        "success": True,
        "transcript": "hello from stt",
        "provider": "local_command",
    }
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "replace"
