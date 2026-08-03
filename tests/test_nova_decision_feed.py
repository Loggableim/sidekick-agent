from web.api.nova_presence import _decision_feed_for


def test_decision_feed_is_bounded_and_redacts_unknown_audit_reasons():
    result = _decision_feed_for([
        {
            "space": "aquarium-zentrum",
            "event_type": "paused",
            "reason": "model_chain_exhausted",
            "at": "2026-08-03T00:00:00+00:00",
        },
        {
            "space": "aquarium-zentrum",
            "event_type": "paused",
            "reason": "SECRET C:/private/project",
            "at": "2026-08-03T00:01:00+00:00",
        },
        {
            "space": "../private",
            "event_type": "paused",
            "reason": "model_chain_exhausted",
            "at": "2026-08-03T00:02:00+00:00",
        },
    ])
    assert result == [
        {
            "space": "aquarium-zentrum",
            "event": "paused",
            "reason": "model_chain_exhausted",
            "at": "2026-08-03T00:00:00+00:00",
        },
        {
            "space": "aquarium-zentrum",
            "event": "paused",
            "reason": "policy_checked",
            "at": "2026-08-03T00:01:00+00:00",
        },
    ]
    assert "SECRET" not in str(result)
    assert "private" not in str(result)