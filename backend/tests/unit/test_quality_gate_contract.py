from __future__ import annotations

from scripts.quality_gate import IAC_DRIFT_TARGETS


def test_iac_drift_targets_cover_rendered_contracts_but_not_machine_local_env() -> None:
    assert "docker-compose.yml" in IAC_DRIFT_TARGETS
    assert "config/Caddyfile" in IAC_DRIFT_TARGETS
    assert ".env" not in IAC_DRIFT_TARGETS
