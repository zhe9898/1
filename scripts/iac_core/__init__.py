"""
iac_core is the canonical ZEN70 IaC compiler core library.

It backs the single supported compiler entrypoint, `scripts/compiler.py`,
and provides the typed load / merge / migrate / lint / secrets / render
pipeline used to turn `system.yaml` into deterministic release artifacts.
"""

from __future__ import annotations
