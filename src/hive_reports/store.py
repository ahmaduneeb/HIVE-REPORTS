"""SQLite storage. Ponytail: one file, one connection, WAL on."""
from __future__ import annotations
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    template TEXT NOT NULL,
    output_path TEXT,
    total TEXT,
    format TEXT
);
CREATE INDEX IF NOT EXISTS idx_tx_created ON transactions(created_at);
CREATE TABLE IF NOT EXISTS templates (
    name TEXT PRIMARY KEY,
    body TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT
);
"""


def _ensure_schema(cx: sqlite3.Connection) -> None:
    """Create tables if missing, then migrate older schemas forward."""
    cx.executescript(SCHEMA)

    # Migration: add `format` column if upgrading from a pre-format hive.db
    cols = {row[1] for row in cx.execute("PRAGMA table_info(transactions)")}
    if "format" not in cols:
        cx.execute("ALTER TABLE transactions ADD COLUMN format TEXT")


class Store:
    def __init__(self, path: str | Path = "hive.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._cursor() as cx:
            _ensure_schema(cx)
            cx.execute("PRAGMA journal_mode=WAL;")

    @contextmanager
    def _cursor(self):
        """Open a connection, yield it, and ALWAYS close it on exit.

        sqlite3.Connection's __enter__/__exit__ only manage transactions, so
        without this wrapper every `with self._connect() as cx:` would leave
        the underlying connection open until garbage collection.
        """
        cx = sqlite3.connect(self.path)
        cx.row_factory = sqlite3.Row
        try:
            with cx:
                yield cx
        finally:
            cx.close()

    def log(self, action: str, detail: str | None = None) -> None:
        with self._cursor() as cx:
            cx.execute(
                "INSERT INTO audit(ts, action, detail) VALUES (?,?,?)",
                (datetime.now(timezone.utc).isoformat(), action, detail),
            )

    def save_transaction(
        self,
        payload: dict,
        template: str,
        output_path: str | None,
        total: str | None,
        fmt: str | None = None,
    ) -> int:
        with self._cursor() as cx:
            cur = cx.execute(
                "INSERT INTO transactions(created_at, payload, template, output_path, total, format) VALUES (?,?,?,?,?,?)",
                (
                    datetime.now(timezone.utc).isoformat(),
                    json.dumps(payload, default=str),
                    template,
                    output_path,
                    total,
                    fmt,
                ),
            )
            return cur.lastrowid

    def recent(self, limit: int = 50) -> list[dict]:
        with self._cursor() as cx:
            return [dict(r) for r in cx.execute(
                "SELECT * FROM transactions ORDER BY id DESC LIMIT ?", (limit,)
            )]

    def upsert_template(self, name: str, body: str) -> None:
        with self._cursor() as cx:
            cx.execute(
                "INSERT INTO templates(name, body, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET body=excluded.body, updated_at=excluded.updated_at",
                (name, body, datetime.now(timezone.utc).isoformat()),
            )

    def get_template(self, name: str) -> dict | None:
        """Return the template dict for ``name``, or ``None`` if not stored."""
        with self._cursor() as cx:
            row = cx.execute(
                "SELECT body FROM templates WHERE name = ?", (name,)
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["body"])

    def list_templates(self) -> list[str]:
        """Return all saved template names, sorted alphabetically."""
        with self._cursor() as cx:
            rows = cx.execute(
                "SELECT name FROM templates ORDER BY name"
            ).fetchall()
        return [r["name"] for r in rows]
