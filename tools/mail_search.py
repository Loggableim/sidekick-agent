"""Tool to search emails in IMAP folders.

This module registers the ``mail_search`` tool. It uses the shared IMAP helpers from
:mod:`tools.mail_imap`.
"""

import json
import logging
import os
import email

from tools.mail_imap import (
    get_space_config,
    get_inbox_config,
    get_imap,
    release_imap,
    parse_mail_summary,
)
from tools.registry import registry

logger = logging.getLogger(__name__)

SCHEMA = {
    "type": "object",
    "properties": {
        "inbox_id": {"type": "string"},
        "query": {"type": "string"},
        "folder": {"type": "string", "default": "INBOX"},
        "limit": {"type": "integer", "default": 20},
    },
    "required": ["inbox_id", "query"],
    "additionalProperties": False,
}


def _get_space_slug(kw: dict) -> str:
    """Determine the active space slug.

    The caller may pass the slug in ``kw['user_task']``.  If not present, fall back
    to the ``HERMES_WEBUI_ACTIVE_WORKSPACE`` environment variable, and finally to
    ``'default'``.
    """
    slug = kw.get("user_task", "")
    if not slug:
        slug = os.getenv("SIDEKICK_WEBUI_ACTIVE_WORKSPACE", "default")
    return slug or "default"


def _handler(args: dict, **kw) -> str:
    space_slug = _get_space_slug(kw)
    inbox_id = args.get("inbox_id")
    query = args.get("query")
    folder = args.get("folder", "INBOX")
    limit = int(args.get("limit", 20))

    inbox = get_inbox_config(space_slug, inbox_id)
    if not inbox:
        return json.dumps({"error": "Inbox not found"})

    try:
        conn = get_imap(inbox)
    except Exception as exc:
        logger.exception("Failed to get IMAP connection: %s", exc)
        return json.dumps({"error": str(exc)})

    try:
        # Select folder
        conn.select(folder, readonly=True)

        # Perform OR SUBJECT/ FROM search
        # imaplib expects separate arguments for the OR expression
        typ, data = conn.search(None, "OR", "SUBJECT", query, "FROM", query)
        if typ != "OK":
            raise RuntimeError(f"IMAP search failed: {typ}")

        # data is a list with one bytestring of space separated uids
        uids = data[0].split() if data else []
        total = len(uids)
        # Take most recent N (last N uids)
        selected_uids = uids[-limit:]
        mails = []
        for uid in selected_uids:
            typ, msg_data = conn.fetch(uid, "(FLAGS BODY.PEEK[])")
            if typ != "OK":
                logger.warning("Failed to fetch uid %s: %s", uid, typ)
                continue
            # msg_data is a list of tuples (b'1 (FLAGS (...))', b'...')
            # Find the part that contains the raw email bytes
            raw = None
            for part in msg_data:
                if isinstance(part, tuple) and len(part) == 2:
                    raw = part[1]
                    break
            if raw is None:
                logger.warning("No raw email data for uid %s", uid)
                continue
            msg = email.message_from_bytes(raw)
            summary = parse_mail_summary(msg, uid.decode() if isinstance(uid, bytes) else uid)
            mails.append(summary)
    except Exception as exc:
        logger.exception("Error during mail search: %s", exc)
        return json.dumps({"error": str(exc)})
    finally:
        release_imap(conn)

    result = {
        "mails": mails,
        "total": total,
        "query": query,
        "folder": folder,
    }
    return json.dumps(result)

# Register the tool
registry.register(
    name="mail_search",
    toolset="mail",
    schema=SCHEMA,
    handler=_handler,
    emoji="🔍",
)

# End of file
