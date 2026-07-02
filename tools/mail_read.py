"""Tool to read emails from an IMAP inbox.

This tool uses the shared IMAP helpers in :mod:`tools.mail_imap`.

The handler accepts the following arguments (matching the schema below):

* ``inbox_id`` (required) – identifier of the inbox in the active space.
* ``folder`` (optional, default ``"INBOX"``) – mailbox folder to read.
* ``limit`` (optional, default ``20``) – maximum number of messages to return.
* ``since`` (optional) – ISO‑date string; only messages with ``SENTDATE`` after this
  date are returned.
* ``unread_only`` (optional, default ``False``) – if ``True`` the IMAP search
  includes the ``UNSEEN`` flag.

The handler returns a JSON string containing ``mails`` (list of summaries),
``total`` (total number of messages in the folder), and ``folder``.
If the inbox cannot be found or the IMAP connection fails, an ``{"error": ...}``
object is returned.
"""

from __future__ import annotations

import datetime
import email
import json
import os

from tools.mail_imap import (
    get_inbox_config,
    get_imap,
    parse_mail_summary,
    release_imap,
)
from tools.registry import registry

SCHEMA = {
    "type": "object",
    "properties": {
        "inbox_id": {"type": "string"},
        "folder": {"type": "string", "default": "INBOX"},
        "limit": {"type": "integer", "default": 20},
        "since": {"type": "string", "format": "date-time", "nullable": True},
        "unread_only": {"type": "boolean", "default": False},
    },
    "required": ["inbox_id"],
    "additionalProperties": False,
}


def _handler(args: dict, **kw) -> str:
    """Implementation of the ``mail_read`` tool.

    Parameters are validated against :data:`SCHEMA` by the tool framework.
    """
    # Resolve the active space slug.
    space_slug = kw.get("user_task") or os.environ.get("HERMES_WEBUI_ACTIVE_WORKSPACE", "default")

    inbox_id = args.get("inbox_id")
    folder = args.get("folder", "INBOX")
    limit = int(args.get("limit", 20))
    since = args.get("since")
    unread_only = bool(args.get("unread_only", False))

    inbox = get_inbox_config(space_slug, inbox_id)
    if not inbox:
        return json.dumps({"error": "Inbox not found"})

    try:
        conn = get_imap(inbox)
    except Exception as exc:
        return json.dumps({"error": f"IMAP connection failed: {exc}"})

    try:
        # Select the folder.
        conn.select(folder)

        # Build search criteria.
        criteria = []
        if unread_only:
            criteria.append("UNSEEN")
        if since:
            try:
                dt = datetime.datetime.fromisoformat(since)
                criteria.append("SINCE " + dt.strftime("%d-%b-%Y"))
            except Exception:
                # Invalid date format – ignore the filter.
                pass

        if criteria:
            typ, data = conn.search(None, *criteria)
        else:
            typ, data = conn.search(None)

        if typ != "OK":
            return json.dumps({"error": f"IMAP search failed: {typ}"})

        msg_ids = data[0].split()
        total = len(msg_ids)
        selected_ids = msg_ids[-limit:] if limit else msg_ids

        mails = []
        for uid in selected_ids:
            typ, msg_data = conn.fetch(uid, "(FLAGS BODY.PEEK[])")
            if typ != "OK":
                continue
            # ``msg_data`` is a list of tuples; the payload is at index 1.
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            mails.append(parse_mail_summary(msg, uid.decode() if isinstance(uid, bytes) else uid))

        return json.dumps({"mails": mails, "total": total, "folder": folder})
    except Exception as exc:
        return json.dumps({"error": str(exc)})
    finally:
        release_imap(conn)


# Register the tool.
registry.register(
    name="mail_read",
    toolset="mail",
    schema=SCHEMA,
    handler=_handler,
    emoji="📨",
)
