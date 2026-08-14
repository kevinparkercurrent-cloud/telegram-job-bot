from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

from job_bot.domain import Assessment, Draft, MatchClass, Vacancy, VacancyStatus


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
    notified_at TEXT,
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
    consumed_at TEXT,
    invalidated_at TEXT
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

    async def set_vacancy_status(self, vacancy_id: str, status: str) -> None:
        await self._connection.execute(
            "UPDATE vacancies SET status = ? WHERE id = ?", (status, vacancy_id)
        )
        await self._connection.commit()

    async def save_assessment(self, vacancy_id: str, assessment: Assessment) -> None:
        await self._connection.execute(
            """
            INSERT INTO assessments(vacancy_id, payload_json, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(vacancy_id) DO UPDATE SET
                payload_json=excluded.payload_json,
                created_at=excluded.created_at
            """,
            (vacancy_id, assessment.model_dump_json(), _utc_now()),
        )
        await self._connection.commit()

    async def save_draft(
        self, draft_id: str, vacancy_id: str, draft: Draft, draft_hash: str
    ) -> None:
        async with self.transaction() as connection:
            await connection.execute(
                "UPDATE drafts SET active = 0 WHERE vacancy_id = ?", (vacancy_id,)
            )
            await connection.execute(
                """
                INSERT INTO drafts(
                    id, vacancy_id, payload_json, draft_hash, active, created_at
                ) VALUES (?, ?, ?, ?, 1, ?)
                """,
                (draft_id, vacancy_id, draft.model_dump_json(), draft_hash, _utc_now()),
            )

    async def get_assessment(self, vacancy_id: str) -> Assessment | None:
        cursor = await self._connection.execute(
            "SELECT payload_json FROM assessments WHERE vacancy_id = ?", (vacancy_id,)
        )
        row = await cursor.fetchone()
        return Assessment.model_validate_json(row["payload_json"]) if row else None

    async def get_active_draft(self, vacancy_id: str) -> tuple[str, Draft, str] | None:
        cursor = await self._connection.execute(
            """
            SELECT id, payload_json, draft_hash FROM drafts
            WHERE vacancy_id = ? AND active = 1
            """,
            (vacancy_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return (
            str(row["id"]),
            Draft.model_validate_json(row["payload_json"]),
            str(row["draft_hash"]),
        )

    async def count_digest_pending(self) -> int:
        cursor = await self._connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM vacancies v
            JOIN assessments a ON a.vacancy_id = v.id
            WHERE v.status = ? AND json_extract(a.payload_json, '$.match_class') = ?
            """,
            (VacancyStatus.QUEUED.value, MatchClass.BORDERLINE.value),
        )
        row = await cursor.fetchone()
        return int(row["count"])

    async def list_digest_pending(self, limit: int = 20) -> list[dict[str, object]]:
        cursor = await self._connection.execute(
            """
            SELECT v.id, v.payload_json AS vacancy_json, v.raw_text,
                   a.payload_json AS assessment_json,
                   d.payload_json AS draft_json
            FROM vacancies v
            JOIN assessments a ON a.vacancy_id = v.id
            JOIN drafts d ON d.vacancy_id = v.id AND d.active = 1
            WHERE v.status = ? AND v.notified_at IS NULL
              AND json_extract(a.payload_json, '$.match_class') = ?
            ORDER BY v.published_at ASC LIMIT ?
            """,
            (VacancyStatus.QUEUED.value, MatchClass.BORDERLINE.value, limit),
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def mark_notified(self, vacancy_ids: list[str], when: datetime) -> None:
        if not vacancy_ids:
            return
        async with self.transaction() as connection:
            await connection.executemany(
                "UPDATE vacancies SET notified_at = ? WHERE id = ?",
                [(when.isoformat(), vacancy_id) for vacancy_id in vacancy_ids],
            )

    async def ping(self) -> bool:
        try:
            cursor = await self._connection.execute("SELECT 1")
            row = await cursor.fetchone()
        except aiosqlite.Error:
            return False
        return row is not None and row[0] == 1

    async def queue_count(self) -> int:
        cursor = await self._connection.execute(
            "SELECT COUNT(*) AS count FROM vacancies WHERE status = ?",
            (VacancyStatus.QUEUED.value,),
        )
        row = await cursor.fetchone()
        return int(row["count"])

    async def list_by_statuses(
        self, statuses: tuple[str, ...], limit: int = 20
    ) -> list[StoredVacancy]:
        placeholders = ",".join("?" for _ in statuses)
        cursor = await self._connection.execute(
            f"""
            SELECT payload_json, raw_text, status FROM vacancies
            WHERE status IN ({placeholders})
            ORDER BY published_at DESC LIMIT ?
            """,
            (*statuses, limit),
        )
        results: list[StoredVacancy] = []
        for row in await cursor.fetchall():
            payload = json.loads(row["payload_json"])
            payload["raw_text"] = row["raw_text"]
            results.append(
                StoredVacancy(
                    vacancy=Vacancy.model_validate(payload), status=str(row["status"])
                )
            )
        return results

    async def set_setting(self, key: str, value: str) -> None:
        await self._connection.execute(
            """
            INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (key, value, _utc_now()),
        )
        await self._connection.commit()

    async def get_setting(self, key: str) -> str | None:
        cursor = await self._connection.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        )
        row = await cursor.fetchone()
        return str(row["value"]) if row else None

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

    async def reserve_send(
        self, vacancy_id: str, approval_id: str, created_at: datetime | None = None
    ) -> bool:
        cursor = await self._connection.execute(
            """
            INSERT OR IGNORE INTO send_attempts(
                vacancy_id, approval_id, status, created_at
            ) VALUES (?, ?, 'reserved', ?)
            """,
            (vacancy_id, approval_id, (created_at or datetime.now(timezone.utc)).isoformat()),
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

    async def save_exchange_rates(
        self, rate_date: str, rates: dict[str, Decimal]
    ) -> None:
        async with self.transaction() as connection:
            await connection.execute(
                "DELETE FROM exchange_rates WHERE rate_date = ?", (rate_date,)
            )
            await connection.executemany(
                """
                INSERT INTO exchange_rates(rate_date, currency, rub_per_unit)
                VALUES (?, ?, ?)
                """,
                [(rate_date, currency, str(value)) for currency, value in rates.items()],
            )

    async def latest_exchange_rates(self) -> tuple[str, dict[str, Decimal]] | None:
        cursor = await self._connection.execute(
            "SELECT MAX(rate_date) AS rate_date FROM exchange_rates"
        )
        row = await cursor.fetchone()
        if row is None or row["rate_date"] is None:
            return None
        rate_date = str(row["rate_date"])
        cursor = await self._connection.execute(
            """
            SELECT currency, rub_per_unit FROM exchange_rates
            WHERE rate_date = ?
            """,
            (rate_date,),
        )
        rates = {
            str(item["currency"]): Decimal(str(item["rub_per_unit"]))
            for item in await cursor.fetchall()
        }
        return rate_date, rates

    async def create_approval(
        self,
        approval_id: str,
        vacancy_id: str,
        recipient: str,
        draft_hash: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> None:
        await self._connection.execute(
            """
            INSERT INTO approvals(
                id, vacancy_id, recipient, draft_hash, issued_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                approval_id,
                vacancy_id,
                recipient,
                draft_hash,
                issued_at.isoformat(),
                expires_at.isoformat(),
            ),
        )
        await self._connection.commit()

    async def get_approval(self, approval_id: str) -> dict[str, str | None] | None:
        cursor = await self._connection.execute(
            "SELECT * FROM approvals WHERE id = ?", (approval_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def invalidate_approvals(self, vacancy_id: str, when: datetime) -> None:
        await self._connection.execute(
            """
            UPDATE approvals SET invalidated_at = ?
            WHERE vacancy_id = ? AND consumed_at IS NULL AND invalidated_at IS NULL
            """,
            (when.isoformat(), vacancy_id),
        )
        await self._connection.commit()

    async def consume_approval(self, approval_id: str, when: datetime) -> bool:
        cursor = await self._connection.execute(
            """
            UPDATE approvals SET consumed_at = ?
            WHERE id = ? AND consumed_at IS NULL AND invalidated_at IS NULL
              AND expires_at > ?
            """,
            (when.isoformat(), approval_id, when.isoformat()),
        )
        await self._connection.commit()
        return cursor.rowcount == 1

    async def count_send_attempts_since(self, since: datetime) -> int:
        cursor = await self._connection.execute(
            """
            SELECT COUNT(*) AS count FROM send_attempts
            WHERE created_at >= ? AND status IN ('reserved', 'sent', 'unknown')
            """,
            (since.isoformat(),),
        )
        row = await cursor.fetchone()
        return int(row["count"])

    async def mark_send_unknown(self, vacancy_id: str, when: datetime) -> None:
        await self._connection.execute(
            """
            UPDATE send_attempts SET status = 'unknown', completed_at = ?
            WHERE vacancy_id = ?
            """,
            (when.isoformat(), vacancy_id),
        )
        await self._connection.commit()
