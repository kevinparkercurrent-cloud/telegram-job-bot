from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Protocol
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from job_bot.approvals import ApprovalInvalid, ApprovalService, Clock
from job_bot.db import Database


USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")


class ResolvedUser(BaseModel):
    user_id: int
    username: str
    is_bot: bool


class TelegramSender(Protocol):
    async def resolve_user(self, username: str) -> ResolvedUser:
        raise NotImplementedError

    async def send_private(self, username: str, text: str) -> int:
        raise NotImplementedError


class SendResult(BaseModel):
    status: str
    telegram_message_id: int | None = None


def normalize_recruiter_recipient(value: str) -> str:
    username = value.strip().removeprefix("@")
    if not USERNAME_RE.fullmatch(username):
        raise ValueError("Recipient must be a Telegram username")
    return username


class SafeSender:
    def __init__(
        self,
        database: Database,
        approvals: ApprovalService,
        telegram: TelegramSender,
        clock: Clock,
        *,
        timezone_name: str = "Europe/Moscow",
        hourly_limit: int = 5,
        daily_limit: int = 15,
    ) -> None:
        self._database = database
        self._approvals = approvals
        self._telegram = telegram
        self._clock = clock
        self._timezone = ZoneInfo(timezone_name)
        self._hourly_limit = hourly_limit
        self._daily_limit = daily_limit

    async def send(self, approval_id: str) -> SendResult:
        try:
            approved = await self._approvals.validate(approval_id)
        except ApprovalInvalid as error:
            return SendResult(status=error.code)

        now = self._clock.now()
        hour_count = await self._database.count_send_attempts_since(
            now - timedelta(hours=1)
        )
        local_now = now.astimezone(self._timezone)
        local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        day_count = await self._database.count_send_attempts_since(
            local_midnight.astimezone(timezone.utc)
        )
        if hour_count >= self._hourly_limit or day_count >= self._daily_limit:
            return SendResult(status="rate_limited")

        try:
            recipient = normalize_recruiter_recipient(approved.approval.recipient)
            resolved = await self._telegram.resolve_user(recipient)
        except (ValueError, LookupError):
            return SendResult(status="invalid_recipient")
        if resolved.is_bot:
            return SendResult(status="invalid_recipient")

        try:
            approved = await self._approvals.consume(approval_id)
        except ApprovalInvalid as error:
            return SendResult(status=error.code)
        if not await self._database.reserve_send(
            approved.approval.vacancy_id, approval_id, now
        ):
            return SendResult(status="already_consumed")

        try:
            message_id = await self._telegram.send_private(
                recipient, approved.draft.text
            )
        except TimeoutError:
            await self._database.mark_send_unknown(
                approved.approval.vacancy_id, self._clock.now()
            )
            return SendResult(status="unknown")

        await self._database.finish_send(approved.approval.vacancy_id, message_id)
        return SendResult(status="sent", telegram_message_id=message_id)
