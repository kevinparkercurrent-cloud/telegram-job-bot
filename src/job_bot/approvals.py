from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Protocol

from pydantic import BaseModel

from job_bot.db import Database
from job_bot.domain import Draft


class Clock(Protocol):
    def now(self) -> datetime:
        raise NotImplementedError


class ApprovalInvalid(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Approval(BaseModel):
    id: str
    vacancy_id: str
    recipient: str
    draft_hash: str
    issued_at: datetime
    expires_at: datetime
    consumed_at: datetime | None = None
    invalidated_at: datetime | None = None


class ApprovedSend(BaseModel):
    approval: Approval
    draft: Draft


def _binding_hash(vacancy_id: str, recipient: str, text: str) -> str:
    value = f"{vacancy_id}\0{recipient}\0{text}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ApprovalService:
    def __init__(self, database: Database, clock: Clock) -> None:
        self._database = database
        self._clock = clock

    async def replace_draft(self, vacancy_id: str, draft: Draft) -> str:
        now = self._clock.now()
        await self._database.invalidate_approvals(vacancy_id, now)
        draft_id = secrets.token_hex(16)
        text_hash = hashlib.sha256(draft.text.encode("utf-8")).hexdigest()
        await self._database.save_draft(draft_id, vacancy_id, draft, text_hash)
        return draft_id

    async def issue(self, vacancy_id: str, recipient: str) -> Approval:
        active = await self._database.get_active_draft(vacancy_id)
        if active is None:
            raise ApprovalInvalid("missing_draft")
        _, draft, _ = active
        now = self._clock.now()
        approval = Approval(
            id=secrets.token_hex(16),
            vacancy_id=vacancy_id,
            recipient=recipient,
            draft_hash=_binding_hash(vacancy_id, recipient, draft.text),
            issued_at=now,
            expires_at=now + timedelta(days=7),
        )
        await self._database.create_approval(
            approval.id,
            approval.vacancy_id,
            approval.recipient,
            approval.draft_hash,
            approval.issued_at,
            approval.expires_at,
        )
        return approval

    async def validate(self, approval_id: str) -> ApprovedSend:
        raw = await self._database.get_approval(approval_id)
        if raw is None:
            raise ApprovalInvalid("not_found")
        approval = Approval.model_validate(raw)
        now = self._clock.now()
        if approval.consumed_at is not None:
            raise ApprovalInvalid("already_consumed")
        if approval.invalidated_at is not None:
            raise ApprovalInvalid("invalidated")
        if approval.expires_at <= now:
            raise ApprovalInvalid("expired")
        active = await self._database.get_active_draft(approval.vacancy_id)
        if active is None:
            raise ApprovalInvalid("missing_draft")
        _, draft, _ = active
        expected = _binding_hash(approval.vacancy_id, approval.recipient, draft.text)
        if not secrets.compare_digest(expected, approval.draft_hash):
            raise ApprovalInvalid("draft_changed")
        return ApprovedSend(approval=approval, draft=draft)

    async def consume(self, approval_id: str) -> ApprovedSend:
        approved = await self.validate(approval_id)
        if not await self._database.consume_approval(approval_id, self._clock.now()):
            raise ApprovalInvalid("already_consumed")
        return approved

    async def consume_for_send(self, approval_id: str) -> ApprovedSend:
        approved = await self.validate(approval_id)
        if not await self._database.consume_approval_and_reserve_send(
            approval_id, approved.approval.vacancy_id, self._clock.now()
        ):
            raise ApprovalInvalid("already_consumed")
        return approved
