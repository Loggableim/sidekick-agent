import sys


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
