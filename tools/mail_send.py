"""Tool for sending emails via SMTP.

This module registers the ``mail_send`` tool which allows a user to send an
email from a configured inbox.  The inbox configuration is loaded from the
``mail.json`` file in the active workspace directory.

The handler performs the following steps:

1. Resolve the active workspace slug.
2. Load the inbox configuration.
3. Verify that SMTP settings are present.
4. Build an :class:`email.message.EmailMessage` instance.
5. Send the message using :func:`tools.mail_imap.send_mail`.
6. Return a JSON string describing success or failure.

The function returns a JSON string because the parent agent expects a string
payload from all tool handlers.
"""

import json
import os
from email.message import EmailMessage

from tools.mail_imap import get_inbox_config, send_mail
from tools.registry import registry

# JSON schema for the tool input
SCHEMA = {
    "type": "object",
    "properties": {
        "inbox_id": {"type": "string"},
        "to": {"type": "string"},
        "subject": {"type": "string"},
        "body": {"type": "string"},
        "cc": {"type": "string"},
    },
    "required": ["inbox_id", "to", "subject", "body"],
    "additionalProperties": False,
}


def _handler(args: dict, **kw) -> str:
    """Send an email using the configured inbox.

    Parameters
    ----------
    args:
        Dictionary containing the tool arguments.
    kw:
        Additional keyword arguments.  ``user_task`` may contain the active
        workspace slug.  If absent, ``SIDEKICK_WEBUI_ACTIVE_WORKSPACE`` is
        consulted first, then ``HERMES_WEBUI_ACTIVE_WORKSPACE`` for legacy
        compatibility, and finally ``default``.

    Returns
    -------
    str
        JSON string describing the result.
    """
    # Resolve space slug
    space_slug = (
        kw.get("user_task", "")
        or os.environ.get("SIDEKICK_WEBUI_ACTIVE_WORKSPACE", "")
        or os.environ.get("HERMES_WEBUI_ACTIVE_WORKSPACE", "")
        or "default"
    )

    inbox = get_inbox_config(space_slug, args.get("inbox_id"))
    if not inbox:
        return json.dumps({"error": "Inbox not found"})

    # Ensure SMTP configured
    if not inbox.get("smtp_host"):
        return json.dumps({"error": "SMTP not configured for this inbox"})

    # Parse recipients
    to_list = [addr.strip() for addr in args.get("to", "").split(",") if addr.strip()]
    cc_raw = args.get("cc", "")
    cc_list = [addr.strip() for addr in cc_raw.split(",") if addr.strip()]

    # Build EmailMessage
    msg = EmailMessage()
    msg["From"] = inbox.get("imap_user", "")
    msg["To"] = ", ".join(to_list)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    msg["Subject"] = args.get("subject", "")
    msg.set_content(args.get("body", ""), subtype="plain")

    all_recipients = to_list + cc_list
    send_result = send_mail(inbox, all_recipients, msg.as_string())

    if send_result.get("success"):
        return json.dumps(
            {
                "success": True,
                "sent_to": all_recipients,
                "subject": args.get("subject", ""),
            }
        )
    else:
        return json.dumps(
            {
                "success": False,
                "error": send_result.get("error", "Unknown error"),
            }
        )

# Register the tool in the global registry
registry.register(
    name="mail_send",
    toolset="mail",
    schema=SCHEMA,
    handler=_handler,
    emoji="📤",
)

if __name__ == "__main__":
    # Simple manual test when run directly
    import sys

    if len(sys.argv) < 2:
        print("Usage: python mail_send.py <json-args>")
        sys.exit(1)
    args = json.loads(sys.argv[1])
    print(_handler(args))

