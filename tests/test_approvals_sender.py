import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from job_bot.approvals import ApprovalInvalid, ApprovalService
from job_bot.db import Database
from job_bot.domain import Draft
from job_bot.sender import ResolvedUser, SafeSender, normalize_recruiter_recipient


NOW = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class RecordingTelegramSender:
    def __init__(self, *, timeout: bool = False, is_bot: bool = False) -> None:
        self.timeout = timeout
        self.is_bot = is_bot
        self.sent: list[tuple[str, str, Path]] = []

    async def resolve_user(self, username: str) -> ResolvedUser:
        return ResolvedUser(user_id=100, username=username, is_bot=self.is_bot)

    async def send_private_with_document(
        self, username: str, text: str, document_path: Path
    ) -> int:
        self.sent.append((username, text, document_path))
        if self.timeout:
            raise TimeoutError
        return 500 + len(self.sent)


async def prepare_approval(db, service, vacancy, suffix: int = 1):
    item = vacancy.model_copy(
        update={
            "id": f"vacancy-{suffix}",
            "message_id": suffix,
            "fingerprint": hashlib.sha256(str(suffix).encode()).hexdigest(),
        }
    )
    await db.insert_vacancy(item)
    draft = Draft(
        text=f"Здравствуйте, отклик {suffix}",
        origin="telegram_rules_template",
        evidence_ids=["achievements.0.statement"],
    )
    await service.replace_draft(item.id, draft)
    return await service.issue(item.id, "hr_alex")


def resume_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "resume.pdf"
    path.write_bytes(b"%PDF-1.7\n% test resume")
    return path


@pytest.mark.asyncio
async def test_edit_invalidates_previous_approval(tmp_path, vacancy) -> None:
    db = await Database.open(tmp_path / "approval.sqlite3")
    service = ApprovalService(db, FixedClock())
    try:
        approval = await prepare_approval(db, service, vacancy)
        await service.replace_draft(
            "vacancy-1",
            Draft(text="Новый текст", origin="user", evidence_ids=[]),
        )
        with pytest.raises(ApprovalInvalid):
            await service.consume(approval.id)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_approval_is_single_use(tmp_path, vacancy) -> None:
    db = await Database.open(tmp_path / "sender.sqlite3")
    approvals = ApprovalService(db, FixedClock())
    telegram = RecordingTelegramSender()
    attachment = resume_pdf(tmp_path)
    sender = SafeSender(
        db, approvals, telegram, FixedClock(), resume_pdf_path=attachment
    )
    try:
        approval = await prepare_approval(db, approvals, vacancy)
        assert (await sender.send(approval.id)).status == "sent"
        assert (await sender.send(approval.id)).status == "already_consumed"
        assert telegram.sent == [
            ("hr_alex", "Здравствуйте, отклик 1", attachment)
        ]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_ambiguous_timeout_is_not_retried(tmp_path, vacancy) -> None:
    db = await Database.open(tmp_path / "sender.sqlite3")
    approvals = ApprovalService(db, FixedClock())
    telegram = RecordingTelegramSender(timeout=True)
    sender = SafeSender(
        db,
        approvals,
        telegram,
        FixedClock(),
        resume_pdf_path=resume_pdf(tmp_path),
    )
    try:
        approval = await prepare_approval(db, approvals, vacancy)
        assert (await sender.send(approval.id)).status == "unknown"
        assert (await sender.send(approval.id)).status == "already_consumed"
        assert len(telegram.sent) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_hourly_limit_blocks_sixth_send(tmp_path, vacancy) -> None:
    db = await Database.open(tmp_path / "limits.sqlite3")
    approvals = ApprovalService(db, FixedClock())
    telegram = RecordingTelegramSender()
    sender = SafeSender(
        db,
        approvals,
        telegram,
        FixedClock(),
        resume_pdf_path=resume_pdf(tmp_path),
    )
    try:
        issued = [
            await prepare_approval(db, approvals, vacancy, suffix)
            for suffix in range(1, 7)
        ]
        for approval in issued[:5]:
            assert (await sender.send(approval.id)).status == "sent"
        assert (await sender.send(issued[5].id)).status == "rate_limited"
        assert len(telegram.sent) == 5
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_resolved_bot_recipient_is_rejected(tmp_path, vacancy) -> None:
    db = await Database.open(tmp_path / "bot-recipient.sqlite3")
    approvals = ApprovalService(db, FixedClock())
    telegram = RecordingTelegramSender(is_bot=True)
    sender = SafeSender(
        db,
        approvals,
        telegram,
        FixedClock(),
        resume_pdf_path=resume_pdf(tmp_path),
    )
    try:
        approval = await prepare_approval(db, approvals, vacancy)
        assert (await sender.send(approval.id)).status == "invalid_recipient"
        assert telegram.sent == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_missing_resume_blocks_send_without_consuming_approval(
    tmp_path, vacancy
) -> None:
    db = await Database.open(tmp_path / "missing-resume.sqlite3")
    approvals = ApprovalService(db, FixedClock())
    telegram = RecordingTelegramSender()
    sender = SafeSender(
        db,
        approvals,
        telegram,
        FixedClock(),
        resume_pdf_path=tmp_path / "missing.pdf",
    )
    try:
        approval = await prepare_approval(db, approvals, vacancy)

        assert (await sender.send(approval.id)).status == "attachment_missing"
        assert (await approvals.validate(approval.id)).approval.id == approval.id
        assert telegram.sent == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_oversized_caption_blocks_send_without_consuming_approval(
    tmp_path, vacancy
) -> None:
    db = await Database.open(tmp_path / "long-caption.sqlite3")
    approvals = ApprovalService(db, FixedClock())
    telegram = RecordingTelegramSender()
    sender = SafeSender(
        db,
        approvals,
        telegram,
        FixedClock(),
        resume_pdf_path=resume_pdf(tmp_path),
    )
    try:
        item = vacancy.model_copy(
            update={"id": "long-vacancy", "message_id": 99, "fingerprint": "long"}
        )
        await db.insert_vacancy(item)
        await approvals.replace_draft(
            item.id,
            Draft(text="x" * 1001, origin="user", evidence_ids=[]),
        )
        approval = await approvals.issue(item.id, "hr_alex")

        assert (await sender.send(approval.id)).status == "draft_too_long"
        assert (await approvals.validate(approval.id)).approval.id == approval.id
        assert telegram.sent == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_failed_reservation_does_not_consume_approval(tmp_path, vacancy) -> None:
    db = await Database.open(tmp_path / "atomic.sqlite3")
    approvals = ApprovalService(db, FixedClock())
    try:
        approval = await prepare_approval(db, approvals, vacancy)
        assert await db.reserve_send(approval.vacancy_id, "existing", NOW)

        assert not await db.consume_approval_and_reserve_send(
            approval.id, approval.vacancy_id, NOW
        )
        stored = await db.get_approval(approval.id)
        assert stored is not None
        assert stored["consumed_at"] is None
    finally:
        await db.close()


@pytest.mark.parametrize("recipient", ("-100123", "https://t.me/jobs_channel", "@"))
def test_non_private_recipient_is_rejected(recipient) -> None:
    with pytest.raises(ValueError):
        normalize_recruiter_recipient(recipient)
