from __future__ import annotations
import sqlite3
import threading
import uuid
import logging

from src.config import config

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS providers (
    id        TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    specialty TEXT
);

CREATE TABLE IF NOT EXISTS patients (
    id    TEXT PRIMARY KEY,
    name  TEXT NOT NULL,
    phone TEXT NOT NULL UNIQUE,
    email TEXT
);

CREATE TABLE IF NOT EXISTS appointments (
    id          TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL REFERENCES providers(id),
    patient_id  TEXT REFERENCES patients(id),
    start_time  TEXT NOT NULL,
    end_time    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'available',
    notes       TEXT
);
"""


class Database:
    def __init__(self):
        self._local = threading.local()
        self._path = config.resolved_db_path
        # Initialize schema on startup
        conn = self._conn()
        conn.executescript(_SCHEMA)
        # Migrate: add specialty column if missing (existing databases)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(providers)")}
        if "specialty" not in cols:
            conn.execute("ALTER TABLE providers ADD COLUMN specialty TEXT")
        conn.commit()
        logger.info("database initialised path=%s", self._path)

    def _conn(self) -> sqlite3.Connection:
        if not getattr(self._local, "conn", None):
            self._local.conn = sqlite3.connect(self._path, check_same_thread=False)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        conn = self._conn()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur

    def fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self._conn().execute(sql, params).fetchall()

    def fetchone(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        return self._conn().execute(sql, params).fetchone()

    @staticmethod
    def new_id() -> str:
        return str(uuid.uuid4())


db = Database()
