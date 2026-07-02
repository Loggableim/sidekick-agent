"""Tool to list IMAP folders for one or all inboxes in a space.

Uses the shared IMAP helpers from :mod:`tools.mail_imap`.

When ``inbox_id`` is omitted, returns an overview of all configured inboxes
with their folder counts.  When ``inbox_id`` is given, returns the full
folder tree for that inbox.
"""

from __future__ import annotations

import json
import logging
import os

from tools.mail_imap import (
    get_inbox_config,
    get_imap,
    get_space_config,
    list_inboxes,
    release_imap,
)
from tools.registry import registry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA = {
    "type": "object",
    "properties": {
        "inbox_id": {
            "type": "string",
            "description": (
                "Optional inbox identifier.  When omitted, returns a summary "
                "of all configured inboxes.  When given, returns the full "
                "folder tree for that inbox."
            ),
        },
    },
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_space_slug(kw: dict) -> str:
    slug = kw.get("user_task", "")
    if not slug:
        slug = os.getenv("HERMES_WEBUI_ACTIVE_WORKSPACE", "default")
    return slug or "default"


def _parse_folder_line(line: bytes) -> dict | None:
    """Parse a single IMAP ``LIST`` response line.

    Typical format::

        (\\\\HasNoChildren) \"/\" \"INBOX\"

    Returns ``{\"name\": \"INBOX\", \"delimiter\": \"/\", \"flags\": [\"\\\\HasNoChildren\"]}``
    or ``None`` if the line cannot be parsed.
    """
    try:
        decoded = line.decode("utf-8", errors="replace")
    except Exception:
        return None

    # IMAP LIST response: <flags> <delimiter> <name>
    # Flags are in parentheses, delimiter is quoted, name is quoted
    import re
    m = re.match(r'^\(([^)]*)\)\s+"([^"]*)"\s+"(.*)"\s*$', decoded)
    if not m:
        return None

    flags_raw = m.group(1).strip()
    flags = [f.strip() for f in flags_raw.split() if f.strip()] if flags_raw else []
    delimiter = m.group(2)
    name = m.group(3)

    return {
        "name": name,
        "delimiter": delimiter,
        "flags": flags,
    }


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def _handler(args: dict, **kw) -> str:
    space_slug = _get_space_slug(kw)
    inbox_id = args.get("inbox_id")

    # Load space config
    config = get_space_config(space_slug)
    if not config:
        return json.dumps({
            "error": "No mail config found for this space",
            "inboxes": [],
        })

    inboxes = config.get("inboxes", [])

    # Filter by inbox_id if given
    if inbox_id:
        inboxes = [ib for ib in inboxes if ib.get("id") == inbox_id]
        if not inboxes:
            return json.dumps({"error": f"Inbox '{inbox_id}' not found", "inboxes": []})

    result_inboxes = []

    for inbox in inboxes:
        ib_id = inbox.get("id", "")
        ib_label = inbox.get("label", ib_id)

        try:
            conn = get_imap(inbox)
        except Exception as exc:
            logger.warning("IMAP connection failed for %s: %s", ib_id, exc)
            result_inboxes.append({
                "id": ib_id,
                "label": ib_label,
                "error": str(exc),
                "folders": [],
            })
            continue

        try:
            typ, data = conn.list()
            if typ != "OK":
                result_inboxes.append({
                    "id": ib_id,
                    "label": ib_label,
                    "error": f"LIST failed: {typ}",
                    "folders": [],
                })
                continue

            folders = []
            for item in data:
                parsed = _parse_folder_line(item) if isinstance(item, bytes) else None
                if parsed:
                    folders.append(parsed)

            result_inboxes.append({
                "id": ib_id,
                "label": ib_label,
                "folders": folders,
            })

        except Exception as exc:
            logger.exception("Folder listing failed for %s", ib_id)
            result_inboxes.append({
                "id": ib_id,
                "label": ib_label,
                "error": str(exc),
                "folders": [],
            })
        finally:
            release_imap(conn)

    return json.dumps({"inboxes": result_inboxes})


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

registry.register(
    name="mail_folders",
    toolset="mail",
    schema=SCHEMA,
    handler=_handler,
    emoji="📁",
)
