"""Builtin integration and transfer job kind catalog."""

from __future__ import annotations

from backend.extensions.job_kind_registry import DataSyncPayload, DataSyncResult, FileTransferPayload, FileTransferResult, HttpRequestPayload, HttpRequestResult

from .extension_contracts import JobKindSpec


def build_core_integration_job_kinds() -> tuple[JobKindSpec, ...]:
    return (
        JobKindSpec("http.request", payload_schema=HttpRequestPayload, result_schema=HttpRequestResult, description="Execute an HTTP request."),
        JobKindSpec("data.sync", payload_schema=DataSyncPayload, result_schema=DataSyncResult, description="Synchronise data across boundaries."),
        JobKindSpec(
            "file.transfer",
            payload_schema=FileTransferPayload,
            result_schema=FileTransferResult,
            description="Transfer files with integrity checks.",
        ),
        JobKindSpec("connector.invoke", description="Control-plane connector invocation kind kept permissive for compatibility."),
    )
