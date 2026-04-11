"""Builtin compute-oriented job kind catalog."""

from __future__ import annotations

from backend.extensions.job_kind_registry import (
    ContainerRunPayload,
    ContainerRunResult,
    MLInferencePayload,
    MLInferenceResult,
    MediaTranscodePayload,
    MediaTranscodeResult,
    ScriptRunPayload,
    ScriptRunResult,
    ShellExecPayload,
    ShellExecResult,
    WasmRunPayload,
    WasmRunResult,
)

from .extension_contracts import JobKindSpec


def build_core_compute_job_kinds() -> tuple[JobKindSpec, ...]:
    return (
        JobKindSpec(
            "shell.exec",
            payload_schema=ShellExecPayload,
            result_schema=ShellExecResult,
            description="Execute a shell command on a worker node.",
        ),
        JobKindSpec("container.run", payload_schema=ContainerRunPayload, result_schema=ContainerRunResult, description="Run a container image."),
        JobKindSpec("ml.inference", payload_schema=MLInferencePayload, result_schema=MLInferenceResult, description="Run ML inference."),
        JobKindSpec(
            "media.transcode",
            payload_schema=MediaTranscodePayload,
            result_schema=MediaTranscodeResult,
            description="Run media transcode workloads.",
        ),
        JobKindSpec("script.run", payload_schema=ScriptRunPayload, result_schema=ScriptRunResult, description="Run an interpreted script."),
        JobKindSpec("wasm.run", payload_schema=WasmRunPayload, result_schema=WasmRunResult, stability="beta", description="Run WebAssembly workloads."),
    )
