#!/usr/bin/env python3
"""Canonical process entrypoint for the backend-driven control plane."""

from __future__ import annotations

from backend.control_plane.app.entrypoint import app

__all__ = ["app"]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.control_plane.app.entrypoint:app", host="0.0.0.0", port=8000, reload=True)  # nosec
