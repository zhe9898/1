"""Kernel capability registry exports."""

from .registry import KernelCapability, capability_keys, get_capability, list_capabilities

__all__ = (
    "KernelCapability",
    "capability_keys",
    "get_capability",
    "list_capabilities",
)
