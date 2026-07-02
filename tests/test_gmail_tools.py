import imaplib


def test_gmail_read_only_helpers_degrade_imap_connection_errors(monkeypatch):
    from web.api import gmail_tools

    def fail_connect(account):
        raise imaplib.IMAP4.error(b"[ALERT] Too many simultaneous connections. (Failure)")

    monkeypatch.setattr(gmail_tools, "_connect_imap", fail_connect)

    listed = gmail_tools._list_emails(1, "INBOX", "dominik")
    searched = gmail_tools._search_emails("from:example@example.com", 1, "dominik")
    folders = gmail_tools._list_folders("dominik")

    assert listed["error"] == "IMAP list failed"
    assert listed["emails"] == []
    assert listed["count"] == 0
    assert searched["error"] == "IMAP search failed"
    assert searched["emails"] == []
    assert searched["count"] == 0
    assert folders["error"] == "Failed to list folders"
    assert folders["folders"] == []


def test_gmail_folders_decode_imap_modified_utf7(monkeypatch):
    from web.api import gmail_tools

    class FakeImap:
        def list(self):
            return "OK", [
                b'(\\HasNoChildren) "/" "[Gmail]/Entw&APw-rfe"',
                b'(\\HasNoChildren) "/" "A &- B"',
            ]

    monkeypatch.setattr(gmail_tools, "_connect_imap", lambda account: (FakeImap(), "user@example.com"))

    result = gmail_tools._list_folders("dominik")
    names = [folder["name"] for folder in result["folders"]]

    assert "[Gmail]/Entwürfe" in names
    assert "A & B" in names


def test_gmail_list_encodes_unicode_folder_for_imap_select(monkeypatch):
    from web.api import gmail_tools

    selected = []

    class FakeImap:
        def select(self, folder):
            selected.append(folder)
            return "OK", [b"1"]

        def uid(self, command, *args):
            assert command == "search"
            return "OK", [b""]

    monkeypatch.setattr(gmail_tools, "_connect_imap", lambda account: (FakeImap(), "user@example.com"))

    result = gmail_tools._list_emails(1, "[Gmail]/Entwürfe", "dominik")

    assert selected == ["[Gmail]/Entw&APw-rfe"]
    assert result["folder"] == "[Gmail]/Entwürfe"
    assert result["emails"] == []


def test_gmail_ai_stream_is_blocked_in_game_mode(monkeypatch, tmp_path):
    from web.api import config as cfg
    from web.api import gmail_tools

    monkeypatch.setattr(cfg, "SETTINGS_FILE", tmp_path / "settings.json")
    cfg.save_settings({"game_mode_enabled": True})
    monkeypatch.setattr(
        gmail_tools.urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Ollama must not be called in Game Mode")),
    )

    chunks = list(gmail_tools._ai_call_stream("Summarize this email"))

    assert chunks == [
        (
            "Game Mode is active. Gmail AI is blocked so local GPU/VRAM stays free.",
            True,
        )
    ]


def test_gmail_related_only_strips_reply_or_forward_prefixes(monkeypatch):
    from web.api import gmail_tools

    monkeypatch.setattr(gmail_tools, "_ai_cache_get", lambda key: None)
    monkeypatch.setattr(gmail_tools, "_ai_cache_set", lambda key, value: None)
    monkeypatch.setattr(
        gmail_tools,
        "_read_email",
        lambda email_id, account: {
            "id": email_id,
            "subject": "Review",
            "from": "alice@example.com",
        },
    )
    monkeypatch.setattr(
        gmail_tools,
        "_list_emails",
        lambda max_r, folder, account: {
            "emails": [
                {"id": "2", "subject": "Product view", "from": "bob@example.net"},
                {"id": "3", "subject": "Re: Review", "from": "bob@example.net"},
            ]
        },
    )

    related = gmail_tools._ai_find_related("1", "dominik")

    assert [item["id"] for item in related] == ["3"]
