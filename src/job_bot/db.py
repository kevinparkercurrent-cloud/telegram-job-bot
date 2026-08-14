from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

from job_bot.domain import Vacancy, VacancyStatus


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);
INSERT OR IGNORE INTO schema_version(version) VALUES (1);

CREATE TABLE IF NOT EXISTS channels (
    channel_id INTEGER PRIMARY KEY,
    label TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vacancies (
    id TEXT PRIMARY KEY,
    channel_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    published_at TEXT NOT NULL,
    fingerprint TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(channel_id, message_id)
);

CREATE TABLE IF NOT EXISTS assessments (
    vacancy_id TEXT PRIMARY KEY REFERENCES vacancies(id) ON DELETE CASCADE,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS drafts (
    id TEXT PRIMARY KEY,
    vacancy_id TEXT NOT NULL REFERENCES vacancies(id) ON DELETE CASCADE,
    payload_json TEXT NOT NULL,
    draft_hash TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    vacancy_id TEXT NOT NULL REFERENCES vacancies(id) ON DELETE CASCADE,
    recipient TEXT NOT NULL,
    draft_hash TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT
);

CREATE TABLE IF NOT EXISTS send_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vacancy_id TEXT NOT NULL UNIQUE REFERENCES vacancies(id) ON DELETE CASCADE,
    approval_id TEXT NOT NULL,
    status TEXT NOT NULL,
    telegram_message_id INTEGER,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vacancy_id TEXT NOT NULL REFERENCES vacancies(id) ON DELETE CASCADE,
    decision TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS exchange_rates (
    rate_date TEXT NOT NULL,
    currency TEXT NOT NULL,
    rub_per_unit TEXT NOT NULL,
    PRIMARY KEY(rate_date, currency)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class StoredVacancy:
    vacancy: Vacancy
    status: str


class Database:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection
        self._transaction_lock = asyncio.Lock()

    @classmethod
    async def open(cls, path: Path) -> "Database":
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = await aiosqlite.connect(path)
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA foreign_keys=ON")
        await connection.execute("PRAGMA journal_mode=WAL")
        await connection.execute("PRAGMA busy_timeout=5000")
        await connection.executescript(SCHEMA)
        await connection.commit()
        return cls(connection)

    async def close(self) -> None:
        await self._connection.close()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._transaction_lock:
            await self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
            except BaseException:
                await self._connection.rollback()
                raise
            else:
                await self._connection.commit()

    async def add_channel(self, channel_id: int, label: str) -> None:
        await self._connection.execute(
            """
            INSERT INTO channels(channel_id, label, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(channel_id) DO UPDATE SET label=excluded.label
            """,
            (channel_id, label, _utc_now()),
        )
        await self._connection.commit()

    async def remove_channel(self, channel_id: int) -> None:
        await self._connection.execute(
            "DELETE FROM channels WHERE channel_id = ?", (channel_id,)
        )
        await self._connection.commit()

    async def is_allowed_channel(self, channel_id: int) -> bool:
        cursor = await self._connection.execute(
            "SELECT 1 FROM channels WHERE channel_id = ?", (channel_id,)
        )
        return await cursor.fetchone() is not None

    async def list_channels(self) -> list[tuple[int, str]]:
        cursor = await self._connection.execute(
            "SELECT channel_id, label FROM channels ORDER BY label, channel_id"
        )
        return [(int(row["channel_id"]), str(row["label"])) for row in await cursor.fetchall()]

    async def insert_vacancy(self, vacancy: Vacancy) -> bool:
        payload = vacancy.model_dump(mode="json", exclude={"raw_text"})
        cursor = await self._connection.execute(
            """
            INSERT OR IGNORE INTO vacancies(
                id, channel_id, message_id, published_at, fingerprint,
                payload_json, raw_text, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                vacancy.id,
                vacancy.channel_id,
                vacancy.message_id,
                vacancy.published_at.isoformat(),
                vacancy.fingerprint,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                vacancy.raw_text,
                VacancyStatus.NEW.value,
                _utc_now(),
            ),
        )
        await self._connection.commit()
        return cursor.rowcount == 1

    async def get_vacancy(self, vacancy_id: str) -> StoredVacancy | None:
        cursor = await self._connection.execute(
            "SELECT payload_json, raw_text, status FROM vacancies WHERE id = ?",
            (vacancy_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        payload["raw_text"] = row["raw_text"]
        return StoredVacancy(
            vacancy=Vacancy.model_validate(payload),
            status=str(row["status"]),
        )

    async def record_decision(self, vacancy_id: str, decision: str) -> None:
        now = _utc_now()
        async with self.transaction() as connection:
            await connection.execute(
                "UPDATE vacancies SET status = ? WHERE id = ?",
                (decision, vacancy_id),
            )
            await connection.execute(
                "INSERT INTO feedback(vacancy_id, decision, created_at) VALUES (?, ?, ?)",
                (vacancy_id, decision, now),
            )

    async def purge_raw_text(self, before: datetime) -> int:
        cursor = await self._connection.execute(
            """
            UPDATE vacancies
            SET raw_text = ''
            WHERE published_at < ? AND raw_text <> ''
            """,
            (before.isoformat(),),
        )
        await self._connection.commit()
        return cursor.rowcount

    async def reserve_send(self, vacancy_id: str, approval_id: str) -> bool:
        cursor = await self._connection.execute(
            """
            INSERT OR IGNORE INTO send_attempts(
                vacancy_id, approval_id, status, created_at
            ) VALUES (?, ?, 'reserved', ?)
            """,
            (vacancy_id, approval_id, _utc_now()),
        )
        await self._connection.commit()
        return cursor.rowcount == 1

    async def finish_send(self, vacancy_id: str, telegram_message_id: int) -> None:
        now = _utc_now()
        async with self.transaction() as connection:
            await connection.execute(
                """
                UPDATE send_attempts
                SET status = 'sent', telegram_message_id = ?, completed_at = ?
                WHERE vacancy_id = ?
                """,
                (telegram_message_id, now, vacancy_id),
            )
            await connection.execute(
                "UPDATE vacancies SET status = ? WHERE id = ?",
                (VacancyStatus.SENT.value, vacancy_id),
            )
