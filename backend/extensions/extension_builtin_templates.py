"""Builtin workflow template catalog."""

from __future__ import annotations

from .extension_builtin_template_contracts import FileTransferTemplateParams, HttpHealthcheckTemplateParams
from .extension_contracts import WorkflowTemplateSpec


def build_core_workflow_templates() -> tuple[WorkflowTemplateSpec, ...]:
    return (
        WorkflowTemplateSpec(
            template_id="ops.http-healthcheck",
            version="1.0.0",
            schema_version="1.0.0",
            display_name="HTTP Healthcheck",
            description="Probe an HTTP endpoint through the workflow engine.",
            parameters_schema=HttpHealthcheckTemplateParams,
            labels=("ops", "healthcheck"),
            steps=(
                {
                    "id": "probe",
                    "kind": "healthcheck",
                    "payload": {
                        "target": "${target}",
                        "check_type": "http",
                        "expected_status": "${expected_status}",
                        "timeout": "${timeout}",
                    },
                },
            ),
        ),
        WorkflowTemplateSpec(
            template_id="edge.file-transfer",
            version="1.0.0",
            schema_version="1.0.0",
            display_name="File Transfer",
            description="Copy a file through the workflow engine with optional checksum verification.",
            parameters_schema=FileTransferTemplateParams,
            labels=("edge", "transfer"),
            steps=(
                {
                    "id": "transfer",
                    "kind": "file.transfer",
                    "payload": {
                        "src": "${src}",
                        "dst": "${dst}",
                        "overwrite": "${overwrite}",
                        "mkdir": "${mkdir}",
                        "verify_sha256": "${verify_sha256}",
                    },
                },
            ),
        ),
    )
