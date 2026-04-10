"""Extensions domain package."""

from __future__ import annotations

from typing import Any

from .connector_service import ConnectorService
from .extension_guard import export_extension_budget_contract, validate_extension_manifest_contract
from .extension_sdk import (
    CompatibilityPolicy,
    ConnectorKindSpec,
    ExtensionManifest,
    JobKindSpec,
    WorkflowTemplateSpec,
    bootstrap_extension_runtime,
    get_extension_info,
    list_extensions,
)
from .trigger_command_service import TriggerCommandService
from .workflow_command_service import WorkflowCommandService


async def fire_trigger(*args: Any, **kwargs: Any) -> Any:
    from .trigger_service import fire_trigger as _fire_trigger

    return await _fire_trigger(*args, **kwargs)


def validate_trigger_target_contract(*args: Any, **kwargs: Any) -> Any:
    from .trigger_service import validate_trigger_target_contract as _validate_trigger_target_contract

    return _validate_trigger_target_contract(*args, **kwargs)


__all__ = [
    "CompatibilityPolicy",
    "ConnectorKindSpec",
    "ConnectorService",
    "ExtensionManifest",
    "JobKindSpec",
    "TriggerCommandService",
    "WorkflowCommandService",
    "WorkflowTemplateSpec",
    "bootstrap_extension_runtime",
    "export_extension_budget_contract",
    "fire_trigger",
    "get_extension_info",
    "list_extensions",
    "validate_extension_manifest_contract",
    "validate_trigger_target_contract",
]
