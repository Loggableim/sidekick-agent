"""
Gmail API — Sidekick backend for Gmail operations.
Clean IMAP/SMTP access with RFC 2047 decoding.
No ctypes DNS patch needed — server Python has working DNS.
Multi-Account support: account query parameter on all endpoints.
"""
import base64
import json
import imaplib
import smtplib
import email
import logging
import os
import re
import threading
import time
import hashlib
import urllib.request
import urllib.error
from email.message import EmailMessage
from email.header import decode_header
from datetime import datetime, timedelta
from urllib.parse import parse_qs
from web.api.helpers import j, bad

_REQUEST_WORKSPACE_LOCAL = threading.local()

# ── Load .env for Gmail passwords (fallback if not in system env) ──
_env_loaded = False
def _load_env():
    global _env_loaded
    if _env_loaded:
        return
    _env_loaded = True
    for candidate in [
        os.path.join(os.path.dirname(__file__), "..", ".env"),
        os.path.join(os.path.dirname(__file__), "..", "..", "home", ".env"),
        os.path.expanduser("~/.hermes/.env"),
    ]:
        env_path = os.path.abspath(candidate)
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip().strip("\"'")
                    if key.startswith("GMAIL_PASS_") and not os.environ.get(key):
                        os.environ[key] = val
        except (FileNotFoundError, PermissionError, OSError):
            continue

_load_env()

logger = logging.getLogger(__name__)

# ── Multi-Account Credentials (from env vars — never hardcode!) ──
# Set these in your .env or shell environment:
#   GMAIL_PASS_DOMINIK=xxxx xxxx xxxx xxxx
#   GMAIL_PASS_LOGGABLEIM=xxxx xxxx xxxx xxxx
def _build_accounts():
    accounts = {}
    dominik_pw = os.environ.get("GMAIL_PASS_DOMINIK")
    if dominik_pw:
        accounts["dominik"] = ("dominikrnr@gmail.com", dominik_pw)
    loggableim_pw = os.environ.get("GMAIL_PASS_LOGGABLEIM")
    if loggableim_pw:
        accounts["loggableim"] = ("loggableim@gmail.com", loggableim_pw)
    return accounts

def _reload_accounts():
    """Re-read ACCOUNTS from env (call after setting env vars at runtime)."""
    global ACCOUNTS
    ACCOUNTS = _build_accounts()

ACCOUNTS = _build_accounts()
DEFAULT_ACCOUNT = "dominik" if "dominik" in ACCOUNTS else (list(ACCOUNTS) or [None])[0]


# ── IMAP connection pool (per-account, thread-safe) ──
_conn_pool: dict[tuple[int, str], tuple[imaplib.IMAP4_SSL, float]] = {}
_CONN_POOL_LOCK = threading.Lock()
_CONN_MAX_AGE = 120
_CONN_POOL_MAX_SIZE = 12


def _decode_rfc2047(val):
    """Decode RFC 2047 encoded words like =?UTF-8?Q?St=C3=A4dte...?="""
    if not val:
        return ""
    if isinstance(val, bytes):
        val = val.decode("utf-8", "replace")
    try:
        parts = decode_header(val)
        result = []
        for chunk, charset in parts:
            if isinstance(chunk, bytes):
                try:
                    result.append(chunk.decode(charset or "utf-8", "replace"))
                except (LookupError, UnicodeDecodeError):
                    result.append(chunk.decode("utf-8", "replace"))
            else:
                result.append(chunk)
        return " ".join(result)
    except Exception:
        return val


def _s(val):
    """Safe string conversion from bytes or str."""
    if isinstance(val, str):
        return val
    if isinstance(val, bytes):
        return val.decode("utf-8", "replace")
    return str(val)


def _decode_imap_utf7(val):
    """Decode IMAP modified UTF-7 mailbox names."""
    text = _s(val)
    if "&" not in text:
        return text
    out = []
    index = 0
    while index < len(text):
        start = text.find("&", index)
        if start < 0:
            out.append(text[index:])
            break
        out.append(text[index:start])
        end = text.find("-", start)
        if end < 0:
            out.append(text[start:])
            break
        token = text[start + 1 : end]
        if not token:
            out.append("&")
        else:
            try:
                encoded = token.replace(",", "/")
                encoded += "=" * (-len(encoded) % 4)
                out.append(base64.b64decode(encoded).decode("utf-16-be"))
            except Exception:
                out.append(text[start : end + 1])
        index = end + 1
    return "".join(out)


def _encode_imap_utf7(val):
    """Encode display mailbox names to IMAP modified UTF-7."""
    text = _s(val)
    out = []
    buf = []

    def flush_buf():
        if not buf:
            return
        raw = "".join(buf).encode("utf-16-be")
        enc = base64.b64encode(raw).decode("ascii").rstrip("=").replace("/", ",")
        out.append("&" + enc + "-")
        buf.clear()

    for ch in text:
        code = ord(ch)
        if ch == "&":
            flush_buf()
            out.append("&-")
        elif 0x20 <= code <= 0x7E:
            flush_buf()
            out.append(ch)
        else:
            buf.append(ch)
    flush_buf()
    return "".join(out)


def _imap_mailbox_arg(folder):
    return _encode_imap_utf7(folder or "INBOX")


def _decode_header_safe(msg, header_name):
    """Get and decode a header value."""
    raw = msg.get(header_name, "")
    if not raw:
        return ""
    val = _decode_rfc2047(raw)
    val = re.sub(r'\s+', ' ', val).strip()
    return val


def _parse_date(date_str):
    """Parse email date string to a display-friendly format."""
    if not date_str:
        return ""
    try:
        parsed = email.utils.parsedate_to_datetime(date_str)
        if not parsed:
            return date_str[:16]
        now = datetime.now(parsed.tzinfo if parsed.tzinfo else None)
        diff = now - parsed
        if diff.total_seconds() < 0:
            return parsed.strftime("%d.%m.")
        if diff.total_seconds() < 3600:
            mins = int(diff.total_seconds() / 60)
            return f"vor {mins} Min." if mins > 0 else "gerade eben"
        if diff.total_seconds() < 86400:
            return parsed.strftime("%H:%M")
        if diff.total_seconds() < 172800:
            return "Gestern"
        if diff.total_seconds() < 604800:
            return parsed.strftime("%a")
        return parsed.strftime("%d.%m.%y")
    except Exception:
        return date_str[:16]


def _get_workspace_accounts() -> dict:
    """Return Gmail accounts for the active workspace.

    Resolution order (all spaces):
      1. Workspace-specific ``gmail.accounts`` from space.yaml (override)
      2. Global ``ACCOUNTS`` from env vars (GMAIL_PASS_*)
      3. Default workspace's ``gmail.accounts`` config (legacy fallback)

    This means global Gmail credentials are available in ALL spaces,
    not just the default space.  Per-space accounts take precedence.
    """
    try:
        # ── 1. Load global accounts from env (available everywhere) ──
        global_accounts = dict(ACCOUNTS) if ACCOUNTS else {}

        from web.api.workspace_isolation import get_active_workspace_slug, get_workspace
        slug = getattr(_REQUEST_WORKSPACE_LOCAL, "slug", None) or get_active_workspace_slug()

        # ── 2. Load workspace-specific accounts (override) ──
        ws_accounts = _load_space_gmail_accounts(slug)

        if ws_accounts:
            # Space has its own accounts → use them (override global)
            return ws_accounts

        # ── 3. No space-specific accounts → use global env accounts ──
        if global_accounts:
            return global_accounts

        # ── 4. Last resort: default workspace config ──
        result = {}
        ws = get_workspace("default")
        if ws:
            cfg = ws.load_config()
            ws_gmail = cfg.get("gmail")
            if isinstance(ws_gmail, dict):
                ws_accts = ws_gmail.get("accounts", {})
                if isinstance(ws_accts, dict):
                    for acc_name, acc_cfg in ws_accts.items():
                        if isinstance(acc_cfg, dict):
                            email_addr = acc_cfg.get("email", "")
                            password = acc_cfg.get("password", "")
                            if email_addr and password:
                                result[acc_name] = (email_addr, password)
        if not result:
            try:
                from web.api.space_engine import get_workspace as se_get_workspace
                se_ws = se_get_workspace("default")
                if se_ws:
                    se_cfg = se_ws.load_config()
                    se_gmail = se_cfg.get("gmail")
                    if isinstance(se_gmail, dict):
                        se_accts = se_gmail.get("accounts", {})
                        if isinstance(se_accts, dict):
                            for acc_name, acc_cfg in se_accts.items():
                                if isinstance(acc_cfg, dict):
                                    email_addr = acc_cfg.get("email", "")
                                    password = acc_cfg.get("password", "")
                                    if email_addr and password:
                                        result[acc_name] = (email_addr, password)
            except Exception:
                pass
        return result
    except Exception:
        logger.debug("Failed to load workspace gmail accounts", exc_info=True)
        if ACCOUNTS:
            return dict(ACCOUNTS)
        return {}


def _load_space_gmail_accounts(slug: str) -> dict:
    """Load gmail.accounts from a specific space's config.

    Returns empty dict if the space has no Gmail accounts configured.
    Checks both the old workspace_isolation and the new space_engine paths.
    """
    if not slug or slug == "default":
        return {}
    result = {}
    # Try old workspace_isolation path
    try:
        from web.api.workspace_isolation import get_workspace
        ws = get_workspace(slug)
        if ws:
            cfg = ws.load_config()
            ws_gmail = cfg.get("gmail")
            if isinstance(ws_gmail, dict):
                ws_accounts = ws_gmail.get("accounts", {})
                if isinstance(ws_accounts, dict):
                    for acc_name, acc_cfg in ws_accounts.items():
                        if isinstance(acc_cfg, dict):
                            email_addr = acc_cfg.get("email", "")
                            password = acc_cfg.get("password", "")
                            if email_addr and password:
                                result[acc_name] = (email_addr, password)
                    if result:
                        return result
    except Exception:
        logger.debug("workspace_isolation gmail load failed for %s", slug, exc_info=True)
    # Try new space_engine path
    try:
        from web.api.space_engine import get_workspace as se_get_workspace
        se_ws = se_get_workspace(slug)
        if se_ws:
            se_cfg = se_ws.load_config()
            se_gmail = se_cfg.get("gmail")
            if isinstance(se_gmail, dict):
                se_accounts = se_gmail.get("accounts", {})
                if isinstance(se_accounts, dict):
                    for acc_name, acc_cfg in se_accounts.items():
                        if isinstance(acc_cfg, dict):
                            email_addr = acc_cfg.get("email", "")
                            password = acc_cfg.get("password", "")
                            if email_addr and password:
                                result[acc_name] = (email_addr, password)
                    if result:
                        return result
    except Exception:
        logger.debug("space_engine gmail load failed for %s", slug, exc_info=True)
    return result


def _get_creds(account):
    """Get (user, password) for an account — workspace-aware."""
    all_accounts = _get_workspace_accounts()
    return all_accounts.get(account, (account, None))


def _connect_imap(account=DEFAULT_ACCOUNT):
    """Get an IMAP connection for the current thread + account (pooled)."""
    if not account or str(account).strip().lower() == "none":
        accounts = _get_workspace_accounts()
        account = next(iter(accounts), account)
    user, pw = _get_creds(account)
    if not user or not pw:
        raise ValueError(f"Missing credentials for account '{account}'. Set GMAIL_PASS_ environment variables.")
    tid = (threading.get_ident(), account)
    now = time.time()

    with _CONN_POOL_LOCK:
        entry = _conn_pool.get(tid)
        if entry:
            conn, created_at = entry
            if (now - created_at) < _CONN_MAX_AGE:
                try:
                    conn.noop()
                    return conn, user
                except Exception:
                    pass
            _conn_pool.pop(tid, None)
            try:
                conn.logout()
            except Exception:
                pass

    conn = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=15)
    conn.login(user, pw)

    with _CONN_POOL_LOCK:
        if len(_conn_pool) >= _CONN_POOL_MAX_SIZE:
            oldest_tid = min(_conn_pool.keys(), key=lambda k: _conn_pool[k][1])
            old_conn, _ = _conn_pool.pop(oldest_tid)
            try:
                old_conn.logout()
            except Exception:
                pass
        _conn_pool[tid] = (conn, time.time())

    return conn, user


def _close_thread_conn(account=DEFAULT_ACCOUNT):
    """Close and remove the IMAP connection for the current thread + account."""
    tid = (threading.get_ident(), account)
    with _CONN_POOL_LOCK:
        entry = _conn_pool.pop(tid, None)
    if entry:
        conn, _ = entry
        try:
            conn.logout()
        except Exception:
            pass


def _close_conn(conn):
    """Legacy: closes immediately (used by mutation endpoints)."""
    try:
        if conn:
            conn.logout()
    except Exception:
        pass


def _fetch_headers(conn, ids, fields="(FROM TO SUBJECT DATE)"):
    """Batch fetch headers + seen flags for multiple message IDs."""
    if not ids:
        return []
    str_ids = [i.decode() if isinstance(i, bytes) else str(i) for i in ids]
    id_str = ",".join(str_ids)
    status, data = conn.fetch(id_str, f"(FLAGS BODY.PEEK[HEADER.FIELDS {fields}])")
    if status != "OK":
        return []

    results = []  # list of (msg, seen)
    i = 0
    while i < len(data):
        entry = data[i]
        if isinstance(entry, tuple) and len(entry) >= 2:
            raw_flags = entry[0] if isinstance(entry[0], bytes) else b""
            raw = entry[1]
            seen = b"\\Seen" in raw_flags
            if raw:
                try:
                    msg = email.message_from_bytes(raw)
                    results.append((msg, seen))
                except Exception:
                    pass
            i += 2
        else:
            i += 1
    return results

def _fetch_headers_by_uid(conn, uids, fields="(FROM TO SUBJECT DATE)"):
    """Batch fetch headers + seen flags using stable IMAP UIDs."""
    if not uids:
        return []
    str_ids = [i.decode() if isinstance(i, bytes) else str(i) for i in uids]
    status, data = conn.uid("fetch", ",".join(str_ids), f"(UID FLAGS BODY.PEEK[HEADER.FIELDS {fields}])")
    if status != "OK":
        return []

    by_uid = {}
    for entry in data:
        if not (isinstance(entry, tuple) and len(entry) >= 2):
            continue
        meta = entry[0] if isinstance(entry[0], bytes) else b""
        raw = entry[1]
        uid_match = re.search(rb"UID\s+(\d+)", meta)
        if not uid_match or not raw:
            continue
        try:
            uid = uid_match.group(1).decode("ascii")
            by_uid[uid] = (email.message_from_bytes(raw), b"\\Seen" in meta)
        except Exception:
            pass
    return [(uid, *by_uid[uid]) for uid in str_ids if uid in by_uid]


# ── Core operations (account-aware) ──


def _list_emails(max_r=25, folder="INBOX", account=DEFAULT_ACCOUNT):
    """List recent emails with decoded subjects, fast batch fetch."""
    try:
        conn, user = _connect_imap(account)
        status, data = conn.select(_imap_mailbox_arg(folder))
        if status != "OK":
            return {"error": f"Cannot select folder '{folder}'", "account": account}

        since = (datetime.now() - timedelta(days=60)).strftime("%d-%b-%Y")
        status, data = conn.uid("search", None, f'(SINCE {since})')
        if status != "OK" or not data[0]:
            return {"emails": [], "count": 0, "folder": folder, "account": account}

        uids = data[0].split()
        uids = uids[-max_r:] if len(uids) > max_r else uids

        msg_list = _fetch_headers_by_uid(conn, uids)
        results = []
        for uid, msg, seen in reversed(msg_list):
            fr = _decode_header_safe(msg, "From")
            results.append({
                "id": uid,
                "uid": uid,
                "from": fr,
                "from_name": re.sub(r'\s*<[^>]+>\s*', '', fr).strip() or fr,
                "to": _decode_header_safe(msg, "To"),
                "subject": _decode_header_safe(msg, "Subject") or "(kein Betreff)",
                "date": _parse_date(_decode_header_safe(msg, "Date")),
                "date_raw": _decode_header_safe(msg, "Date"),
                "seen": seen,
            })

        return {"emails": results, "count": len(results), "folder": folder, "account": account}
    except Exception:
        logger.exception("_list_emails failed")
        return {"error": "IMAP list failed", "emails": [], "count": 0, "folder": folder, "account": account}


def _read_email(email_id, account=DEFAULT_ACCOUNT):
    """Read full email content."""
    try:
        conn, user = _connect_imap(account)
        conn.select("INBOX")
        status, data = conn.uid("fetch", str(email_id), "(BODY[])")
        if status != "OK":
            return {"error": "Fetch failed", "account": account}

        raw = data[0][1] if isinstance(data[0], tuple) else data[0]
        msg = email.message_from_bytes(raw)

        body = ""
        body_plain = ""
        body_html = ""
        attachments = []
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_disposition() == "attachment":
                    fn = part.get_filename()
                    if fn:
                        attachments.append(_decode_rfc2047(fn))
                ct = part.get_content_type()
                if ct == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        try:
                            decoded = payload.decode("utf-8", "replace")
                        except Exception:
                            decoded = payload.decode("latin-1", "replace")
                        if not body_plain:
                            body_plain = decoded
                        if not body:
                            body = decoded
                elif ct == "text/html":
                    payload = part.get_payload(decode=True)
                    if payload:
                        try:
                            decoded = payload.decode("utf-8", "replace")
                        except Exception:
                            decoded = payload.decode("latin-1", "replace")
                        if not body_html:
                            body_html = decoded
                        if not body:
                            body = decoded
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                try:
                    decoded = payload.decode("utf-8", "replace")
                except Exception:
                    decoded = payload.decode("latin-1", "replace")
                body = decoded
                # Guess type from content-type header
                ct = msg.get_content_type()
                if ct == "text/html":
                    body_html = decoded
                else:
                    body_plain = decoded

        return {
            "id": email_id,
            "from": _decode_header_safe(msg, "From"),
            "to": _decode_header_safe(msg, "To"),
            "subject": _decode_header_safe(msg, "Subject") or "(kein Betreff)",
            "date": _decode_header_safe(msg, "Date"),
            "body": body.strip()[:15000] if body else "",
            "body_plain": body_plain.strip()[:15000] if body_plain else "",
            "body_html": body_html.strip()[:30000] if body_html else "",
            "content_type": "html" if body_html else "text",
            "attachments": attachments,
            "account": account,
        }
    except Exception:
        logger.exception("_read_email failed")
        return {"error": "IMAP read failed", "account": account}


def _search_emails(query, max_r=25, account=DEFAULT_ACCOUNT):
    """Search emails by Gmail-style query syntax."""
    q = query.lower().strip()
    if q.startswith("from:"):
        imap_q = f'(FROM "{q[5:].strip()}")'
    elif q.startswith("subject:"):
        imap_q = f'(SUBJECT "{q[8:].strip()}")'
    elif q.startswith("has:attachment"):
        imap_q = '(OR (HEADER Content-Type "multipart/mixed") (HEADER Content-Type "application/"))'
    else:
        imap_q = f'(TEXT "{query}")'

    try:
        conn, user = _connect_imap(account)
        conn.select("INBOX")
        status, data = conn.search(None, imap_q)
        if status != "OK" or not data[0]:
            return {"emails": [], "count": 0, "query": query, "account": account}

        ids = data[0].split()
        ids = ids[-max_r:] if len(ids) > max_r else ids
        str_ids = [i.decode() if isinstance(i, bytes) else str(i) for i in ids]

        msg_list = _fetch_headers(conn, ids)
        results = []
        for i, (msg, seen) in enumerate(reversed(msg_list)):
            fr = _decode_header_safe(msg, "From")
            results.append({
                "id": str_ids[-(i+1)] if i < len(str_ids) else str(i),
                "from": fr,
                "from_name": re.sub(r'\s*<[^>]+>\s*', '', fr).strip() or fr,
                "to": _decode_header_safe(msg, "To"),
                "subject": _decode_header_safe(msg, "Subject") or "(kein Betreff)",
                "date": _parse_date(_decode_header_safe(msg, "Date")),
                "date_raw": _decode_header_safe(msg, "Date"),
                "seen": seen,
            })

        return {"emails": results, "count": len(results), "query": query, "account": account}
    except Exception:
        logger.exception("_search_emails failed")
        return {"error": "IMAP search failed", "emails": [], "count": 0, "account": account}


def _list_folders(account=DEFAULT_ACCOUNT):
    """List all Gmail folders/labels."""
    try:
        conn, user = _connect_imap(account)
        status, data = conn.list()
        if status != "OK":
            return {"error": "Failed to list folders", "folders": [], "account": account}

        known_system = {"INBOX", "[Gmail]/Gesendet", "[Gmail]/Papierkorb",
                        "[Gmail]/Entwürfe", "[Gmail]/Spam", "[Gmail]/Wichtig",
                        "[Gmail]/Alle Nachrichten"}
        folders = []
        for f in data:
            try:
                decoded = f.decode("utf-8", "replace") if isinstance(f, bytes) else f
                parts = decoded.split('"')
                if len(parts) >= 3:
                    name = _decode_imap_utf7(parts[-2])
                    if name:
                        folders.append({
                            "name": name,
                            "system": name in known_system or name.startswith("[Gmail]"),
                        })
            except Exception:
                continue

        folders.sort(key=lambda x: (0 if x["system"] else 1, x["name"]))
        return {"folders": folders, "account": account}
    except Exception:
        logger.exception("_list_folders failed")
        return {"error": "Failed to list folders", "folders": [], "account": account}


def _send_email(to, subject, body, account=DEFAULT_ACCOUNT, attachments=None):
    """Send email via Gmail SMTP. attachments: list of {filename, content_b64, mimetype}"""
    import base64
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders as _enc

    user, pw = _get_creds(account)

    if attachments:
        msg = MIMEMultipart()
        msg["From"] = user
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))
        for att in attachments:
            filename = att.get("filename", "attachment")
            content_b64 = att.get("content_b64", "")
            mimetype = att.get("mimetype", "application/octet-stream")
            maintype, _, subtype = mimetype.partition("/")
            if not subtype:
                subtype = "octet-stream"
            part = MIMEBase(maintype, subtype)
            part.set_payload(base64.b64decode(content_b64))
            _enc.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=filename)
            msg.attach(part)
    else:
        msg = EmailMessage()
        msg.set_content(body)
        msg["From"] = user
        msg["To"] = to
        msg["Subject"] = subject

    conn = smtplib.SMTP("smtp.gmail.com", 587, timeout=30)
    try:
        conn.starttls()
        conn.login(user, pw)
        conn.send_message(msg)
        return {"status": "sent", "to": to, "subject": subject, "account": account}
    except Exception as e:
        logger.exception("gmail send failed")
        return {"error": str(e), "account": account}
    finally:
        try:
            conn.quit()
        except Exception:
            pass


def _delete_email(email_id, folder="INBOX", account=DEFAULT_ACCOUNT):
    """Move email to trash (reversible)."""
    conn, user = _connect_imap(account)
    try:
        conn.select(_imap_mailbox_arg(folder))
        for trash in ["[Gmail]/Papierkorb", "[Gmail]/Trash", "Trash"]:
            status, _ = conn.uid("copy", str(email_id), _imap_mailbox_arg(trash))
            if status == "OK":
                break
        else:
            return {"error": "Could not find Trash folder", "account": account}

        conn.uid("store", str(email_id), "+FLAGS", "\\Deleted")
        conn.expunge()
        return {"status": "trashed", "id": email_id, "account": account}
    finally:
        _close_conn(conn)


def _move_email(email_id, to_folder, from_folder="INBOX", account=DEFAULT_ACCOUNT):
    """Move email to another folder."""
    conn, user = _connect_imap(account)
    try:
        conn.select(_imap_mailbox_arg(from_folder))
        status, _ = conn.uid("copy", str(email_id), _imap_mailbox_arg(to_folder))
        if status != "OK":
            return {"error": f"Folder '{to_folder}' not found", "account": account}

        conn.uid("store", str(email_id), "+FLAGS", "\\Deleted")
        conn.expunge()
        return {"status": "moved", "id": email_id, "to": to_folder, "account": account}
    finally:
        _close_conn(conn)


def _list_accounts():
    """Return available accounts — workspace-aware.
    
    For non-default workspaces without Gmail config,
    returns ``needs_setup=True`` instead of an empty list.
    """
    all_accounts = _get_workspace_accounts()
    result = {
        "accounts": [
            {"id": k, "email": all_accounts[k][0], "default": k == DEFAULT_ACCOUNT}
            for k in all_accounts
        ]
    }
    # Signal to frontend: this space needs Gmail setup first
    if not all_accounts:
        try:
            from web.api.workspace_isolation import get_active_workspace_slug
            slug = get_active_workspace_slug()
            if slug and slug != "default":
                result["needs_setup"] = True
            else:
                # Default space with no global credentials → show splash
                result["no_credentials"] = True
        except Exception:
            result["no_credentials"] = True
    return result


# ── AI Cache ──
_ai_cache: dict[str, tuple[str, float]] = {}
_AI_CACHE_TTL = 3600  # 1 hour
_AI_CACHE_LOCK = threading.Lock()

def _ai_cache_get(key):
    with _AI_CACHE_LOCK:
        entry = _ai_cache.get(key)
        if entry:
            result, ts = entry
            if time.time() - ts < _AI_CACHE_TTL:
                return result
            _ai_cache.pop(key, None)
    return None

def _ai_cache_set(key, result):
    with _AI_CACHE_LOCK:
        if len(_ai_cache) > 200:
            _ai_cache.clear()
        _ai_cache[key] = (result, time.time())

def _ai_cache_key(prefix, account, email_id):
    raw = f"{prefix}:{account}:{email_id}"
    return hashlib.sha256(raw.encode()).hexdigest()


# ── AI Text Generation (pluggable backends) ──
_AGENT_GATEWAY_PORTS = [9119, 9118, 9120]

def _ai_call(prompt, system_prompt="You are a helpful assistant.", max_tokens=300):
    """Try multiple AI backends in order. Returns text or None."""
    # 1. Try Nova agent gateway (OpenAI-compatible /chat/completions)
    for port in _AGENT_GATEWAY_PORTS:
        try:
            payload = json.dumps({
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": max_tokens,
                "temperature": 0.3,
            }).encode()
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/chat/completions",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=10)
            data = json.loads(resp.read())
            choice = data.get("choices", [{}])[0]
            text = choice.get("message", {}).get("content", "")
            if text and text.strip():
                return text.strip()
        except Exception:
            continue

    # 2. Try Ollama
    try:
        payload = json.dumps({
            "model": "llama3.2:latest",
            "prompt": f"{system_prompt}\n\n{prompt}",
            "stream": False,
            "options": {"num_predict": max_tokens, "temperature": 0.3},
        }).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=30)
        data = json.loads(resp.read())
        text = data.get("response", "")
        if text and text.strip():
            return text.strip()
    except Exception:
        pass

    return None


def _ai_call_stream(prompt, system_prompt="You are a helpful assistant.", model="llama3.2:latest"):
    """Stream tokens from an AI model. Yields (token, done)."""
    try:
        from web.api import config as cfg

        if cfg.is_game_mode_enabled():
            yield (
                "Game Mode is active. Gmail AI is blocked so local GPU/VRAM stays free.",
                True,
            )
            return
    except Exception:
        logger.debug("Gmail AI Game Mode check failed", exc_info=True)

    full_prompt = f"{system_prompt}\n\n{prompt}"
    # Try Ollama streaming
    try:
        payload = json.dumps({
            "model": model,
            "prompt": full_prompt,
            "stream": True,
            "options": {"num_predict": 200, "temperature": 0.3},
        }).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=60)
        buf = ""
        for chunk in iter(lambda: resp.read(1), b""):
            buf += chunk.decode("utf-8", "replace")
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    token = data.get("response", "")
                    done = data.get("done", False)
                    if token:
                        yield token, done
                    if done:
                        return
                except json.JSONDecodeError:
                    continue
        return
    except Exception as e:
        yield f"⚠️ {str(e)}", True
        return


def _stream_summary(handler, email_id, account="dominik", model="llama3.2:latest"):
    """Send SSE summary stream for an email."""
    email_data = _read_email(email_id, account)
    if "error" in email_data:
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Connection", "keep-alive")
        handler.end_headers()
        err_msg = json.dumps({"token": "⚠️ Kein Zugriff auf E-Mail", "done": True})
        handler.wfile.write(f"data: {err_msg}\n\n".encode("utf-8"))
        handler.wfile.flush()
        return

    body = _strip_html(email_data.get("body", ""))
    subject = email_data.get("subject", "")
    sender = email_data.get("from", "")

    prompt = (
        f"Fasse die folgende E-Mail kurz und präzise in 1-2 Sätzen auf Deutsch zusammen.\n"
        f"Betreff: {subject}\n"
        f"Von: {sender}\n\n"
        f"{body[:3000]}"
    )

    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "keep-alive")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()

    try:
        for token, done in _ai_call_stream(prompt, "Du bist ein hilfreicher Assistent.", model):
            event = json.dumps({"token": token, "done": done})
            handler.wfile.write(f"data: {event}\n\n".encode("utf-8"))
            handler.wfile.flush()
            if done:
                return
    except Exception as e:
        error = json.dumps({"token": f"⚠️ Fehler: {e}", "done": True})
        handler.wfile.write(f"data: {error}\n\n".encode("utf-8"))
        handler.wfile.flush()


def _strip_html(text):
    """Remove HTML tags, keep plain text."""
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ── AI: Summarize email ──
def _ai_summarize_email(email_id, account="dominik"):
    cache_key = _ai_cache_key("summary", account, email_id)
    cached = _ai_cache_get(cache_key)
    if cached:
        return cached

    email_data = _read_email(email_id, account)
    if "error" in email_data:
        return "(Kein Zugriff auf E-Mail-Inhalt)"

    body = _strip_html(email_data.get("body", ""))
    subject = email_data.get("subject", "")

    prompt = (
        f"Fasse die folgende E-Mail kurz und präzise in 1-2 Sätzen auf Deutsch zusammen.\n"
        f"Betreff: {subject}\n"
        f"Von: {email_data.get('from', '')}\n\n"
        f"{body[:3000]}"
    )

    result = _ai_call(prompt, "Du bist ein KI-Assistent, der E-Mails zusammenfasst.", max_tokens=200)
    if not result:
        # Fallback: first 200 chars of body
        result = body[:200] + "..." if len(body) > 200 else body
        if not result:
            result = "(Kein Text zum Zusammenfassen)"

    _ai_cache_set(cache_key, result)
    return result


# ── AI: Draft reply ──
def _ai_draft_reply(email_id, account="dominik"):
    cache_key = _ai_cache_key("draft", account, email_id)
    cached = _ai_cache_get(cache_key)
    if cached:
        return cached

    email_data = _read_email(email_id, account)
    if "error" in email_data:
        return ""

    body = _strip_html(email_data.get("body", ""))
    subject = email_data.get("subject", "")
    sender = email_data.get("from", "")

    # Determine if this needs a reply-to-all or simple reply
    to_list = email_data.get("to", "")

    prompt = (
        f"Schreibe einen professionellen Antwortentwurf auf Deutsch für die folgende E-Mail.\n"
        f"Original-Betreff: {subject}\n"
        f"Original-Absender: {sender}\n"
        f"Original-Empfänger: {to_list}\n\n"
        f"Original-Nachricht:\n{body[:3000]}\n\n"
        f"Antworte nur mit dem Text des Entwurfs, ohne Betreff oder Anrede-Zusätze."
    )

    result = _ai_call(
        prompt,
        "Du bist ein hilfreicher Assistent, der professionelle E-Mail-Antworten verfasst.",
        max_tokens=400,
    )
    if not result:
        result = f"Vielen Dank für Ihre Nachricht zum Thema '{subject}'. Ich werde mich zeitnah darum kümmern.\n\nMit freundlichen Grüßen"

    _ai_cache_set(cache_key, result)
    return result


# ── AI: Find related emails ──
_THREAD_PREFIX_RE = re.compile(r"^\s*(?:(?:re|fw|fwd)\s*:\s*)+", re.IGNORECASE)


def _strip_thread_subject_prefixes(subject):
    return _THREAD_PREFIX_RE.sub("", subject or "").strip().lower()


def _ai_find_related(email_id, account="dominik", max_related=5):
    cache_key = _ai_cache_key("related", account, email_id)
    cached = _ai_cache_get(cache_key)
    if cached:
        return json.loads(cached)

    email_data = _read_email(email_id, account)
    if "error" in email_data:
        return []

    subject = email_data.get("subject", "")
    sender = email_data.get("from", "")

    # Get a broader list of recent emails to search for related
    listing = _list_emails(max_r=50, folder="INBOX", account=account)
    all_emails = listing.get("emails", [])

    # Score by: same sender, subject keyword overlap
    subject_words = set(re.sub(r"[^\w\s]", " ", subject.lower()).split())
    sender_domain = sender.split("@")[-1] if "@" in sender else ""
    sender_name = re.sub(r"\s*<[^>]+>", "", sender).strip().lower()

    scored = []
    for e in all_emails:
        if str(e.get("id")) == str(email_id):
            continue  # skip self

        score = 0
        e_subj = e.get("subject", "")
        e_from = e.get("from", "")

        # Same sender is highly related
        if e_from.strip().lower() == sender_name:
            score += 10
        elif sender_domain and sender_domain in e_from:
            score += 5

        # Subject word overlap
        e_words = set(re.sub(r"[^\w\s]", " ", e_subj.lower()).split())
        overlap = len(subject_words & e_words)
        score += overlap * 2

        # Re: or Fwd: continuation
        base_subject = _strip_thread_subject_prefixes(subject)
        base_e_subj = _strip_thread_subject_prefixes(e_subj)
        if base_e_subj and base_subject and (base_e_subj in base_subject or base_subject in base_e_subj):
            score += 8

        # Has shared keywords with body
        if score > 0:
            scored.append((score, e))

    scored.sort(key=lambda x: -x[0])
    related = [
        {"id": s[1]["id"], "subject": s[1].get("subject", ""), "from": s[1].get("from", "")}
        for s in scored[:max_related]
    ]

    _ai_cache_set(cache_key, json.dumps(related))
    return related


# ── HTTP Handlers ──

def _get_account(parsed):
    """Extract account from query params."""
    qs = parse_qs(parsed.query)
    requested = (qs.get("account") or [""])[0].strip()
    accounts = _get_workspace_accounts()
    if requested and requested in accounts:
        return requested
    if DEFAULT_ACCOUNT and DEFAULT_ACCOUNT in accounts:
        return DEFAULT_ACCOUNT
    return next(iter(accounts), requested or DEFAULT_ACCOUNT)


def handle_gmail_get(handler, parsed) -> bool:
    path = parsed.path
    qs = parse_qs(parsed.query)
    workspace = (qs.get("workspace") or [""])[0].strip().lower()
    if workspace:
        _REQUEST_WORKSPACE_LOCAL.slug = workspace
    account = _get_account(parsed)
    try:
        if path == "/api/gmail/accounts":
            return j(handler, _list_accounts())

        if path == "/api/gmail/list":
            max_r = min(int((qs.get("max") or [25])[0]), 100)
            folder = (qs.get("folder") or ["INBOX"])[0]
            return j(handler, _list_emails(max_r, folder, account))

        if path == "/api/gmail/read":
            email_id = (qs.get("id") or [""])[0]
            if not email_id:
                return bad(handler, "Missing email id")
            return j(handler, _read_email(email_id, account))

        if path == "/api/gmail/search":
            query = (qs.get("query") or [""])[0]
            max_r = min(int((qs.get("max") or [25])[0]), 100)
            if not query:
                return bad(handler, "Missing query")
            return j(handler, _search_emails(query, max_r, account))

        if path == "/api/gmail/folders":
            return j(handler, _list_folders(account))

        # ── Gmail AI endpoints ──
        if path == "/api/gmail/ai/summary":
            email_id = (qs.get("id") or [""])[0]
            if not email_id:
                return bad(handler, "Missing email id")
            summary = _ai_summarize_email(email_id, account)
            return j(handler, {"summary": summary})

        if path == "/api/gmail/ai/draft":
            email_id = (qs.get("id") or [""])[0]
            if not email_id:
                return bad(handler, "Missing email id")
            draft = _ai_draft_reply(email_id, account)
            return j(handler, {"draft": draft})

        if path == "/api/gmail/ai/related":
            email_id = (qs.get("id") or [""])[0]
            if not email_id:
                return bad(handler, "Missing email id")
            related = _ai_find_related(email_id, account)
            return j(handler, {"related": related})

        # ── Gmail AI Streaming Summary (SSE) ──
        if path == "/api/gmail/ai/summary/stream":
            email_id = (qs.get("id") or [""])[0]
            model = (qs.get("model") or ["llama3.2:latest"])[0]
            if not email_id:
                return bad(handler, "Missing email id")
            _stream_summary(handler, email_id, account, model)
            return True

        return False
    except Exception as e:
        logger.exception("gmail GET %s failed", path)
        return j(handler, {"error": f"Gmail error: {str(e)}"}, status=500)
    finally:
        if workspace:
            try:
                del _REQUEST_WORKSPACE_LOCAL.slug
            except AttributeError:
                pass


def handle_gmail_post(handler, parsed, body) -> bool:
    path = parsed.path
    if body is None:
        try:
            cl = int(handler.headers.get("Content-Length", 0))
            body = json.loads(handler.rfile.read(cl)) if cl > 0 else {}
        except Exception:
            body = {}
    account = body.get("account", DEFAULT_ACCOUNT)
    workspace = str(body.get("workspace", "")).strip().lower()
    if workspace:
        _REQUEST_WORKSPACE_LOCAL.slug = workspace

    # Ensure workspace context is set from body if not already set by request params
    body_ws = workspace
    if body_ws:
        try:
            from web.api.space_engine import get_active_workspace_slug
            if not get_active_workspace_slug():
                # Set workspace context directly from body-provided slug
                from web.api.workspace_isolation import get_or_create_workspace, set_active_workspace as ws_set_active
                from web.api.config import set_session_dir
                from web.api.kanban_bridge import set_workspace_kanban
                from web.api.space_engine import set_active_workspace as sp_set_active
                ws_obj = get_or_create_workspace(body_ws)
                ws_set_active(ws_obj.slug)
                sp_set_active(ws_obj.slug)
                ws_obj.sessions_dir.mkdir(parents=True, exist_ok=True)
                set_session_dir(str(ws_obj.sessions_dir))
                set_workspace_kanban(str(ws_obj.root))
        except Exception:
            pass

    try:
        if path == "/api/gmail/send":
            raw_attachments = body.get("attachments")
            # Normalize: accept both 'data' and 'content_b64' field names
            if raw_attachments:
                attachments = []
                for att in raw_attachments:
                    normalized = {
                        "filename": att.get("filename", "attachment"),
                        "content_b64": att.get("content_b64", att.get("data", "")),
                        "mimetype": att.get("mimetype", att.get("contentType", "application/octet-stream")),
                    }
                    attachments.append(normalized)
            else:
                attachments = None
            return j(handler, _send_email(
                body.get("to", ""), body.get("subject", ""), body.get("body", ""),
                account, attachments=attachments
            ))

        if path == "/api/gmail/delete":
            return j(handler, _delete_email(
                body.get("id", ""), body.get("folder", "INBOX"), account
            ))

        if path == "/api/gmail/move":
            return j(handler, _move_email(
                body.get("id", ""), body.get("to_folder", ""), body.get("from_folder", "INBOX"), account
            ))

        # ── Gmail AI: Create Kanban task from email ──
        if path == "/api/gmail/ai/task":
            email_id = str(body.get("id") or "").strip()
            if not email_id:
                return bad(handler, "Missing email id")

            # Read email to extract details
            email_data = _read_email(email_id, account)
            if "error" in email_data:
                return bad(handler, email_data["error"])

            title = str(body.get("title") or email_data.get("subject") or "").strip()
            if not title:
                title = f"E-Mail: {email_data.get('from', 'Unbekannt')}"

            email_body = _strip_html(email_data.get("body", ""))
            task_body = (
                f"**Erstellt aus E-Mail**\n\n"
                f"**Von:** {email_data.get('from', '')}\n"
                f"**Betreff:** {email_data.get('subject', '')}\n"
                f"**Datum:** {email_data.get('date', '')}\n\n"
                f"{email_body[:2000]}"
            )
            priority = int(body.get("priority", 1))
            created_by = body.get("created_by", "gmail-ai")

            try:
                from web.api.kanban_bridge import _create_task_payload
                kanban_body = {
                    "title": title,
                    "body": task_body,
                    "priority": priority,
                    "created_by": created_by,
                }
                result = _create_task_payload(kanban_body)
                task = result.get("task", {})
                task_id = task.get("id", "")
                gmailToast_msg = f"📋 Task \"{title}\" erstellt (ID: {task_id[:8]}...)"
                return j(handler, {
                    "status": "created",
                    "task": task,
                    "message": gmailToast_msg,
                })
            except ImportError as exc:
                return bad(handler, f"Kanban nicht verfügbar: {exc}", status=503)
            except (ValueError, LookupError, RuntimeError) as exc:
                return bad(handler, str(exc))

        return False
    except Exception as e:
        logger.exception("gmail POST %s failed", path)
        return j(handler, {"error": f"Gmail error: {str(e)}"}, status=500)
    finally:
        if workspace:
            try:
                del _REQUEST_WORKSPACE_LOCAL.slug
            except AttributeError:
                pass
