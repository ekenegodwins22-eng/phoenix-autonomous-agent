"""Database compatibility for local SQLite and Render PostgreSQL."""
from __future__ import annotations
import sqlite3
from app.config import settings


def using_postgres() -> bool:
    return bool(settings.database_url)


class DatabaseConnection:
    def __init__(self):
        self._pg = None
        if using_postgres():
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError('DATABASE_URL is configured but psycopg is not installed') from exc
            self._pg = psycopg.connect(settings.database_url, row_factory=dict_row)
            self._conn = self._pg
        else:
            self._conn = sqlite3.connect(settings.database_path, timeout=30)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute('PRAGMA journal_mode=WAL')
            self._conn.execute('PRAGMA busy_timeout=30000')

    @property
    def row_factory(self):
        return getattr(self._conn, 'row_factory', None)

    @row_factory.setter
    def row_factory(self, value):
        if not using_postgres():
            self._conn.row_factory = value

    @staticmethod
    def _sql(sql: str) -> str:
        return sql.replace('?', '%s') if using_postgres() else sql

    def execute(self, sql, params=()):
        return self._conn.execute(self._sql(sql), params)

    def executescript(self, sql):
        for statement in sql.split(';'):
            statement = statement.strip()
            if statement:
                self.execute(statement)

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type:
            self._conn.rollback()
        else:
            self._conn.commit()
        self.close()


def connect() -> DatabaseConnection:
    if not using_postgres():
        from pathlib import Path
        Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)
    return DatabaseConnection()
