"""Alembic environment.

* URL comes from ``DATABASE_URL`` (same variable the app uses).
* ``target_metadata`` is the app's declarative metadata, so autogenerate sees
  every model.
* ``compare_type`` / ``compare_server_default`` are on: a mismatch between the
  models and the database should show up as a pending migration.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool, text

from alembic import context

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import get_settings
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.sync_database_url)

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):
    """Skip objects Alembic must not manage."""
    if type_ == "table" and name in {"spatial_ref_sys"}:
        return False
    return not (type_ == "index" and name and name.startswith("alembic_"))


def run_migrations_offline() -> None:
    context.configure(
        url=settings.sync_database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # Extensions must exist before the enum/trgm indexes are created.
        connection.execute(text('CREATE EXTENSION IF NOT EXISTS "pg_trgm"'))
        connection.commit()

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_object=include_object,
            version_table="alembic_version",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
