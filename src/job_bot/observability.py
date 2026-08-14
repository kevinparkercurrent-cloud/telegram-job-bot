from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel

from job_bot.db import Database


REDACTION_PATTERNS = (
    re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]+\b"),
    re.compile(r"\+[1-9]\d{9,14}\b"),
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for pattern in REDACTION_PATTERNS:
            message = pattern.sub("[REDACTED]", message)
        record.msg = message
        record.args = ()
        return True


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(RedactingFilter())
    logging.basicConfig(level=level, handlers=[handler], force=True)


class TelegramHealth(Protocol):
    session_valid: bool
    last_collected_at: datetime | None


class BotHealth(Protocol):
    polling: bool


class BackupHealth(Protocol):
    last_success_at: datetime | None


class HealthSnapshot(BaseModel):
    database_ok: bool
    telegram_session_ok: bool
    bot_polling_ok: bool
    ai_available: bool
    queue_count: int
    last_collected_at: datetime | None
    last_backup_at: datetime | None
    can_collect: bool
    can_send: bool


class HealthService:
    def __init__(
        self,
        database: Database,
        telegram: TelegramHealth,
        bot: BotHealth,
        backup: BackupHealth,
        *,
        ai_available: bool,
    ) -> None:
        self._database = database
        self._telegram = telegram
        self._bot = bot
        self._backup = backup
        self._ai_available = ai_available

    async def snapshot(self) -> HealthSnapshot:
        database_ok = await self._database.ping()
        queue_count = await self._database.queue_count() if database_ok else 0
        can_collect = database_ok and self._telegram.session_valid
        can_send = can_collect and self._bot.polling
        return HealthSnapshot(
            database_ok=database_ok,
            telegram_session_ok=self._telegram.session_valid,
            bot_polling_ok=self._bot.polling,
            ai_available=self._ai_available,
            queue_count=queue_count,
            last_collected_at=self._telegram.last_collected_at,
            last_backup_at=self._backup.last_success_at,
            can_collect=can_collect,
            can_send=can_send,
        )
