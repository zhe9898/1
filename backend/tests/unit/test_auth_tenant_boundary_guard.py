from __future__ import annotations

from tools import auth_tenant_boundary_guard as guard


def test_auth_tenant_boundary_guard_redacts_sensitive_terms_in_output(monkeypatch, capsys) -> None:
    monkeypatch.setattr(guard, "auth_tenant_boundary_violations", lambda: ["demo password token secret violation"])
    monkeypatch.setattr(guard, "export_auth_boundary_contract", lambda: "boundary-contract")

    assert guard.main() == 1

    captured = capsys.readouterr()
    assert "boundary-contract" not in captured.out
    assert "violation_count=1" in captured.out
    assert "unknown-auth-boundary: contract violation" in captured.out
    assert "password" not in captured.out.lower()
    assert "token" not in captured.out.lower()
    assert "secret" not in captured.out.lower()
