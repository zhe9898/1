"""Canonical control-plane app entrypoint."""

from __future__ import annotations

from backend.control_plane.app.factory import create_app

app = create_app()

__all__ = ("app",)
