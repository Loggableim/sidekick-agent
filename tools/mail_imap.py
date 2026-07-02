"""Shared IMAP/SMTP helper for space-scoped mail tools.

Reads ``mail.json`` from the active space directory and provides
connection helpers for IMAP (imaplib) and SMTP (smtplib).

Each space under ``C:\\sidekick\\home\\spaces\\<slug>\\`` can have its own
``mail.json`` with one or more inbox configurations.

Usage::

    from tools.mail_imap import get_space_config, get_imap, release_imap

    config = get_space_config("nova")
    if config:
        conn = get_imap(config, "gmail-work")
        # ... use conn ...
        release_imap(conn)
"""

from __future__ import annotations

import imaplib
import json
import logging
import os
import smtplib
import ssl
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SIDEKICK_HOME = Path(
    os.environ.get("SIDEKICK_HOME")
    or os.environ.get("HERMES_HOME")
    or Path.home() / ".sidekick"
)

_IMAP_CONNECT_TIMEOUT = 10  # seconds
_IMAP_READ_TIMEOUT = 30     # seconds
_SMTP_TIMEOUT = 15          # seconds
_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10 MB

# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def get_space_config(space_slug: str) -> dict | None:
    """Load ``mail.json`` from the given space directory.

    Returns the parsed JSON dict (with an ``inboxes`` list) or ``None``
    if the file does not exist or is invalid.
    """
    path = _SIDEKICK_HOME / "spaces" / space_slug / "mail.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("inboxes"), list):
            return data
        logger.warning("mail.json in %s has no 'inboxes' list", space_slug)
        return None
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read mail.json for %s: %s", space_slug, exc)
        return None


def get_inbox_config(space_slug: str, inbox_id: str | None = None) -> dict | None:
    """Return a single inbox config from the space's ``mail.json``.

    If *inbox_id* is ``None``, returns the first inbox marked as ``default``,
    or the first inbox if no default is set.
    """
    config = get_space_config(space_slug)
    if not config:
        return None

    inboxes = config.get("inboxes", [])

    if inbox_id:
        for ib in inboxes:
            if ib.get("id") == inbox_id:
                return ib
        logger.warning("Inbox '%s' not found in space '%s'", inbox_id, space_slug)
        return None

    # No inbox_id given — try default, then first
    for ib in inboxes:
        if ib.get("default"):
            return ib
    return inboxes[0] if inboxes else None


def list_inboxes(space_slug: str) -> list[dict]:
    """Return all inbox configs for a space, with unread counts (or 0)."""
    config = get_space_config(space_slug)
    if not config:
        return []
    inboxes = config.get("inboxes", [])
    result = []
    for ib in inboxes:
        result.append({
            "id": ib.get("id", ""),
            "label": ib.get("label", ib.get("id", "Unnamed")),
            "default": ib.get("default", False),
            "unread": 0,  # filled lazily
        })
    return result


# ---------------------------------------------------------------------------
# IMAP connection helpers
# ---------------------------------------------------------------------------

# Simple connection cache: { (host, user) -> (conn, timestamp) }
_imap_cache: dict[tuple[str, str], tuple[imaplib.IMAP4, float]] = {}
_imap_cache_ttl = 300  # 5 minutes


def get_imap(inbox: dict) -> imaplib.IMAP4:
    """Open (or reuse) an IMAP SSL connection for the given inbox config.

    Required inbox keys: ``imap_host``, ``imap_port``, ``imap_user``,
    ``imap_pass``.  Optional: ``use_ssl`` (default ``True``).

    Returns an authenticated ``imaplib.IMAP4`` instance.
    """
    host = inbox["imap_host"]
    port = int(inbox.get("imap_port", 993))
    user = inbox["imap_user"]
    password = inbox["imap_pass"]
    use_ssl = inbox.get("use_ssl", True)

    cache_key = (host, user)

    # Check cache
    cached = _imap_cache.get(cache_key)
    if cached:
        conn, ts = cached
        if (time.time() - ts) < _imap_cache_ttl:
            try:
                # Quick noop to verify connection is alive
                conn.noop()
                return conn
            except (imaplib.IMAP4.error, OSError):
                logger.info("IMAP connection stale, reconnecting: %s@%s", user, host)
                _imap_cache.pop(cache_key, None)

    # Open new connection
    if use_ssl:
        context = ssl.create_default_context()
        conn = imaplib.IMAP4_SSL(
            host, port,
            timeout=_IMAP_CONNECT_TIMEOUT,
            ssl_context=context,
        )
    else:
        conn = imaplib.IMAP4(host, port, timeout=_IMAP_CONNECT_TIMEOUT)
        conn.starttls()

    conn.login(user, password)
    _imap_cache[cache_key] = (conn, time.time())
    return conn


def release_imap(conn: imaplib.IMAP4 | None) -> None:
    """Close an IMAP connection gracefully.  No-op if ``None``."""
    if conn is None:
        return
    try:
        conn.close()
        conn.logout()
    except Exception:
        pass


def flush_imap_cache() -> None:
    """Close and clear all cached IMAP connections."""
    for (host, user), (conn, _) in list(_imap_cache.items()):
        try:
            conn.close()
            conn.logout()
        except Exception:
            pass
    _imap_cache.clear()


# ---------------------------------------------------------------------------
# SMTP connection helpers
# ---------------------------------------------------------------------------


def send_mail(inbox: dict, to_addrs: list[str], message: str) -> dict:
    """Send an email via SMTP using the given inbox config.

    Required inbox keys: ``smtp_host``, ``smtp_port``, ``smtp_user``,
    ``smtp_pass``.  *message* must be a fully-formed RFC 5322 message
    (use ``email.message.EmailMessage`` to build it).

    Returns ``{"success": True}`` or ``{"success": False, "error": "..."}``.
    """
    host = inbox.get("smtp_host")
    port = int(inbox.get("smtp_port", 587))
    user = inbox.get("smtp_user", inbox.get("imap_user", ""))
    password = inbox.get("smtp_pass", inbox.get("imap_pass", ""))
    use_tls = inbox.get("smtp_use_tls", True)

    if not host:
        return {"success": False, "error": "SMTP not configured for this inbox"}

    try:
        if use_tls:
            context = ssl.create_default_context()
            with smtplib.SMTP(host, port, timeout=_SMTP_TIMEOUT) as server:
                server.starttls(context=context)
                server.login(user, password)
                server.sendmail(user, to_addrs, message)
        else:
            with smtplib.SMTP_SSL(host, port, timeout=_SMTP_TIMEOUT) as server:
                server.login(user, password)
                server.sendmail(user, to_addrs, message)

        return {"success": True}

    except Exception as exc:
        logger.exception("SMTP send failed for %s", user)
        return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Mail parsing helpers
# ---------------------------------------------------------------------------


def decode_mail_body(msg) -> str:
    """Extract plain-text body from an ``email.message.Message``.

    Falls back to HTML if no plain text part exists.
    """
    import email
    from email import policy

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            cdisp = str(part.get("Content-Disposition", ""))
            if "attachment" in cdisp:
                continue
            if ctype == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        return payload.decode(charset, errors="replace")
                    except (LookupError, UnicodeDecodeError):
                        return payload.decode("utf-8", errors="replace")
        # No plain text — try HTML
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        return payload.decode(charset, errors="replace")
                    except (LookupError, UnicodeDecodeError):
                        return payload.decode("utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                return payload.decode(charset, errors="replace")
            except (LookupError, UnicodeDecodeError):
                return payload.decode("utf-8", errors="replace")

    return "(no content)"


def parse_mail_summary(msg, uid: str | None = None) -> dict:
    """Extract a summary dict from an email message.

    Returns::

        {
            "uid": "...",
            "subject": "...",
            "from": "...",
            "date": "...",
            "snippet": "...",
            "flags": [...],
        }
    """
    import email.utils

    subject = str(msg.get("Subject", "(no subject)"))
    from_ = str(msg.get("From", "(unknown)"))
    date_str = str(msg.get("Date", ""))
    # Parse + reformat date
    parsed_date = email.utils.parsedate_to_datetime(date_str) if date_str else None
    date_iso = parsed_date.isoformat() if parsed_date else date_str

    # Snippet: first 200 chars of body
    body = decode_mail_body(msg)
    snippet = body.strip()[:200].replace("\n", " ").replace("\r", "")

    flags_raw = msg.get("Flags", "")
    flags = [f.strip().strip("()") for f in flags_raw.split() if f.strip()]

    return {
        "uid": uid or "",
        "subject": subject,
        "from": from_,
        "date": date_iso,
        "snippet": snippet,
        "flags": flags,
    }
