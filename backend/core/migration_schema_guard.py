"""Reusable schema-inspection helpers for idempotent Alembic migrations."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection


def _normalized_columns(columns: Sequence[str] | None) -> tuple[str, ...]:
    return tuple(str(column) for column in (columns or ()))


@dataclass
class SchemaGuard:
    """Thin wrapper around SQLAlchemy inspector with mutation-aware refreshes."""

    bind: Connection
    inspector: sa.Inspector = field(init=False)

    def __post_init__(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        self.inspector = sa.inspect(self.bind)

    def has_table(self, table_name: str) -> bool:
        return self.inspector.has_table(table_name)

    def has_column(self, table_name: str, column_name: str) -> bool:
        if not self.has_table(table_name):
            return False
        return any(column["name"] == column_name for column in self.inspector.get_columns(table_name))

    def has_index_named(self, table_name: str, index_name: str) -> bool:
        if not self.has_table(table_name):
            return False
        return any(index["name"] == index_name for index in self.inspector.get_indexes(table_name))

    def has_equivalent_index(
        self,
        table_name: str,
        columns: Sequence[str],
        *,
        unique: bool | None = None,
    ) -> bool:
        if not self.has_table(table_name):
            return False
        target_columns = _normalized_columns(columns)
        for index in self.inspector.get_indexes(table_name):
            current_columns = _normalized_columns(index.get("column_names"))
            if current_columns != target_columns:
                continue
            if unique is None or bool(index.get("unique")) == unique:
                return True
        return False

    def has_unique_constraint_named(self, table_name: str, constraint_name: str) -> bool:
        if not self.has_table(table_name):
            return False
        return any(
            constraint["name"] == constraint_name
            for constraint in self.inspector.get_unique_constraints(table_name)
        )

    def has_equivalent_unique_constraint(self, table_name: str, columns: Sequence[str]) -> bool:
        if not self.has_table(table_name):
            return False
        target_columns = frozenset(_normalized_columns(columns))
        for constraint in self.inspector.get_unique_constraints(table_name):
            if frozenset(_normalized_columns(constraint.get("column_names"))) == target_columns:
                return True
        return False

    def has_foreign_key_named(self, table_name: str, constraint_name: str) -> bool:
        if not self.has_table(table_name):
            return False
        return any(foreign_key.get("name") == constraint_name for foreign_key in self.inspector.get_foreign_keys(table_name))

    def has_equivalent_foreign_key(
        self,
        table_name: str,
        *,
        constrained_columns: Sequence[str],
        referred_table: str,
        referred_columns: Sequence[str],
    ) -> bool:
        if not self.has_table(table_name):
            return False
        target_columns = _normalized_columns(constrained_columns)
        target_referred_columns = _normalized_columns(referred_columns)
        for foreign_key in self.inspector.get_foreign_keys(table_name):
            if foreign_key.get("referred_table") != referred_table:
                continue
            if _normalized_columns(foreign_key.get("constrained_columns")) != target_columns:
                continue
            if _normalized_columns(foreign_key.get("referred_columns")) != target_referred_columns:
                continue
            return True
        return False

    def add_column_if_missing(self, table_name: str, column: sa.Column[Any]) -> None:
        if self.has_table(table_name) and not self.has_column(table_name, str(column.name)):
            op.add_column(table_name, column)
            self.refresh()

    def create_index_if_missing(
        self,
        table_name: str,
        index_name: str,
        columns: Sequence[str],
        *,
        unique: bool = False,
        postgresql_where: sa.ClauseElement | None = None,
        **kwargs: Any,
    ) -> None:
        if not self.has_table(table_name):
            return
        if self.has_index_named(table_name, index_name) or self.has_equivalent_index(table_name, columns, unique=unique):
            return
        op.create_index(
            index_name,
            table_name,
            list(columns),
            unique=unique,
            postgresql_where=postgresql_where,
            **kwargs,
        )
        self.refresh()

    def create_unique_constraint_if_missing(
        self,
        table_name: str,
        constraint_name: str,
        columns: Sequence[str],
    ) -> None:
        if not self.has_table(table_name):
            return
        if self.has_unique_constraint_named(table_name, constraint_name) or self.has_equivalent_unique_constraint(table_name, columns):
            return
        op.create_unique_constraint(constraint_name, table_name, list(columns))
        self.refresh()

    def create_foreign_key_if_missing(
        self,
        table_name: str,
        constraint_name: str,
        referred_table: str,
        local_columns: Sequence[str],
        remote_columns: Sequence[str],
        *,
        ondelete: str | None = None,
    ) -> None:
        if not self.has_table(table_name):
            return
        if self.has_foreign_key_named(table_name, constraint_name) or self.has_equivalent_foreign_key(
            table_name,
            constrained_columns=local_columns,
            referred_table=referred_table,
            referred_columns=remote_columns,
        ):
            return
        op.create_foreign_key(
            constraint_name,
            table_name,
            referred_table,
            list(local_columns),
            list(remote_columns),
            ondelete=ondelete,
        )
        self.refresh()
