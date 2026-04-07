from __future__ import annotations

import pytest

from backend.core.alert_actions import normalize_alert_action


def test_normalize_alert_action_accepts_public_webhook() -> None:
    normalized = normalize_alert_action(
        {
            "type": "webhook",
            "url": "https://hooks.example.test/alerts",
            "method": "POST",
            "headers": {"X-Zen70-Alert": "1"},
        }
    )

    assert normalized["type"] == "webhook"
    assert normalized["url"] == "https://hooks.example.test/alerts"
    assert normalized["headers"] == {"X-Zen70-Alert": "1"}


def test_normalize_alert_action_rejects_private_webhook() -> None:
    with pytest.raises(ValueError):
        normalize_alert_action(
            {
                "type": "webhook",
                "url": "http://127.0.0.1:6379/metrics",
                "method": "POST",
            }
        )
