"""Database adapters. Code uses '?' placeholders; adapters translate for their driver."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from brain.storage.schema import MIGRATIONS


class SqliteDatabase:
    """Local development / tests. A file path, or ':memory:'."""
    dialect = "sqlite"

    def __init__(self, path: str | Path = ":memory:"):
        # check_same_thread=False: the local server answers each request in its own thread, and the
        # scheduler runs in another. All of them use the database ONLY while holding the app's single
        # lock (api/server.py), so access is never concurrent. (Found on her computer, 25.11.)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql: str, params: tuple = ()):
        return self.conn.execute(sql, params)

    def query(self, sql: str, params: tuple = ()) -> list[tuple]:
        return self.conn.execute(sql, params).fetchall()

    def transaction(self):
        return self.conn          # sqlite3 connection as context manager = commit / rollback

    def close(self):
        self.conn.close()


class PostgresDatabase:
    """APPROVED target (spec §36). NOT TESTED in this environment: needs the psycopg driver
    and a PostgreSQL server – server location and key management are OPEN (Q-B)."""
    dialect = "postgres"

    def __init__(self, dsn: str):
        try:
            import psycopg
        except ImportError as e:
            raise RuntimeError("PostgreSQL needs the 'psycopg' package (not installed)") from e
        self.conn = psycopg.connect(dsn)

    def execute(self, sql: str, params: tuple = ()):
        return self.conn.execute(sql.replace("?", "%s"), params)

    def query(self, sql: str, params: tuple = ()) -> list[tuple]:
        return self.conn.execute(sql.replace("?", "%s"), params).fetchall()

    def transaction(self):
        return self.conn.transaction()

    def close(self):
        self.conn.close()


def migrate(db) -> list[int]:
    """Apply missing migrations in order. Returns the versions applied now."""
    db.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT)")
    done = {row[0] for row in db.query("SELECT version FROM schema_migrations")}
    applied = []
    for version, name, build in MIGRATIONS:
        if version in done:
            continue
        with db.transaction():
            for stmt in build(db.dialect):
                db.execute(stmt)
            db.execute("INSERT INTO schema_migrations (version, name) VALUES (?, ?)", (version, name))
        applied.append(version)
    return applied
