#!/usr/bin/env python3
"""Compatibility exports for the canonical IaC compiler package surface."""

from __future__ import annotations

from scripts.compiler.secrets_manager import generate_secrets
from scripts.iac_core.lint import config_lint

__all__ = ["config_lint", "generate_secrets"]
