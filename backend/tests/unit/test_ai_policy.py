from __future__ import annotations

import json

from backend.control_plane.auth.ai_policy import apply_prompt_override, resolve_ai_proxy_policy


def test_child_role_alias_resolves_to_child_prompt_override() -> None:
    policy = resolve_ai_proxy_policy(
        {"role": "family_child", "ai_route_preference": "auto"},
        method="POST",
        path="v1/chat/completions",
    )

    assert policy.route_preference == "auto"
    assert policy.prompt_override is not None
    assert "family learning guide" in policy.prompt_override.system_prompt


def test_elder_prompt_override_uses_structured_contract_without_device_ids() -> None:
    policy = resolve_ai_proxy_policy(
        {"role": "elder", "ai_route_preference": "cloud"},
        method="POST",
        path="v1/chat/completions",
    )

    assert policy.route_preference == "cloud"
    assert policy.prompt_override is not None
    assert policy.prompt_override.response_format == {"type": "json_object"}
    assert "device IDs" in policy.prompt_override.system_prompt
    assert "light_living_1" not in policy.prompt_override.system_prompt


def test_apply_prompt_override_injects_system_message_and_response_format() -> None:
    policy = resolve_ai_proxy_policy(
        {"role": "family_elder", "ai_route_preference": "auto"},
        method="POST",
        path="chat/completions",
    )

    body = {"messages": [{"role": "user", "content": "turn the light on"}]}
    updated = apply_prompt_override(json.dumps(body).encode("utf-8"), policy.prompt_override)  # type: ignore[arg-type]
    payload = json.loads(updated.decode("utf-8"))

    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][1]["content"] == "turn the light on"
    assert payload["response_format"] == {"type": "json_object"}


def test_non_chat_requests_do_not_apply_role_prompt_override() -> None:
    policy = resolve_ai_proxy_policy(
        {"role": "family_child", "ai_route_preference": "cloud"},
        method="POST",
        path="v1/images/generations",
    )

    assert policy.route_preference == "cloud"
    assert policy.prompt_override is None


def test_non_post_chat_requests_do_not_apply_role_prompt_override() -> None:
    policy = resolve_ai_proxy_policy(
        {"role": "elder", "ai_route_preference": "auto"},
        method="GET",
        path="v1/chat/completions",
    )

    assert policy.route_preference == "auto"
    assert policy.prompt_override is None
