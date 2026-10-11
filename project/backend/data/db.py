"""Compatibility module delegating database connections to services.storage."""

from services.storage import (
    SQLITE_DB_PATH,
    ensure_sqlite_directory,
    get_postgres_connection,
    get_sqlite_connection,
    init_database_tables,
    is_postgres_active,
)

__all__ = [
    "SQLITE_DB_PATH",
    "ensure_sqlite_directory",
    "get_postgres_connection",
    "get_sqlite_connection",
    "init_database_tables",
    "is_postgres_active",
]
