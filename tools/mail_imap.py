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
import re
import smtplib
import ssl
import socket
import time
from pathlib import Path
from typing import Any

import yaml

from shared.paths import sidekick_home
from web.api._home import get_active_webui_home

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_IMAP_CONNECT_TIMEOUT = 10  # seconds
_IMAP_READ_TIMEOUT = 30     # seconds
_SMTP_TIMEOUT = 15          # seconds
_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10 MB

_MAIL_PROVIDER_PRESETS: tuple[dict[str, Any], ...] = (
    {
        "name": "Gmail",
        "domains": {"gmail.com", "googlemail.com"},
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
        "use_ssl": True,
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_use_tls": True,
        "note": "Gmail braucht meist ein App-Passwort, wenn 2FA aktiv ist.",
    },
    {
        "name": "Outlook / Microsoft",
        "domains": {"outlook.com", "hotmail.com", "live.com", "msn.com", "office365.com"},
        "imap_host": "outlook.office365.com",
        "imap_port": 993,
        "use_ssl": True,
        "smtp_host": "smtp.office365.com",
        "smtp_port": 587,
        "smtp_use_tls": True,
        "note": "Microsoft-Konten brauchen oft IMAP im Webmail aktiviert.",
    },
    {
        "name": "iCloud",
        "domains": {"icloud.com", "me.com", "mac.com"},
        "imap_host": "imap.mail.me.com",
        "imap_port": 993,
        "use_ssl": True,
        "smtp_host": "smtp.mail.me.com",
        "smtp_port": 587,
        "smtp_use_tls": True,
        "note": "iCloud Mail benötigt oft ein App-spezifisches Passwort.",
    },
    {
        "name": "Yahoo",
        "domains": {"yahoo.com", "ymail.com", "rocketmail.com"},
        "imap_host": "imap.mail.yahoo.com",
        "imap_port": 993,
        "use_ssl": True,
        "smtp_host": "smtp.mail.yahoo.com",
        "smtp_port": 587,
        "smtp_use_tls": True,
        "note": "Yahoo benötigt meist ein App-Passwort.",
    },
    {
        "name": "Proton Mail",
        "domains": {"proton.me", "protonmail.com"},
        "imap_host": "127.0.0.1",
        "imap_port": 1143,
        "use_ssl": False,
        "smtp_host": "127.0.0.1",
        "smtp_port": 1025,
        "smtp_use_tls": False,
        "note": "Proton Mail benötigt den Proton Mail Bridge Dienst. Die Standard-IMAP/SMTP-Server sind nicht direkt nutzbar.",
    },
)

# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def _slugify_mail_account_id(email: str, fallback: str = "mail") -> str:
    local_part = str(email or "").split("@", 1)[0].strip().lower()
    local_part = re.sub(r"[^a-z0-9_-]+", "-", local_part)
    local_part = re.sub(r"-+", "-", local_part).strip("-")
    return local_part or fallback


def _mail_provider_for_domain(domain: str) -> dict[str, Any] | None:
    normalized = str(domain or "").strip().lower()
    if not normalized:
        return None
    for preset in _MAIL_PROVIDER_PRESETS:
        if normalized in preset["domains"]:
            return preset
    return None


def _build_inbox_from_email(
    email: str,
    password: str,
    *,
    account_id: str | None = None,
    label: str | None = None,
) -> dict[str, Any] | None:
    normalized_email = str(email or "").strip()
    if "@" not in normalized_email:
        return None
    local_part, domain = normalized_email.rsplit("@", 1)
    provider = _mail_provider_for_domain(domain)
    account_name = str(account_id or "").strip() or _slugify_mail_account_id(normalized_email)
    inbox_label = str(label or "").strip() or normalized_email

    if provider:
        imap_host = provider["imap_host"]
        imap_port = int(provider.get("imap_port", 993))
        use_ssl = bool(provider.get("use_ssl", True))
        smtp_host = provider["smtp_host"]
        smtp_port = int(provider.get("smtp_port", 587))
        smtp_use_tls = bool(provider.get("smtp_use_tls", True))
        provider_name = str(provider.get("name", "Mail"))
        note = str(provider.get("note", "")).strip()
        confidence = "high"
    else:
        imap_host = f"imap.{domain}"
        smtp_host = f"smtp.{domain}"
        imap_port = 993
        use_ssl = True
        smtp_port = 587
        smtp_use_tls = True
        provider_name = "IMAP/SMTP"
        note = "Für unbekannte Domains verwendet Sidekick generische IMAP/SMTP-Hostnamen. Falls der Login fehlschlägt, nutze die erweiterten Serverfelder."
        confidence = "fallback"

    inbox = {
        "id": account_name,
        "label": inbox_label,
        "default": True,
        "imap_host": imap_host,
        "imap_port": imap_port,
        "use_ssl": use_ssl,
        "imap_user": normalized_email,
        "imap_pass": password,
        "smtp_host": smtp_host,
        "smtp_port": smtp_port,
        "smtp_use_tls": smtp_use_tls,
        "smtp_user": normalized_email,
        "smtp_pass": password,
        "provider": provider_name,
        "confidence": confidence,
    }
    if note:
        inbox["note"] = note
    return inbox


def _load_legacy_mail_config_from_space_yaml(space_slug: str, home: Path | None = None) -> dict | None:
    base_home = Path(home).expanduser().resolve() if home else sidekick_home()
    space_yaml = base_home / "spaces" / space_slug / "space.yaml"
    if not space_yaml.exists():
        return None
    try:
        raw = yaml.safe_load(space_yaml.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        logger.debug("Failed to read space.yaml for %s: %s", space_slug, exc)
        return None
    if not isinstance(raw, dict):
        return None

    gmail_cfg = raw.get("gmail")
    if not isinstance(gmail_cfg, dict):
        return None
    accounts = gmail_cfg.get("accounts", {})
    if not isinstance(accounts, dict) or not accounts:
        return None

    inboxes: list[dict[str, Any]] = []
    for idx, (account_id, account_cfg) in enumerate(accounts.items()):
        if not isinstance(account_cfg, dict):
            continue
        email = str(account_cfg.get("email", "")).strip()
        password = str(account_cfg.get("password", "")).strip()
        if not email or not password:
            continue
        inbox = _build_inbox_from_email(
            email,
            password,
            account_id=str(account_id or "").strip() or None,
            label=str(account_cfg.get("label", "")).strip() or None,
        )
        if inbox:
            inbox["default"] = bool(account_cfg.get("default", idx == 0))
            inboxes.append(inbox)
    if not inboxes:
        return None
    return {"inboxes": inboxes, "source": "legacy_space_yaml"}


def _load_mail_config_from_env() -> dict | None:
    email = os.getenv("EMAIL_ADDRESS", "").strip()
    password = os.getenv("EMAIL_PASSWORD", "").strip()
    if not email or not password:
        return None

    imap_host = os.getenv("EMAIL_IMAP_HOST", "").strip()
    smtp_host = os.getenv("EMAIL_SMTP_HOST", "").strip()
    inbox = _build_inbox_from_email(email, password)
    if not inbox:
        return None
    if imap_host:
        inbox["imap_host"] = imap_host
    if smtp_host:
        inbox["smtp_host"] = smtp_host
    inbox["label"] = os.getenv("EMAIL_HOME_ADDRESS_NAME", "") or inbox["label"]
    inbox["source"] = "env"
    return {"inboxes": [inbox], "source": "env"}


def resolve_space_slug(kw: dict[str, Any] | None = None, *, default: str = "default") -> str:
    """Resolve the active workspace slug for mail tools.

    Resolution order:
    1. ``kw['user_task']`` from the tool framework
    2. ``SIDEKICK_WEBUI_ACTIVE_WORKSPACE``
    3. ``HERMES_WEBUI_ACTIVE_WORKSPACE`` for legacy compatibility
    4. ``default`` (usually ``"default"``)
    """
    kw = kw or {}
    slug = str(kw.get("user_task", "") or "").strip()
    if slug:
        return slug
    slug = os.getenv("SIDEKICK_WEBUI_ACTIVE_WORKSPACE", "").strip().lower()
    if slug:
        return slug
    slug = os.getenv("HERMES_WEBUI_ACTIVE_WORKSPACE", "").strip().lower()
    if slug:
        return slug
    return default


def _resolve_mail_home(home: Path | None = None) -> Path:
    """Return the mail home directory for the current request context.

    When *home* is provided, use it directly. Otherwise prefer the active
    WebUI home so mail tools follow the same profile/space resolution as the
    rest of the runtime.
    """
    if home is not None:
        return Path(home).expanduser().resolve()
    try:
        return Path(get_active_webui_home()).expanduser().resolve()
    except Exception:
        return sidekick_home()


def suggest_mail_config(
    email: str,
    password: str,
    *,
    account_id: str | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    """Build a one-inbox ``mail.json`` config from an email address.

    The caller provides the human inputs; this helper infers IMAP/SMTP hosts
    for common providers and falls back to generic ``imap.<domain>`` /
    ``smtp.<domain>`` hostnames otherwise.
    """
    inbox = _build_inbox_from_email(email, password, account_id=account_id, label=label)
    if not inbox:
        return {
            "success": False,
            "error": "Invalid email address",
            "config": {"inboxes": []},
        }

    warnings: list[str] = []
    if inbox.get("confidence") == "fallback":
        warnings.append(
            "Für diese Domain wurden generische IMAP/SMTP-Hostnamen verwendet. "
            "Falls der Login fehlschlägt, öffne die erweiterten Serverfelder."
        )

    return {
        "success": True,
        "provider": inbox.get("provider", "Mail"),
        "domain": str(email).strip().split("@", 1)[-1].lower(),
        "warnings": warnings,
        "config": {"inboxes": [inbox]},
    }


def get_space_config(space_slug: str, home: Path | None = None) -> dict | None:
    """Load ``mail.json`` from the given space directory.

    Returns the parsed JSON dict (with an ``inboxes`` list) or a synthesized
    config from legacy Gmail config / email env vars.  ``None`` is returned
    only when no usable config can be found.
    """
    base_home = _resolve_mail_home(home)
    path = base_home / "spaces" / space_slug / "mail.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("inboxes"), list):
                return data
            logger.warning("mail.json in %s has no 'inboxes' list", space_slug)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read mail.json for %s: %s", space_slug, exc)

    legacy = _load_legacy_mail_config_from_space_yaml(space_slug, base_home)
    if legacy:
        return legacy

    env_cfg = _load_mail_config_from_env()
    if env_cfg:
        return env_cfg

    logger.warning("No mail config found for space %s", space_slug)
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
