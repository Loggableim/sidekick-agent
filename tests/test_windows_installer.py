from pathlib import Path


INSTALLER = Path(__file__).resolve().parents[1] / "install.ps1"


def test_installer_stops_processes_before_replacing_invalid_install_dir():
    script = INSTALLER.read_text(encoding="utf-8")

    invalid_repo_branch = script.index(
        "Existing directory at $InstallDir is not a valid git repo - replacing it."
    )
    helper_call = script.index("Remove-InstallDirSafely", invalid_repo_branch)
    next_clone_branch = script.index("if (-not $didUpdate)", invalid_repo_branch)

    assert helper_call < next_clone_branch


def test_installer_process_stop_matches_python_running_from_install_dir():
    script = INSTALLER.read_text(encoding="utf-8")

    stop_function = script[
        script.index("function Stop-RunningSidekickProcesses") :
        script.index("function Install-Dependencies")
    ]

    assert "Win32_Process" in stop_function
    assert "CommandLine.IndexOf($InstallDir" in stop_function
    assert "python.exe" in stop_function
    assert "pythonw.exe" in stop_function
