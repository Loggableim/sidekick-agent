from __future__ import annotations

from cli import web_server


def test_status_digest_is_bounded_and_marks_truncation() -> None:
    limit = web_server._NOVA_STATUS_DIGEST_MAX_BYTES
    prefix = " M generated\n"
    oversized = prefix + ("?? build/" + "x" * 127 + "\n") * (limit // 128 + 20)

    digest = web_server._bounded_nova_status_digest(oversized)
    assert digest is not None
    assert len(digest) == 64
    assert digest == web_server._bounded_nova_status_digest(oversized)
    changed = " X generated\n" + oversized[len(" M generated\n"): ]
    assert digest != web_server._bounded_nova_status_digest(changed)


def test_status_digest_ignores_clean_or_invalid_payloads() -> None:
    assert web_server._bounded_nova_status_digest("") is None
    assert web_server._bounded_nova_status_digest("   \n") is None
    assert web_server._bounded_nova_status_digest(None) is None
    assert web_server._bounded_nova_status_digest(" M app.py\n") is not None
