import os

from backend.api.action_contracts import ControlAction
from backend.api.ui_contracts import FormFieldOption, FormFieldSchema, FormSectionSchema, ResourceSchemaResponse
from backend.core.gateway_profile import DEFAULT_PRODUCT_NAME, normalize_gateway_profile, to_public_profile


def _resource_schema() -> ResourceSchemaResponse:
    runtime_profile = normalize_gateway_profile(os.getenv("GATEWAY_PROFILE", "gateway-kernel"))
    return ResourceSchemaResponse(
        product=DEFAULT_PRODUCT_NAME,
        profile=to_public_profile(runtime_profile),
        runtime_profile=runtime_profile,
        resource="jobs",
        title="Jobs",
        description="Queue, inspect, and remediate scheduler work from backend-owned contracts.",
        empty_state="No jobs match the current view.",
        policies={
            "ui_mode": "backend-driven",
            "resource_mode": "operations-console",
            "list_query_filters": {
                "job_id": "exact",
                "status": "status-view",
                "lease_state": "lease-state-view",
                "priority_bucket": "bucket",
                "target_executor": "exact",
                "target_zone": "exact",
                "required_capability": "contains",
            },
            "submit_encoding": {
                "required_capabilities": "tags",
                "payload": "json",
            },
            "submission_security": {
                "baseline_scope": "write:jobs",
                "privileged_scope": "admin:jobs",
                "default_console_kind": "noop",
            },
        },
        submit_action=ControlAction(
            key="create",
            label="Queue Job",
            endpoint="/v1/jobs",
            method="POST",
            enabled=True,
            requires_admin=False,
            reason=None,
            confirmation=None,
            fields=[],
        ),
        sections=[
            FormSectionSchema(
                id="identity",
                label="Job Identity",
                description="Job kind and source define the control-plane contract.",
                fields=[
                    FormFieldSchema(key="kind", label="Kind", required=True, value="noop"),
                    FormFieldSchema(key="connector_id", label="Connector", placeholder="optional"),
                    FormFieldSchema(key="idempotency_key", label="Idempotency Key", placeholder="optional"),
                    FormFieldSchema(key="source", label="Source", value="console", placeholder="console"),
                ],
            ),
            FormSectionSchema(
                id="scheduling",
                label="Scheduling",
                description="Selectors and priority determine placement.",
                fields=[
                    FormFieldSchema(key="priority", label="Priority", input_type="number", value=50, required=True),
                    FormFieldSchema(
                        key="queue_class",
                        label="Queue Class",
                        input_type="select",
                        options=[
                            FormFieldOption(value="", label="Auto"),
                            FormFieldOption(value="realtime", label="Realtime"),
                            FormFieldOption(value="interactive", label="Interactive"),
                            FormFieldOption(value="batch", label="Batch"),
                            FormFieldOption(value="gpu-heavy", label="GPU Heavy"),
                            FormFieldOption(value="analytics", label="Analytics"),
                        ],
                    ),
                    FormFieldSchema(key="worker_pool", label="Worker Pool", placeholder="optional override"),
                    FormFieldSchema(key="lease_seconds", label="Lease Seconds", input_type="number", value=30, required=True),
                    FormFieldSchema(key="timeout_seconds", label="Timeout Seconds", input_type="number", value=300, required=True),
                    FormFieldSchema(key="max_retries", label="Max Retries", input_type="number", value=0, required=True),
                    FormFieldSchema(key="estimated_duration_s", label="Estimated Duration (s)", input_type="number"),
                    FormFieldSchema(
                        key="target_os",
                        label="Target OS",
                        input_type="select",
                        options=[
                            FormFieldOption(value="", label="Any"),
                            FormFieldOption(value="windows", label="Windows"),
                            FormFieldOption(value="darwin", label="macOS"),
                            FormFieldOption(value="linux", label="Linux"),
                        ],
                    ),
                    FormFieldSchema(
                        key="target_arch",
                        label="Target Arch",
                        input_type="select",
                        options=[
                            FormFieldOption(value="", label="Any"),
                            FormFieldOption(value="amd64", label="amd64"),
                            FormFieldOption(value="arm64", label="arm64"),
                        ],
                    ),
                    FormFieldSchema(
                        key="target_executor",
                        label="Target Executor",
                        input_type="select",
                        options=[
                            FormFieldOption(value="", label="Any"),
                            FormFieldOption(value="go-native", label="Go Native"),
                            FormFieldOption(value="python-runner", label="Python Runner"),
                            FormFieldOption(value="shell", label="Shell"),
                            FormFieldOption(value="swift-native", label="Swift Native"),
                            FormFieldOption(value="kotlin-native", label="Kotlin Native"),
                            FormFieldOption(value="vector-worker", label="Vector Worker"),
                            FormFieldOption(value="search-service", label="Search Service"),
                        ],
                    ),
                    FormFieldSchema(key="target_zone", label="Target Zone", placeholder="optional"),
                    FormFieldSchema(
                        key="required_capabilities",
                        label="Required Capabilities",
                        input_type="tags",
                        placeholder="comma,separated,capabilities",
                    ),
                    FormFieldSchema(key="required_cpu_cores", label="Required CPU Cores", input_type="number"),
                    FormFieldSchema(key="required_memory_mb", label="Required Memory (MB)", input_type="number"),
                    FormFieldSchema(key="required_gpu_vram_mb", label="Required GPU VRAM (MB)", input_type="number"),
                    FormFieldSchema(key="required_storage_mb", label="Required Storage (MB)", input_type="number"),
                ],
            ),
            FormSectionSchema(
                id="payload",
                label="Payload",
                description="Payload is submitted as JSON to the control plane.",
                fields=[
                    FormFieldSchema(
                        key="payload",
                        label="Payload JSON",
                        input_type="json",
                        required=False,
                        value="{}",
                        placeholder='{"action":"ping"}',
                    )
                ],
            ),
        ],
    )
