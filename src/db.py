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


def user_stats() -> list[dict[str, Any]]:
    """Для /stats: уникальные пользователи + breakdown по действиям.

    Собирает данные из обеих таблиц (sent + attempts).
    """
    with _conn() as c:
        cur = c.execute(
            """
            SELECT
                u.tg_user_id,
                MAX(u.tg_username)                                   AS tg_username,
                MIN(u.first_seen)                                    AS first_seen,
                MAX(u.last_seen)                                     AS last_seen,
                SUM(CASE WHEN u.action = 'pdf_received'    THEN 1 ELSE 0 END) AS pdfs,
                SUM(CASE WHEN u.action = 'supplier_chosen' THEN 1 ELSE 0 END) AS clicks,
                SUM(CASE WHEN u.action = 'cancelled'       THEN 1 ELSE 0 END) AS cancels,
                SUM(CASE WHEN u.action = 'send_fail'       THEN 1 ELSE 0 END) AS fails,
                SUM(CASE WHEN u.action = 'parse_fail'      THEN 1 ELSE 0 END) AS parse_fails,
                SUM(CASE WHEN u.action = 'stamp_fail'      THEN 1 ELSE 0 END) AS stamp_fails
            FROM (
                SELECT tg_user_id, tg_username, action, ts AS first_seen, ts AS last_seen
                FROM attempts
            ) u
            GROUP BY u.tg_user_id
            ORDER BY MAX(u.last_seen) DESC
            """
        )
        cols = [d[0] for d in cur.description]
        users = [dict(zip(cols, row)) for row in cur.fetchall()]

        # Добавим количество УСПЕШНЫХ отправок (из sent) к каждому пользователю
        cur = c.execute("SELECT tg_user_id, COUNT(*) AS sent_ok FROM sent GROUP BY tg_user_id")
        sent_by_user = {row[0]: row[1] for row in cur.fetchall()}
        for u in users:
            u["sent_ok"] = sent_by_user.get(u["tg_user_id"], 0)

        return users
