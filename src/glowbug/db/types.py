from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import Numeric, String
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class Money(TypeDecorator[Decimal]):
    """Exact decimal storage.

    PostgreSQL uses NUMERIC. SQLite has no exact decimal type, so values are
    stored as strings there to keep tests and local development lossless.
    """

    impl = Numeric(38, 18, asdecimal=True)
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(64))
        return dialect.type_descriptor(Numeric(38, 18, asdecimal=True))

    def process_bind_param(
        self,
        value: Decimal | str | int | None,
        dialect: Dialect,
    ) -> Decimal | str | None:
        if value is None:
            return None

        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
        if dialect.name == "sqlite":
            return format(decimal_value, "f")
        return decimal_value

    def process_result_value(
        self,
        value: str | Decimal | None,
        dialect: Dialect,
    ) -> Decimal | None:
        if value is None:
            return None
        return value if isinstance(value, Decimal) else Decimal(str(value))
