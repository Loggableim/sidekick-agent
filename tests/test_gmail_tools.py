import imaplib

import pytest


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


@pytest.mark.parametrize("operation", ["send", "validate"])
def test_mail_imap_plain_smtp_uses_non_ssl_transport_when_tls_is_disabled(monkeypatch, operation):
    from tools import mail_imap

    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            calls.append(("SMTP", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def login(self, user, password):
            calls.append(("login", user, password))

        def sendmail(self, user, to_addrs, message):
            calls.append(("sendmail", user, tuple(to_addrs), message))

    class FakeSMTPSSL:
        def __init__(self, *args, **kwargs):
            raise AssertionError("SMTP_SSL must not be used when smtp_use_tls is False")

    monkeypatch.setattr(mail_imap.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(mail_imap.smtplib, "SMTP_SSL", FakeSMTPSSL)

    inbox = {
        "smtp_host": "127.0.0.1",
        "smtp_port": 1025,
        "smtp_use_tls": False,
        "smtp_user": "user@example.org",
        "smtp_pass": "secret",
    }

    if operation == "send":
        result = mail_imap.send_mail(inbox, ["to@example.org"], "Subject: Test\n\nBody")
        assert result["success"] is True
        assert any(call[0] == "sendmail" for call in calls)
    else:
        mail_imap.validate_smtp(inbox)
        assert any(call[0] == "login" for call in calls)

    assert calls[0][0] == "SMTP"


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


def test_mail_imap_cache_distinguishes_port_and_ssl_mode(monkeypatch):
    from tools import mail_imap

    mail_imap.flush_imap_cache()
    created = []

    class FakeIMAP:
        def __init__(self, kind, host, port, **kwargs):
            self.kind = kind
            self.host = host
            self.port = port
            self.kwargs = kwargs
            self.noop_count = 0
            self.closed = False
            created.append(self)

        def noop(self):
            self.noop_count += 1
            return "OK", [b"alive"]

        def close(self):
            self.closed = True

        def logout(self):
            self.closed = True

        def login(self, user, password):
            return "OK", [b"logged in"]

    monkeypatch.setattr(
        mail_imap.imaplib,
        "IMAP4_SSL",
        lambda host, port, timeout=None, ssl_context=None: FakeIMAP("ssl", host, port, timeout=timeout, ssl_context=ssl_context),
    )
    monkeypatch.setattr(
        mail_imap.imaplib,
        "IMAP4",
        lambda host, port, timeout=None: FakeIMAP("plain", host, port, timeout=timeout),
    )

    first = mail_imap.get_imap(
        {
            "imap_host": "imap.example.org",
            "imap_port": 993,
            "imap_user": "user@example.org",
            "imap_pass": "secret",
            "use_ssl": True,
        }
    )
    second = mail_imap.get_imap(
        {
            "imap_host": "imap.example.org",
            "imap_port": 1143,
            "imap_user": "user@example.org",
            "imap_pass": "secret",
            "use_ssl": True,
        }
    )

    assert first is not second
    assert len(created) == 2
    assert created[0].port == 993
    assert created[1].port == 1143


def test_mail_imap_cache_distinguishes_password_changes(monkeypatch):
    from tools import mail_imap

    mail_imap.flush_imap_cache()
    created = []

    class FakeIMAP:
        def __init__(self, host, port, **kwargs):
            self.host = host
            self.port = port
            self.kwargs = kwargs
            self.noop_count = 0
            created.append(self)

        def noop(self):
            self.noop_count += 1
            return "OK", [b"alive"]

        def close(self):
            return None

        def logout(self):
            return None

        def login(self, user, password):
            self.user = user
            self.password = password
            return "OK", [b"logged in"]

    monkeypatch.setattr(
        mail_imap.imaplib,
        "IMAP4_SSL",
        lambda host, port, timeout=None, ssl_context=None: FakeIMAP(host, port, timeout=timeout, ssl_context=ssl_context),
    )

    first = mail_imap.get_imap(
        {
            "imap_host": "imap.example.org",
            "imap_port": 993,
            "imap_user": "user@example.org",
            "imap_pass": "first-secret",
            "use_ssl": True,
        }
    )
    second = mail_imap.get_imap(
        {
            "imap_host": "imap.example.org",
            "imap_port": 993,
            "imap_user": "user@example.org",
            "imap_pass": "second-secret",
            "use_ssl": True,
        }
    )

    assert first is not second
    assert len(created) == 2
    assert created[0].password == "first-secret"
    assert created[1].password == "second-secret"
