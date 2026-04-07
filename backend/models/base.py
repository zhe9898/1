"""Shared SQLAlchemy declarative base for canonical models."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for all canonical models."""
