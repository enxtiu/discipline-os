"""Declarative base, custom column types and shared mixins.

Conventions enforced here:

* every table is ``user_id``-scoped (see :class:`UserScopedMixin`);
* every table has ``created_at`` / ``updated_at`` in UTC (``timestamptz``);
* primary keys are client-generatable UUIDv7;
* enums are stored as ``varchar`` + ``CHECK`` (``native_enum=False``) so that
  adding a member later is a plain constraint swap instead of ``ALTER TYPE``.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, MetaData, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column
from sqlalchemy.types import JSON

from app.core.ids import new_id
from app.core.tz import utcnow

__all__ = [
    "Base",
    "JsonType",
    "TimestampMixin",
    "TzDateTime",
    "UiEnum",
    "UserScopedMixin",
    "UuidPkMixin",
    "UuidType",
    "enum_col",
    "uuid_fk",
]

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


# Native ``uuid`` on PostgreSQL, ``CHAR(32)`` elsewhere (tests, tooling) — the
# standard SQLAlchemy 2.0 type keeps Alembic migrations self-contained and free
# of app imports.
UuidType = Uuid()

JsonType = JSONB().with_variant(JSON(), "sqlite")
TzDateTime = DateTime(timezone=True)


class UiEnum(str, enum.Enum):
    """Base for string enums; ``__str__`` returns the raw value (JSON friendly)."""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return str(self.value)


def enum_col(enum_cls: type[enum.Enum], name: str, *, length: int = 32) -> sa.Enum:
    """VARCHAR + CHECK enum so future members don't need ``ALTER TYPE``."""
    return sa.Enum(
        enum_cls,
        name=name,
        native_enum=False,
        length=length,
        validate_strings=True,
        create_constraint=True,
        values_callable=lambda cls: [member.value for member in cls],
    )


def uuid_fk(
    target: str,
    *,
    ondelete: str = "CASCADE",
    nullable: bool = False,
    index: bool = True,
) -> Any:
    return mapped_column(
        UuidType,
        ForeignKey(target, ondelete=ondelete),
        nullable=nullable,
        index=index,
    )


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    def to_dict(self) -> dict[str, Any]:
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        pk = getattr(self, "id", None)
        return f"<{type(self).__name__} id={pk}>"


class UuidPkMixin:
    """Client-generatable primary key."""

    id: Mapped[uuid.UUID] = mapped_column(UuidType, primary_key=True, default=new_id)


class TimestampMixin:
    """``created_at`` / ``updated_at`` in UTC.

    The values are generated **in Python** (``default=utcnow``) *and* have a
    server default for raw SQL. Python-side generation matters in an async app:
    a server-side ``onupdate`` expires the attribute after every UPDATE, and the
    next read would try to lazy-load it — which raises ``MissingGreenlet``
    outside SQLAlchemy's greenlet. With a Python callable the value is simply
    there after ``flush()``.
    """

    created_at: Mapped[datetime] = mapped_column(TzDateTime, default=utcnow, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TzDateTime, default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False
    )


class UserScopedMixin(TimestampMixin):
    """``user_id`` FK + timestamps.  Every domain table starts with this."""

    @declared_attr
    def user_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            UuidType,
            ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
