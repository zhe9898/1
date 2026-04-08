"""Workflow template registry for extension SDK and discovery APIs.

Templates are versioned, parameterised workflow blueprints that stay outside
the core scheduler. The scheduler still only runs Jobs and Workflows; this
registry formalises how external teams publish reusable workflow templates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from backend.kernel.extensions.job_kind_registry import is_job_kind_registered
from backend.kernel.extensions.workflow_engine import validate_dag

_PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z0-9_.-]+)\}")


@dataclass(frozen=True, slots=True)
class WorkflowTemplateRegistration:
    template_id: str
    version: str = "1.0.0"
    schema_version: str = "1.0.0"
    sdk_version: str = "1.0.0"
    display_name: str = ""
    description: str = ""
    parameters_schema: type[BaseModel] | None = None
    default_parameters: dict[str, Any] = field(default_factory=dict)
    steps: tuple[dict[str, Any], ...] = ()
    labels: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


_WORKFLOW_TEMPLATE_REGISTRY: dict[str, WorkflowTemplateRegistration] = {}


def register_workflow_template(
    template_id: str,
    *,
    version: str = "1.0.0",
    schema_version: str = "1.0.0",
    sdk_version: str = "1.0.0",
    display_name: str = "",
    description: str = "",
    parameters_schema: type[BaseModel] | None = None,
    default_parameters: dict[str, Any] | None = None,
    steps: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    labels: list[str] | tuple[str, ...] = (),
    metadata: dict[str, Any] | None = None,
) -> None:
    if not steps:
        raise ValueError(f"Workflow template '{template_id}' must declare at least one step")

    validate_dag(list(steps))
    unknown_kinds = sorted({str(step.get("kind", "")).strip() for step in steps if not is_job_kind_registered(str(step.get("kind", "")).strip())})
    if unknown_kinds:
        raise ValueError(f"Workflow template '{template_id}' references unregistered job kind(s): {', '.join(unknown_kinds)}")

    _WORKFLOW_TEMPLATE_REGISTRY[template_id] = WorkflowTemplateRegistration(
        template_id=template_id,
        version=version,
        schema_version=schema_version,
        sdk_version=sdk_version,
        display_name=display_name or template_id,
        description=description,
        parameters_schema=parameters_schema,
        default_parameters=dict(default_parameters or {}),
        steps=tuple(dict(step) for step in steps),
        labels=tuple(labels),
        metadata=dict(metadata or {}),
    )


def unregister_workflow_template(template_id: str) -> None:
    _WORKFLOW_TEMPLATE_REGISTRY.pop(template_id, None)


def is_workflow_template_registered(template_id: str) -> bool:
    return template_id in _WORKFLOW_TEMPLATE_REGISTRY


def get_registered_workflow_templates() -> list[str]:
    return sorted(_WORKFLOW_TEMPLATE_REGISTRY.keys())


def _validate_template_parameters(registration: WorkflowTemplateRegistration, parameters: dict[str, Any]) -> dict[str, Any]:
    merged = dict(registration.default_parameters)
    merged.update(parameters)

    if registration.parameters_schema is None:
        return merged

    try:
        validated = registration.parameters_schema(**merged)
    except ValidationError as exc:
        error_details = exc.errors()
        raise ValueError(
            f"Workflow template parameter validation failed for '{registration.template_id}': " f"{len(error_details)} error(s) - {error_details[0]['msg']}"
        ) from exc
    return validated.model_dump(mode="python")


def _render_value(value: Any, parameters: dict[str, Any]) -> Any:
    if isinstance(value, str):
        full_match = _PLACEHOLDER_RE.fullmatch(value)
        if full_match:
            return parameters.get(full_match.group(1))

        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            replacement = parameters.get(key)
            return "" if replacement is None else str(replacement)

        return _PLACEHOLDER_RE.sub(replace, value)

    if isinstance(value, list):
        return [_render_value(item, parameters) for item in value]

    if isinstance(value, tuple):
        return tuple(_render_value(item, parameters) for item in value)

    if isinstance(value, dict):
        return {key: _render_value(item, parameters) for key, item in value.items()}

    return value


def render_workflow_template(template_id: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    registration = _WORKFLOW_TEMPLATE_REGISTRY.get(template_id)
    if registration is None:
        raise ValueError(f"Workflow template '{template_id}' is not registered")

    validated_parameters = _validate_template_parameters(registration, dict(parameters or {}))
    rendered_steps = [_render_value(step, validated_parameters) for step in registration.steps]
    validate_dag(rendered_steps)
    return {
        "template_id": registration.template_id,
        "version": registration.version,
        "display_name": registration.display_name,
        "description": registration.description,
        "parameters": validated_parameters,
        "steps": rendered_steps,
    }


def get_workflow_template_info(template_id: str) -> dict[str, Any]:
    registration = _WORKFLOW_TEMPLATE_REGISTRY.get(template_id)
    if registration is None:
        raise ValueError(f"Workflow template '{template_id}' is not registered")

    return {
        "template_id": registration.template_id,
        "version": registration.version,
        "schema_version": registration.schema_version,
        "sdk_version": registration.sdk_version,
        "display_name": registration.display_name,
        "description": registration.description,
        "parameters_schema": registration.parameters_schema.model_json_schema() if registration.parameters_schema else None,
        "default_parameters": dict(registration.default_parameters),
        "steps": [dict(step) for step in registration.steps],
        "labels": list(registration.labels),
        "metadata": dict(registration.metadata),
    }


def list_workflow_templates() -> list[dict[str, Any]]:
    return [get_workflow_template_info(template_id) for template_id in get_registered_workflow_templates()]
