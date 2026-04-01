"""Executor Contract Registry — formal capability declarations.

Addresses the "execution layer not thick enough" gap by formalising
what each executor type can do, what resource profile it requires,
and what job kinds it supports.

The registry is populated at startup from ``system.yaml`` (or hardcoded
defaults) and consulted at:

1. **Node registration** — validate executor field against known contracts.
2. **Dispatch scoring**  — bonus for exact executor match, penalty if
   contract declares the job kind unsupported.
3. **Console / diagnostics** — surface executor capability matrix.

Usage in system.yaml::

    scheduling:
      executor_contracts:
        docker:
          description: "Container-based execution via Docker Engine"
          supported_kinds: ["shell.exec", "container.run", "http.request"]
          requires_gpu: false
          min_memory_mb: 256
        process:
          description: "Direct process execution on host OS"
          supported_kinds: ["shell.exec", "script.run"]
          requires_gpu: false
          min_memory_mb: 64
        gpu:
          description: "GPU-accelerated workloads (CUDA/OpenCL)"
          supported_kinds: ["ml.inference", "media.transcode"]
          requires_gpu: true
          min_memory_mb: 1024
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ExecutorContract:
    """Immutable capability declaration for an executor type."""

    name: str
    description: str = ""
    supported_kinds: frozenset[str] = field(default_factory=frozenset)
    requires_gpu: bool = False
    min_memory_mb: int = 0
    min_cpu_cores: int = 0
    max_concurrency_hint: int = 0  # 0 = no recommendation
    stability_tier: str = "ga"  # ga | beta | experimental


# ── Default contracts (always available) ──────────────────────────────

_DEFAULT_CONTRACTS: dict[str, ExecutorContract] = {
    "docker": ExecutorContract(
        name="docker",
        description="Container-based execution via Docker Engine",
        supported_kinds=frozenset({"shell.exec", "container.run", "http.request", "healthcheck"}),
        min_memory_mb=256,
        stability_tier="ga",
    ),
    "process": ExecutorContract(
        name="process",
        description="Direct process execution on host OS",
        supported_kinds=frozenset({"shell.exec", "script.run"}),
        min_memory_mb=64,
        stability_tier="ga",
    ),
    "gpu": ExecutorContract(
        name="gpu",
        description="GPU-accelerated workloads (CUDA/OpenCL)",
        supported_kinds=frozenset({"ml.inference", "media.transcode", "shell.exec"}),
        requires_gpu=True,
        min_memory_mb=1024,
        stability_tier="ga",
    ),
    "wasm": ExecutorContract(
        name="wasm",
        description="WebAssembly sandboxed execution",
        supported_kinds=frozenset({"wasm.run"}),
        min_memory_mb=128,
        stability_tier="beta",
    ),
    "k8s": ExecutorContract(
        name="k8s",
        description="Kubernetes pod-based execution via kubelet API",
        supported_kinds=frozenset(
            {
                "container.run",
                "shell.exec",
                "http.request",
                "healthcheck",
                "cron.tick",
                "data.sync",
            }
        ),
        min_memory_mb=512,
        min_cpu_cores=1,
        max_concurrency_hint=32,
        stability_tier="ga",
    ),
    "remote-ssh": ExecutorContract(
        name="remote-ssh",
        description="Remote execution via SSH tunnel to edge device",
        supported_kinds=frozenset(
            {
                "shell.exec",
                "script.run",
                "healthcheck",
                "iot.collect",
                "data.sync",
            }
        ),
        min_memory_mb=128,
        stability_tier="ga",
    ),
    "edge-native": ExecutorContract(
        name="edge-native",
        description="Lightweight on-device executor for constrained IoT/edge nodes",
        supported_kinds=frozenset(
            {
                "shell.exec",
                "iot.collect",
                "healthcheck",
                "data.sync",
                "cron.tick",
            }
        ),
        min_memory_mb=32,
        max_concurrency_hint=4,
        stability_tier="ga",
    ),
    "unknown": ExecutorContract(
        name="unknown",
        description="Fallback for unregistered executors — accepts all kinds",
        supported_kinds=frozenset(),  # empty = accept all (permissive fallback)
        stability_tier="ga",
    ),
}


class ExecutorRegistry:
    """Singleton registry of executor contracts.

    Loaded from system.yaml on first access, with hardcoded defaults
    as the base layer.
    """

    _contracts: dict[str, ExecutorContract] = {}
    _loaded: bool = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        # Start with defaults
        self._contracts = dict(_DEFAULT_CONTRACTS)
        # Overlay from system.yaml
        try:
            from pathlib import Path

            import yaml  # type: ignore[import-untyped, unused-ignore]

            config = yaml.safe_load(Path("system.yaml").read_text(encoding="utf-8"))
            raw = (config.get("scheduling", {}) or {}).get("executor_contracts", {}) or {}
            for name, cfg in raw.items():
                if not isinstance(cfg, dict):
                    continue
                kinds_raw = cfg.get("supported_kinds", []) or []
                self._contracts[name] = ExecutorContract(
                    name=name,
                    description=str(cfg.get("description", "")),
                    supported_kinds=frozenset(str(k) for k in kinds_raw),
                    requires_gpu=bool(cfg.get("requires_gpu", False)),
                    min_memory_mb=int(cfg.get("min_memory_mb", 0)),
                    min_cpu_cores=int(cfg.get("min_cpu_cores", 0)),
                    max_concurrency_hint=int(cfg.get("max_concurrency_hint", 0)),
                    stability_tier=str(cfg.get("stability_tier", "ga")),
                )
        except Exception:
            logger.debug("No executor_contracts in system.yaml, using defaults", exc_info=True)
        self._loaded = True

    def get(self, executor_name: str) -> ExecutorContract | None:
        """Look up contract by executor name."""
        self._ensure_loaded()
        return self._contracts.get(executor_name)

    def get_or_default(self, executor_name: str) -> ExecutorContract:
        """Look up contract, falling back to 'unknown' contract."""
        return self.get(executor_name) or self._contracts.get("unknown", _DEFAULT_CONTRACTS["unknown"])

    def all_contracts(self) -> dict[str, ExecutorContract]:
        """Return all registered contracts (immutable copy)."""
        self._ensure_loaded()
        return dict(self._contracts)

    def register(self, contract: ExecutorContract) -> None:
        """Dynamically register or override a contract."""
        self._ensure_loaded()
        self._contracts[contract.name] = contract

    def validate_node_executor(
        self,
        executor: str,
        *,
        memory_mb: int = 0,
        cpu_cores: int = 0,
        gpu_vram_mb: int = 0,
    ) -> list[str]:
        """Validate node capabilities against executor contract.

        Returns list of validation warnings (empty = all ok).
        """
        contract = self.get(executor)
        if contract is None:
            return [f"executor '{executor}' not in registry — permissive fallback"]

        warnings: list[str] = []
        if contract.requires_gpu and gpu_vram_mb <= 0:
            warnings.append(f"executor '{executor}' requires GPU but node reports 0 VRAM")
        if contract.min_memory_mb > 0 and memory_mb < contract.min_memory_mb:
            warnings.append(f"executor '{executor}' recommends >={contract.min_memory_mb}MB memory, node has {memory_mb}MB")
        if contract.min_cpu_cores > 0 and cpu_cores < contract.min_cpu_cores:
            warnings.append(f"executor '{executor}' recommends >={contract.min_cpu_cores} CPU cores, node has {cpu_cores}")
        return warnings

    def is_kind_supported(self, executor: str, kind: str) -> bool | None:
        """Check if an executor supports a job kind.

        Returns True/False if contract exists and has supported_kinds,
        or None if contract is unknown or has no kind restrictions.
        """
        contract = self.get(executor)
        if contract is None:
            return None  # unknown executor → permissive
        if not contract.supported_kinds:
            return None  # empty set = accept all
        return kind in contract.supported_kinds

    def kind_compatible(self, executor: str, kind: str) -> tuple[bool, str]:
        """Hard compatibility check for dispatch pre-filter.

        Returns (compatible, reason). A *None* result from is_kind_supported
        is treated as compatible (permissive for unknown executors).
        """
        result = self.is_kind_supported(executor, kind)
        if result is None:
            return True, ""
        if result:
            return True, ""
        contract = self.get(executor) or self.get_or_default(executor)
        return False, f"executor '{executor}' contract excludes kind '{kind}' (supported: {sorted(contract.supported_kinds)})"

    def reload(self) -> None:
        """Force re-read from config."""
        self._loaded = False
        self._ensure_loaded()


# ── Module-level singleton ────────────────────────────────────────────

_registry: ExecutorRegistry | None = None


def get_executor_registry() -> ExecutorRegistry:
    """Return the process-wide ExecutorRegistry singleton."""
    global _registry
    if _registry is None:
        _registry = ExecutorRegistry()
    return _registry
