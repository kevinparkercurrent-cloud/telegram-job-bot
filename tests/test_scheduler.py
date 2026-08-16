from datetime import datetime, timezone

import pytest

from job_bot.db import Database
from job_bot.domain import Assessment, Draft, MatchClass
from job_bot.scheduler import Scheduler


class RecordingDigestNotifier:
    def __init__(self) -> None:
        self.digests = []

    async def send_digest(self, items) -> None:
        self.digests.append(items)


async def prepare_borderline(db: Database, vacancy) -> None:
    await db.insert_vacancy(vacancy)
    await db.save_assessment(
        vacancy.id,
        Assessment(
            score=74,
            match_class=MatchClass.BORDERLINE,
            reasons=["Роль подходит"],
        ),
    )
    await db.save_draft(
        "draft-1",
        vacancy.id,
        Draft(
            text="Здравствуйте!",
            origin="telegram_rules_template",
            evidence_ids=["achievements.0.statement"],
        ),
        "hash",
    )
    await db.set_vacancy_status(vacancy.id, "queued")


@pytest.mark.asyncio
async def test_digest_runs_once_per_slot(tmp_path, vacancy) -> None:
    db = await Database.open(tmp_path / "scheduler.sqlite3")
    notifier = RecordingDigestNotifier()
    scheduler = Scheduler(db, notifier, "Europe/Moscow", ("12:00", "19:00"))
    due = datetime(2026, 8, 14, 9, 0, tzinfo=timezone.utc)
    try:
        await prepare_borderline(db, vacancy)
        await scheduler.tick(due)
        await scheduler.tick(due)
        assert len(notifier.digests) == 1
        assert notifier.digests[0][0].vacancy_id == vacancy.id
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_retention_removes_only_raw_text(tmp_path, vacancy) -> None:
    db = await Database.open(tmp_path / "retention.sqlite3")
    scheduler = Scheduler(db, RecordingDigestNotifier(), "Europe/Moscow", ("12:00", "19:00"))
    try:
        await db.insert_vacancy(vacancy)
        await db.record_decision(vacancy.id, "skipped")
        removed = await scheduler.run_retention(
            datetime(2026, 8, 14, tzinfo=timezone.utc)
        )
        stored = await db.get_vacancy(vacancy.id)
        assert removed == 1
        assert stored is not None
        assert stored.vacancy.raw_text == ""
        assert stored.vacancy.fingerprint == vacancy.fingerprint
        assert stored.status == "skipped"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_digest_preserves_original_telegram_post_url(tmp_path, vacancy) -> None:
    db = await Database.open(tmp_path / "scheduler-source.sqlite3")
    notifier = RecordingDigestNotifier()
    scheduler = Scheduler(db, notifier, "Europe/Moscow", ("12:00", "19:00"))
    due = datetime(2026, 8, 14, 9, 0, tzinfo=timezone.utc)
    linked = vacancy.model_copy(
        update={"source_post_url": "https://t.me/jobs_feed/7"}
    )
    try:
        await prepare_borderline(db, linked)
        await scheduler.tick(due)

        assert notifier.digests[0][0].source_post_url == "https://t.me/jobs_feed/7"
    finally:
        await db.close()
