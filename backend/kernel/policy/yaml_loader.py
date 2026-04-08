from __future__ import annotations

import types
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Union, get_args, get_origin, get_type_hints

from backend.kernel.policy.types import SchedulingPolicy


@dataclass(frozen=True, slots=True)
class SchedulingPolicyBootstrap:
    tenant_quotas_raw: dict[str, Any] = field(default_factory=dict)
    placement_policies_raw: list[dict[str, Any]] = field(default_factory=list)
    default_service_class_yaml: str = "standard"
    resource_quotas_raw: dict[str, Any] = field(default_factory=dict)
    executor_contracts_raw: dict[str, Any] = field(default_factory=dict)
    policy: SchedulingPolicy | None = None


def load_policy_bootstrap(path: str) -> SchedulingPolicyBootstrap:
    import yaml  # type: ignore[import-untyped, unused-ignore]

    raw_document = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(raw_document, Mapping):
        raw_document = {}

    scheduling_raw = raw_document.get("scheduling", {}) or {}
    if not isinstance(scheduling_raw, Mapping):
        scheduling_raw = {}

    policy_raw = scheduling_raw.get("policy", {}) or {}
    if policy_raw and not isinstance(policy_raw, Mapping):
        raise TypeError("scheduling.policy must be a mapping")

    return SchedulingPolicyBootstrap(
        tenant_quotas_raw=_coerce_mapping(scheduling_raw.get("tenant_quotas")),
        placement_policies_raw=_coerce_mapping_list(scheduling_raw.get("placement_policies")),
        default_service_class_yaml=str(scheduling_raw.get("default_service_class", "standard")),
        resource_quotas_raw=_coerce_mapping(scheduling_raw.get("resource_quotas")),
        executor_contracts_raw=_coerce_mapping(scheduling_raw.get("executor_contracts")),
        policy=parse_policy_mapping(policy_raw) if policy_raw else None,
    )


def parse_policy_mapping(raw: Mapping[str, Any]) -> SchedulingPolicy:
    return _build_dataclass_instance(SchedulingPolicy, raw)


def _build_dataclass_instance(cls: type[Any], raw: Mapping[str, Any]) -> Any:
    resolved_types = get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    for field_def in fields(cls):
        if field_def.name not in raw:
            continue
        kwargs[field_def.name] = _coerce_value(raw[field_def.name], resolved_types.get(field_def.name, field_def.type))
    return cls(**kwargs)


def _coerce_value(value: Any, annotation: Any) -> Any:
    if annotation is Any:
        return value

    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        return _coerce_union(value, get_args(annotation))

    if is_dataclass(annotation):
        if not isinstance(value, Mapping):
            raise TypeError(f"expected mapping for {annotation}, got {type(value).__name__}")
        return _build_dataclass_instance(annotation, value)

    if origin is dict:
        key_type, value_type = get_args(annotation) or (Any, Any)
        if not isinstance(value, Mapping):
            raise TypeError(f"expected mapping for {annotation}, got {type(value).__name__}")
        return {
            _coerce_value(raw_key, key_type): _coerce_value(raw_value, value_type)
            for raw_key, raw_value in value.items()
        }

    if origin is list:
        item_type = (get_args(annotation) or (Any,))[0]
        if not isinstance(value, list):
            raise TypeError(f"expected list for {annotation}, got {type(value).__name__}")
        return [_coerce_value(item, item_type) for item in value]

    if origin is tuple:
        if not isinstance(value, (list, tuple)):
            raise TypeError(f"expected list/tuple for {annotation}, got {type(value).__name__}")
        item_types = get_args(annotation)
        if len(item_types) == 2 and item_types[1] is Ellipsis:
            return tuple(_coerce_value(item, item_types[0]) for item in value)
        if item_types and len(value) != len(item_types):
            raise ValueError(f"tuple arity mismatch for {annotation}: expected {len(item_types)}, got {len(value)}")
        return tuple(_coerce_value(item, item_type) for item, item_type in zip(value, item_types or ()))

    if annotation is bool:
        return _coerce_bool(value)
    if annotation is int:
        return int(value)
    if annotation is float:
        return float(value)
    if annotation is str:
        return str(value)

    return value


def _coerce_union(value: Any, variants: tuple[Any, ...]) -> Any:
    if value is None and type(None) in variants:
        return None

    last_error: Exception | None = None
    for variant in variants:
        if variant is type(None):
            continue
        try:
            return _coerce_value(value, variant)
        except Exception as exc:  # pragma: no cover - only triggered on mismatched variant attempts
            last_error = exc
    if last_error is not None:
        raise last_error
    return value


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise TypeError(f"cannot coerce {value!r} to bool")


def _coerce_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): raw_value for key, raw_value in value.items()}


def _coerce_mapping_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [_coerce_mapping(item) for item in value if isinstance(item, Mapping)]
