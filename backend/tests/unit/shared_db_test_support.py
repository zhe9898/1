from __future__ import annotations

from unittest.mock import MagicMock


def scalar_result(value: object | None) -> MagicMock:
    result = MagicMock()
    scalars = MagicMock()
    scalars.first.return_value = value
    scalars.all.return_value = [value] if value is not None else []
    result.scalars.return_value = scalars
    return result
