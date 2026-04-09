"""Node resource schema and bootstrap helpers.

Extracted from nodes_helpers.py for maintainability.
Contains the ``_resource_schema`` FormSchema definition and
bootstrap receipt/command builders.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

from backend.api.action_contracts import ControlAction
from backend.api.ui_contracts import FormFieldOption, FormFieldSchema, FormSectionSchema, ResourceSchemaResponse
from backend.kernel.profiles.public_profile import DEFAULT_PRODUCT_NAME, normalize_gateway_profile, to_public_profile
from backend.kernel.topology.runtime_contracts import (
    control_plane_persona_options,
    executor_contract_options,
    node_executor_contract,
    persona_supports_android,
    persona_supports_ios,
)
from backend.models.node import Node

from .nodes_models import BootstrapReceipt


def _resource_schema() -> ResourceSchemaResponse:
    runtime_profile = normalize_gateway_profile(os.getenv("GATEWAY_PROFILE", "gateway-kernel"))
    return ResourceSchemaResponse(
        product=DEFAULT_PRODUCT_NAME,
        profile=to_public_profile(runtime_profile),
        runtime_profile=runtime_profile,
        resource="nodes",
        title="Nodes",
        description="Provision runners, issue one-time machine credentials, and govern fleet state from backend-owned contracts.",
        empty_state="No nodes match the current view.",
        policies={
            "ui_mode": "backend-driven",
            "resource_mode": "fleet-management",
            "list_query_filters": {
                "node_id": "exact",
                "node_type": "exact",
                "executor": "exact",
                "os": "exact",
                "zone": "exact",
                "enrollment_status": "exact",
                "drain_status": "derived",
                "heartbeat_state": "derived",
                "capacity_state": "derived",
                "attention": "derived-flag",
            },
            "submit_encoding": {
                "capabilities": "tags",
                "accepted_kinds": "tags",
                "worker_pools": "tags",
                "metadata": "json",
            },
            "runtime_contract_semantics": {
                "executor": "control-plane-persona",
                "executor_contract": "kernel-canonical-executor-contract",
                "accepted_kinds": "workload-kind-allowlist",
            },
            "secret_delivery": {
                "field": "node_token",
                "version_field": "auth_token_version",
                "visibility": "one-time",
            },
        },
        submit_action=ControlAction(
            key="provision",
            label="Provision Node",
            endpoint="/v1/nodes",
            method="POST",
            enabled=True,
            requires_admin=True,
        ),
        sections=[
            FormSectionSchema(
                id="identity",
                label="Identity",
                description="Create a fleet record and one-time machine credential for a new runner.",
                fields=[
                    FormFieldSchema(key="node_id", label="Node ID", required=True, placeholder="mac-mini-01"),
                    FormFieldSchema(key="name", label="Name", required=True, placeholder="Mac Mini 01"),
                    FormFieldSchema(
                        key="node_type",
                        label="Node Type",
                        input_type="select",
                        value="runner",
                        options=[
                            FormFieldOption(value="runner", label="Runner"),
                            FormFieldOption(value="sidecar", label="Sidecar"),
                            FormFieldOption(value="native-client", label="Native Client"),
                        ],
                    ),
                    FormFieldSchema(
                        key="address",
                        label="Address",
                        input_type="url",
                        placeholder="https://runner.example.invalid or http://10.0.0.12:9000",
                    ),
                ],
            ),
            FormSectionSchema(
                id="runtime",
                label="Runtime",
                description="Declare the execution contract the scheduler should trust before the first heartbeat arrives.",
                fields=[
                    FormFieldSchema(key="profile", label="Profile", value="go-runner", required=True),
                    FormFieldSchema(
                        key="executor",
                        label="Runtime Persona",
                        input_type="select",
                        value="go-native",
                        options=[FormFieldOption(value=value, label=label) for value, label in control_plane_persona_options()],
                    ),
                    FormFieldSchema(
                        key="executor_contract",
                        label="Executor Contract",
                        input_type="select",
                        value="",
                        options=[FormFieldOption(value="", label="Auto from Persona Contract")]
                        + [FormFieldOption(value=value, label=label) for value, label in executor_contract_options()],
                    ),
                    FormFieldSchema(
                        key="os",
                        label="OS",
                        input_type="select",
                        value="windows",
                        options=[
                            FormFieldOption(value="windows", label="Windows"),
                            FormFieldOption(value="darwin", label="macOS"),
                            FormFieldOption(value="linux", label="Linux"),
                            FormFieldOption(value="ios", label="iOS"),
                            FormFieldOption(value="android", label="Android"),
                            FormFieldOption(value="unknown", label="Unknown"),
                        ],
                    ),
                    FormFieldSchema(
                        key="arch",
                        label="Arch",
                        input_type="select",
                        value="amd64",
                        options=[
                            FormFieldOption(value="amd64", label="amd64"),
                            FormFieldOption(value="arm64", label="arm64"),
                            FormFieldOption(value="unknown", label="unknown"),
                        ],
                    ),
                    FormFieldSchema(key="zone", label="Zone", placeholder="home-lab"),
                    FormFieldSchema(key="protocol_version", label="Runner Contract", value="runner.v1"),
                    FormFieldSchema(key="lease_version", label="Lease Contract", value="job-lease.v1"),
                    FormFieldSchema(key="agent_version", label="Agent Version", placeholder="runner-agent 0.1.0"),
                    FormFieldSchema(key="max_concurrency", label="Max Concurrency", input_type="number", value=1),
                ],
            ),
            FormSectionSchema(
                id="resources",
                label="Resources",
                description="Declare explicit capacity so heterogeneous dispatch can respect executor and resource selectors.",
                fields=[
                    FormFieldSchema(key="cpu_cores", label="CPU Cores", input_type="number", value=0),
                    FormFieldSchema(key="memory_mb", label="Memory (MB)", input_type="number", value=0),
                    FormFieldSchema(key="gpu_vram_mb", label="GPU VRAM (MB)", input_type="number", value=0),
                    FormFieldSchema(key="storage_mb", label="Storage (MB)", input_type="number", value=0),
                ],
            ),
            FormSectionSchema(
                id="capabilities",
                label="Capabilities",
                description="Seed scheduler selectors and operator notes before the node comes online.",
                fields=[
                    FormFieldSchema(
                        key="capabilities",
                        label="Capabilities",
                        input_type="tags",
                        placeholder="job.execute,connector.invoke",
                    ),
                    FormFieldSchema(
                        key="accepted_kinds",
                        label="Accepted Kinds",
                        input_type="tags",
                        placeholder="connector.invoke,shell.exec",
                    ),
                    FormFieldSchema(
                        key="worker_pools",
                        label="Worker Pools",
                        input_type="tags",
                        placeholder="interactive,batch",
                    ),
                    FormFieldSchema(
                        key="metadata",
                        label="Metadata",
                        input_type="json",
                        value="{}",
                        placeholder='{"runtime":"go","managed_by":"console"}',
                    ),
                ],
            ),
        ],
    )


# 鈹€鈹€ Bootstrap helpers 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€


def _build_bootstrap_gateway_base_url() -> str:
    gateway_base_url = os.getenv("NODE_BOOTSTRAP_GATEWAY_BASE_URL", "<gateway-base-url>").strip()
    return gateway_base_url or "<gateway-base-url>"


def _should_embed_bootstrap_token() -> bool:
    return os.getenv("NODE_BOOTSTRAP_EMBED_TOKEN", "").strip().lower() in {"1", "true", "yes", "on"}


def _bootstrap_token_value(node_token: str) -> str:
    if _should_embed_bootstrap_token():
        return node_token
    return "<paste-one-time-node-token-here>"


def _bootstrap_requires_insecure_http_opt_in(gateway_base_url: str) -> bool:
    parsed = urlparse(gateway_base_url)
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def _build_bootstrap_commands(node: Node, node_token: str) -> dict[str, str]:
    gateway_base_url = _build_bootstrap_gateway_base_url()
    token_value = _bootstrap_token_value(node_token)
    executor_contract = node_executor_contract(node)
    powershell_lines = [
        f'$env:RUNNER_NODE_ID="{node.node_id}"',
        f'$env:RUNNER_TENANT_ID="{node.tenant_id}"',
        f'$env:NODE_TOKEN="{token_value}"',
        f'$env:GATEWAY_BASE_URL="{gateway_base_url}"',
        f'$env:RUNNER_EXECUTOR="{node.executor}"',
        f'$env:RUNNER_EXECUTOR_CONTRACT="{executor_contract}"',
    ]
    unix_lines = [
        f'export RUNNER_NODE_ID="{node.node_id}"',
        f'export RUNNER_TENANT_ID="{node.tenant_id}"',
        f'export NODE_TOKEN="{token_value}"',
        f'export GATEWAY_BASE_URL="{gateway_base_url}"',
        f'export RUNNER_EXECUTOR="{node.executor}"',
        f'export RUNNER_EXECUTOR_CONTRACT="{executor_contract}"',
    ]
    if _bootstrap_requires_insecure_http_opt_in(gateway_base_url):
        powershell_lines.append('$env:RUNNER_ALLOW_INSECURE_HTTP="true"')
        unix_lines.append('export RUNNER_ALLOW_INSECURE_HTTP="true"')
    powershell_lines.append(".\\runner-agent.exe")
    unix_lines.append("./runner-agent")
    return {
        "powershell": "\n".join(powershell_lines),
        "unix": "\n".join(unix_lines),
    }


def _bootstrap_notes() -> list[str]:
    return [
        ("Replace <gateway-base-url> with the externally reachable gateway base URL. " "The runner appends the /api/v1/... control-plane paths itself."),
        (
            "The machine channel requires HTTPS by default. Only local development may opt "
            "into http://127.0.0.1... together with RUNNER_ALLOW_INSECURE_HTTP=true."
        ),
        (
            "RUNNER_EXECUTOR is the control-plane persona, while RUNNER_EXECUTOR_CONTRACT pins the canonical "
            "execution contract used for workload-kind compatibility."
        ),
        ("Keep RUNNER_TENANT_ID alongside the node token so the machine channel is scoped " "to the tenant context before authentication completes."),
        (
            "The node token is one-time display material and should only be stored on the "
            "target host or native client. It will not be shown again after this response closes."
        ),
    ]


IOS_NATIVE_NOTES = [
    "Embed this JSON into the iOS native client configuration so HealthKit, " "notifications, and local device bridges reuse the control-plane contract."
]

ANDROID_NATIVE_NOTES = [
    (
        "Embed this JSON into the Android native client configuration so Health Connect, "
        "notifications, and local device bridges reuse the control-plane contract."
    )
]


def _build_bootstrap_receipts(node: Node, node_token: str) -> list[BootstrapReceipt]:
    gateway_base_url = _build_bootstrap_gateway_base_url()
    receipts: list[BootstrapReceipt] = []
    bootstrap_commands = _build_bootstrap_commands(node, node_token)

    if node.node_type != "native-client" and node.os in {"windows", "unknown"}:
        receipts.append(
            BootstrapReceipt(
                key="powershell",
                label="Windows / PowerShell",
                platform="windows",
                kind="command",
                content=bootstrap_commands["powershell"],
                notes=["Use this bootstrap command on Windows runner hosts."],
            )
        )
    if node.node_type != "native-client" and node.os in {"darwin", "linux", "unknown"}:
        receipts.append(
            BootstrapReceipt(
                key="unix",
                label="macOS / Linux",
                platform="unix",
                kind="command",
                content=bootstrap_commands["unix"],
                notes=["Use this bootstrap command on macOS or Linux runner hosts."],
            )
        )

    native_common = {
        "node_id": node.node_id,
        "tenant_id": node.tenant_id,
        "node_token": _bootstrap_token_value(node_token),
        "gateway_base_url": gateway_base_url,
        "executor": node.executor,
        "executor_contract": node_executor_contract(node),
        "zone": node.zone or "mobile",
    }
    if node.node_type == "native-client" or node.os in {"ios", "android"} or persona_supports_ios(node.executor) or persona_supports_android(node.executor):
        if node.os in {"ios", "unknown"} or persona_supports_ios(node.executor) or node.node_type == "native-client":
            receipts.append(
                BootstrapReceipt(
                    key="ios-native",
                    label="iOS Native Client",
                    platform="ios",
                    kind="json-config",
                    content=(
                        "{\n"
                        f'  "node_id": "{native_common["node_id"]}",\n'
                        f'  "tenant_id": "{native_common["tenant_id"]}",\n'
                        f'  "node_token": "{native_common["node_token"]}",\n'
                        f'  "gateway_base_url": "{native_common["gateway_base_url"]}",\n'
                        '  "native_bridge": ["health.ingest", "notify.push", "device.local"],\n'
                        f'  "executor": "{native_common["executor"]}",\n'
                        f'  "executor_contract": "{native_common["executor_contract"]}",\n'
                        f'  "zone": "{native_common["zone"]}"\n'
                        "}"
                    ),
                    notes=IOS_NATIVE_NOTES,
                )
            )
        if node.os in {"android", "unknown"} or persona_supports_android(node.executor) or node.node_type == "native-client":
            receipts.append(
                BootstrapReceipt(
                    key="android-native",
                    label="Android Native Client",
                    platform="android",
                    kind="json-config",
                    content=(
                        "{\n"
                        f'  "node_id": "{native_common["node_id"]}",\n'
                        f'  "tenant_id": "{native_common["tenant_id"]}",\n'
                        f'  "node_token": "{native_common["node_token"]}",\n'
                        f'  "gateway_base_url": "{native_common["gateway_base_url"]}",\n'
                        '  "native_bridge": ["health.ingest", "notify.push", "device.local"],\n'
                        f'  "executor": "{native_common["executor"]}",\n'
                        f'  "executor_contract": "{native_common["executor_contract"]}",\n'
                        f'  "zone": "{native_common["zone"]}"\n'
                        "}"
                    ),
                    notes=ANDROID_NATIVE_NOTES,
                )
            )
    return receipts
