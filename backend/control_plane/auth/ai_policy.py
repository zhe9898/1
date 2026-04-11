from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from backend.kernel.contracts.role_claims import (
    current_user_role,
    is_child_role_value,
    is_elder_role_value,
    normalize_ai_route_preference,
)

_CHILD_SYSTEM_PROMPT = (
    "You are a patient family learning guide. Answer with simple, age-appropriate, educational language. "
    "Refuse unsafe, violent, sexual, or otherwise adult-only content and gently redirect to safe topics."
)
_ELDER_SYSTEM_PROMPT = (
    "You are a smart-home intent interpreter for the control plane. Return compact JSON only. "
    'Schema: {"intent":"device_control","target_hint":"string","action":"on|off|open|close|toggle|set","parameters":{}}. '
    "Never invent concrete device IDs. If the request is ambiguous or unsupported, return "
    '{"error":"unrecognized_command"}.'
)


@dataclass(frozen=True, slots=True)
class AIPromptOverride:
    system_prompt: str
    response_format: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class AIProxyPolicy:
    route_preference: str
    prompt_override: AIPromptOverride | None = None


def resolve_ai_proxy_policy(
    current_user: Mapping[str, object] | None,
    *,
    method: str,
    path: str,
) -> AIProxyPolicy:
    route_preference = normalize_ai_route_preference((current_user or {}).get("ai_route_preference"))
    if method.upper() != "POST" or "chat/completions" not in path:
        return AIProxyPolicy(route_preference=route_preference)

    role = current_user_role(current_user)
    if is_child_role_value(role):
        return AIProxyPolicy(
            route_preference=route_preference,
            prompt_override=AIPromptOverride(system_prompt=_CHILD_SYSTEM_PROMPT),
        )
    if is_elder_role_value(role):
        return AIProxyPolicy(
            route_preference=route_preference,
            prompt_override=AIPromptOverride(
                system_prompt=_ELDER_SYSTEM_PROMPT,
                response_format={"type": "json_object"},
            ),
        )
    return AIProxyPolicy(route_preference=route_preference)


def apply_prompt_override(content: bytes, override: AIPromptOverride) -> bytes:
    body_json = json.loads(content or b"{}")
    if not isinstance(body_json, dict):
        raise ValueError("AI request body must be a JSON object")

    messages = body_json.setdefault("messages", [])
    if not isinstance(messages, list):
        raise ValueError("AI request messages must be a list")

    messages.insert(
        0,
        {
            "role": "system",
            "content": override.system_prompt,
        },
    )
    if override.response_format is not None:
        body_json["response_format"] = dict(override.response_format)
    return json.dumps(body_json, ensure_ascii=False).encode("utf-8")


__all__ = (
    "AIPromptOverride",
    "AIProxyPolicy",
    "apply_prompt_override",
    "resolve_ai_proxy_policy",
)
