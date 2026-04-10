from __future__ import annotations

import inspect

from backend.control_plane.adapters.kernel import list_kernel_capabilities


def test_list_kernel_capabilities_defaults_to_stable_only() -> None:
    default_value = inspect.signature(list_kernel_capabilities).parameters["stable_only"].default
    assert default_value is True
