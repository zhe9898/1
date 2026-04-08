from __future__ import annotations

from backend.control_plane.auth.role_claims import has_admin_role, normalize_ai_route_preference, normalize_role_name


def test_normalize_role_name_collapses_aliases_to_canonical_roles() -> None:
    assert normalize_role_name("family_child") == "child"
    assert normalize_role_name("kid") == "child"
    assert normalize_role_name("family_elder") == "elder"
    assert normalize_role_name("长辈") == "elder"


def test_has_admin_role_accepts_superadmin_and_rejects_family() -> None:
    assert has_admin_role({"role": "superadmin"}) is True
    assert has_admin_role({"role": "admin"}) is True
    assert has_admin_role({"role": "family"}) is False


def test_normalize_ai_route_preference_falls_back_to_auto() -> None:
    assert normalize_ai_route_preference("cloud") == "cloud"
    assert normalize_ai_route_preference("invalid") == "auto"
