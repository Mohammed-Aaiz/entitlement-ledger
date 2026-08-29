"""Alembic environment for EntitlementLedger.

Loads DATABASE_URL from .env. Uses proper Alembic migrations for schema management.
"""
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool, text

# Load .env before reading DATABASE_URL
from dotenv import load_dotenv
_backend_dir = Path(__file__).resolve().parent.parent
load_dotenv(_backend_dir / ".env")

# Add backend to path so we can import models if needed
sys.path.insert(0, str(_backend_dir))

config = context.config

# Override sqlalchemy.url from DATABASE_URL env var
DATABASE_URL = os.environ.get("DATABASE_URL", "")
if DATABASE_URL:
    # Ensure URL uses psycopg2-style driver for SQLAlchemy Alembic
    # asyncpg URLs (postgresql+asyncpg://) need to be converted for Alembic's sync engine
    if "asyncpg" in DATABASE_URL:
        DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    config.set_main_option("sqlalchemy.url", DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

logger = None  # set after fileConfig


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode — connects to the real database."""
    url = config.get_main_option("sqlalchemy.url")
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=None,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
