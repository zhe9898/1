"""Trigger target validation and published contract resolution."""

from __future__ import annotations

from typing import Any

from .extension_sdk import bootstrap_extension_runtime, get_published_job_kind, get_published_workflow_template
from .trigger_target_contracts import JobTriggerTarget, WorkflowTemplateTriggerTarget


def validate_trigger_target_contract(target: dict[str, Any]) -> dict[str, Any]:
    target_kind = str(target.get("target_kind") or "").strip()
    if target_kind == "job":
        parsed_job = JobTriggerTarget.model_validate(target)
        bootstrap_extension_runtime()
        get_published_job_kind(parsed_job.job_kind)
        parsed: JobTriggerTarget | WorkflowTemplateTriggerTarget = parsed_job
    elif target_kind == "workflow_template":
        parsed_template = WorkflowTemplateTriggerTarget.model_validate(target)
        bootstrap_extension_runtime()
        get_published_workflow_template(parsed_template.template_id)
        parsed = parsed_template
    else:
        raise ValueError("Trigger target_kind must be 'job' or 'workflow_template'")
    return parsed.model_dump(mode="json")
