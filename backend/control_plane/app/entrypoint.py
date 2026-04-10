"""Canonical FastAPI app entrypoint for the backend-driven control plane."""

from __future__ import annotations

from backend.control_plane.app.factory import create_app

app = create_app()

__all__ = ("app",)
