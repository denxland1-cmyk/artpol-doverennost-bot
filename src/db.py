"""SQLite журнал отправок.

Минимальная схема: каждое успешное письмо = одна запись.
Все попытки (включая ошибки и отмены) логируются в attempts.
"""

from __future__ import annotations
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

DB_PATH = Path(os.environ.get("DB_PATH", "./data/doverennosti.db"))


def init():
    """Создаёт БД и таблицы. Идемпотентно — можно вызывать на каждый запуск."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS sent (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                tg_user_id INTEGER NOT NULL,
                tg_username TEXT,
                supplier_key TEXT NOT NULL,
                to_email TEXT NOT NULL,
                doverennost_number TEXT,
                doverennost_date TEXT,
                driver_surname TEXT,
                item_name TEXT,
                item_qty INTEGER,
                subject TEXT,
                attach_name TEXT
            );

            CREATE TABLE IF NOT EXISTS attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                tg_user_id INTEGER NOT NULL,
                tg_username TEXT,
                action TEXT NOT NULL,
                detail TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_sent_ts ON sent(ts);
            CREATE INDEX IF NOT EXISTS idx_attempts_ts ON attempts(ts);
            """
        )


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def log_sent(*, tg_user_id: int, tg_username: str | None,
             supplier_key: str, to_email: str,
             doverennost_number: str | None, doverennost_date: str | None,
             driver_surname: str | None, item_name: str | None, item_qty: int | None,
             subject: str, attach_name: str) -> None:
    with _conn() as c:
        c.execute(
            """INSERT INTO sent
            (tg_user_id, tg_username, supplier_key, to_email,
             doverennost_number, doverennost_date, driver_surname,
             item_name, item_qty, subject, attach_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (tg_user_id, tg_username, supplier_key, to_email,
             doverennost_number, doverennost_date, driver_surname,
             item_name, item_qty, subject, attach_name),
        )


def log_attempt(*, tg_user_id: int, tg_username: str | None,
                action: str, detail: str | None = None) -> None:
    """action: 'pdf_received', 'supplier_chosen', 'send_ok', 'send_fail',
    'cancelled', 'access_denied', 'parse_fail', ..."""
    with _conn() as c:
        c.execute(
            "INSERT INTO attempts (tg_user_id, tg_username, action, detail) VALUES (?, ?, ?, ?)",
            (tg_user_id, tg_username, action, detail),
        )


def recent_sent(limit: int = 20) -> list[dict[str, Any]]:
    """Для админ-команды /history — последние N отправок."""
    with _conn() as c:
        cur = c.execute(
            """SELECT ts, tg_username, supplier_key, doverennost_number,
                      driver_surname, item_qty, item_name
               FROM sent ORDER BY id DESC LIMIT ?""",
            (limit,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
