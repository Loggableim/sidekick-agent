import types

from runtime import display


def test_display_accepts_dict_skin(monkeypatch):
    skin = {
        "tool_prefix": "|",
        "tool_emojis": {"terminal": "T"},
        "spinner": {
            "waiting_faces": ["wait"],
            "thinking_faces": ["think"],
            "thinking_verbs": ["reason"],
            "wings": [["<", ">"]],
        },
        "colors": {
            "banner_dim": "#010203",
            "session_label": "#040506",
            "session_border": "#070809",
            "ui_error": "#ef5350",
            "ui_ok": "#4caf50",
        },
    }

    monkeypatch.setattr(display, "_get_skin", lambda: skin)

    assert display.get_skin_tool_prefix() == "|"
    assert display.get_tool_emoji("terminal") == "T"
    assert display.KawaiiSpinner.get_waiting_faces() == ["wait"]
    assert display.KawaiiSpinner.get_thinking_faces() == ["think"]
    assert display.KawaiiSpinner.get_thinking_verbs() == ["reason"]
    assert display._skin_get_spinner_wings(skin) == [("<", ">")]
    assert display.get_cute_tool_message("terminal", {"command": "pwd"}, 0.1, "{}").startswith("|")


def test_diff_colors_accept_dict_skin(monkeypatch):
    skin_module = types.SimpleNamespace(
        get_active_skin=lambda: {
            "colors": {
                "banner_dim": "#010203",
                "session_label": "#040506",
                "session_border": "#070809",
                "ui_error": "#ef5350",
                "ui_ok": "#4caf50",
            }
        }
    )
    monkeypatch.setitem(__import__("sys").modules, "sidekick_cli.skin_engine", skin_module)
    monkeypatch.setattr(display, "_diff_colors_cached", None)

    colors = display._diff_ansi()

    assert colors["dim"].startswith("\033[38;2;1;2;3m")
    assert colors["file"].startswith("\033[38;2;4;5;6m")
    assert colors["hunk"].startswith("\033[38;2;7;8;9m")
