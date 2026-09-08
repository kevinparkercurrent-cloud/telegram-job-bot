from datetime import datetime, timezone

import pytest

from job_bot.db import Database
from job_bot.domain import Assessment, Draft, MatchClass
from job_bot.hr_discovery import HRDiscoveryService, HRLeadCandidate
from job_bot.collector import ChannelPost
from job_bot.scheduler import Scheduler


class RecordingDigestNotifier:
    def __init__(self) -> None:
        self.digests = []
        self.manual_digests = []
        self.hr_digests = []

    async def send_digest(self, items) -> None:
        self.digests.append(items)

    async def send_manual_digest(self, items) -> None:
        self.manual_digests.append(items)

    async def send_hr_digest(self, items) -> None:
        self.hr_digests.append(items)


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


@pytest.mark.asyncio
async def test_digest_preserves_short_vacancy_summary(tmp_path, vacancy) -> None:
    db = await Database.open(tmp_path / "scheduler-summary.sqlite3")
    notifier = RecordingDigestNotifier()
    scheduler = Scheduler(db, notifier, "Europe/Moscow", ("12:00", "19:00"))
    due = datetime(2026, 8, 14, 9, 0, tzinfo=timezone.utc)
    summarized = vacancy.model_copy(
        update={"summary": "Запуск продукта и управление релизами."}
    )
    try:
        await prepare_borderline(db, summarized)
        await scheduler.tick(due)

        assert notifier.digests[0][0].summary == summarized.summary
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_manual_vacancies_are_sent_as_separate_link_digest(
    tmp_path, vacancy
) -> None:
    db = await Database.open(tmp_path / "scheduler-manual.sqlite3")
    notifier = RecordingDigestNotifier()
    scheduler = Scheduler(db, notifier, "Europe/Moscow", ("12:00", "19:00"))
    due = datetime(2026, 8, 14, 9, 0, tzinfo=timezone.utc)
    manual = vacancy.model_copy(
        update={
            "recruiter_username": None,
            "source_post_url": "https://t.me/jobs_feed/15",
        }
    )
    try:
        await db.insert_vacancy(manual)
        await db.set_vacancy_status(manual.id, "manual")

        await scheduler.tick(due)

        assert notifier.digests == []
        assert len(notifier.manual_digests) == 1
        assert notifier.manual_digests[0][0].source_post_url == (
            "https://t.me/jobs_feed/15"
        )
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_hr_contacts_are_sent_once_in_daily_digest_at_last_slot(
    tmp_path,
) -> None:
    db = await Database.open(tmp_path / "scheduler-hr.sqlite3")
    notifier = RecordingDigestNotifier()
    scheduler = Scheduler(db, notifier, "Europe/Moscow", ("12:00", "19:00"))
    post = ChannelPost(
        channel_id=-100123,
        message_id=301,
        published_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
        text=(
            "Компания: Betting Labs\nВакансия: Media Buyer в iGaming.\n"
            "Для отклика пишите @betting_recruiter"
        ),
        source_post_url="https://t.me/igaming_jobs/301",
    )
    try:
        assert await HRDiscoveryService(db).process_post(post)

        await scheduler.tick(datetime(2026, 8, 14, 9, 0, tzinfo=timezone.utc))
        assert notifier.hr_digests == []

        evening = datetime(2026, 8, 14, 16, 0, tzinfo=timezone.utc)
        await scheduler.tick(evening)
        await scheduler.tick(evening)

        assert len(notifier.hr_digests) == 1
        assert notifier.hr_digests[0][0].contact == "@betting_recruiter"
        assert notifier.hr_digests[0][0].source_post_url == (
            "https://t.me/igaming_jobs/301"
        )
        assert await db.list_hr_pending() == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_daily_hr_digest_drains_more_than_twenty_contacts(tmp_path) -> None:
    db = await Database.open(tmp_path / "scheduler-many-hr.sqlite3")
    notifier = RecordingDigestNotifier()
    scheduler = Scheduler(db, notifier, "Europe/Moscow", ("12:00", "19:00"))
    try:
        for index in range(21):
            username = f"recruiter_{index:02}"
            inserted = await db.insert_hr_contact(
                HRLeadCandidate(
                    contact=f"@{username}",
                    normalized_contact=username,
                    company=None,
                    role="Media Buyer",
                    relevance_reason="iGaming-команда",
                    source_post_url=f"https://t.me/igaming_jobs/{500 + index}",
                ),
                source_vacancy_id=f"-100123:{500 + index}",
            )
            assert inserted

        await scheduler.tick(
            datetime(2026, 8, 14, 16, 0, tzinfo=timezone.utc)
        )

        assert len(notifier.hr_digests) == 1
        assert len(notifier.hr_digests[0]) == 21
        assert await db.list_hr_pending(limit=None) == []
    finally:
        await db.close()
