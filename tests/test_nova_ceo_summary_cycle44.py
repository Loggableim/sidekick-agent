"""CEO blocker summary remains bounded, redacted, and Space-isolated."""

from web.api.nova_presence import _blockers_for


def test_three_space_blocker_summary_exposes_only_public_codes_and_managed_spaces() -> None:
    managed = [
        {"space": "aquarium-zentrum", "state": "paused"},
        {"space": "finanzjunkie", "state": "failed"},
    ]
    events = [
        {"space": "aquarium-zentrum", "reason": "governance_changed"},
        {"space": "finanzjunkie", "reason": "verification_not_verified"},
        {"space": "nova", "reason": "token=secret"},
        {"space": "../../secret", "reason": "root"},
    ]
    blockers = _blockers_for(managed, events)
    assert blockers == [
        {"space": "aquarium-zentrum", "code": "governance_changed"},
        {"space": "finanzjunkie", "code": "verification_not_verified"},
    ]
    rendered = str(blockers).lower()
    assert "secret" not in rendered and "token" not in rendered

