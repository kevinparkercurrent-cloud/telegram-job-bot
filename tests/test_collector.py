from dataclasses import replace
from datetime import datetime, timezone

import pytest

from job_bot.collector import ChannelPost, CollectionResult, Collector
from job_bot.db import Database


class RecordingPipeline:
    def __init__(self, accepted: bool = True) -> None:
        self.accepted = accepted
        self.received: list[ChannelPost] = []

    async def process_post(self, post: ChannelPost) -> bool:
        self.received.append(post)
        return self.accepted


def post(channel_id: int = -1009) -> ChannelPost:
    return ChannelPost(
        channel_id=channel_id,
        message_id=10,
        published_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
        text="Project Manager remote",
    )


@pytest.mark.asyncio
async def test_ignores_non_allowlisted_channel(tmp_path) -> None:
    db = await Database.open(tmp_path / "collector.sqlite3")
    pipeline = RecordingPipeline()
    try:
        result = await Collector(db, pipeline).handle(post())
        assert result == CollectionResult.IGNORED
        assert pipeline.received == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_forwards_allowlisted_post_once(tmp_path) -> None:
    db = await Database.open(tmp_path / "collector.sqlite3")
    pipeline = RecordingPipeline()
    try:
        await db.add_channel(-1009, "jobs")
        result = await Collector(db, pipeline).handle(post())
        assert result == CollectionResult.PROCESSED
        assert pipeline.received == [post()]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_reports_duplicate_when_pipeline_declines_post(tmp_path) -> None:
    db = await Database.open(tmp_path / "collector.sqlite3")
    pipeline = RecordingPipeline(accepted=False)
    try:
        await db.add_channel(-1009, "jobs")
        result = await Collector(db, pipeline).handle(post())
        assert result == CollectionResult.DUPLICATE
    finally:
        await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        (
            "#резюме #opentowork #projectmanager\n"
            "Ищу: Project Manager / Delivery Manager\n"
            "Обо мне: 4 года управляю IT-проектами."
        ),
        (
            "Резюме: Project Manager / Руководитель проектов\n"
            "Желаемая сфера работы: IT и цифровые продукты\n"
            "Контакты: @candidate_name"
        ),
    ],
)
async def test_ignores_candidate_resumes_before_pipeline(tmp_path, text) -> None:
    db = await Database.open(tmp_path / "collector.sqlite3")
    pipeline = RecordingPipeline()
    candidate_post = replace(post(), text=text)
    try:
        await db.add_channel(-1009, "jobs")

        result = await Collector(db, pipeline).handle(candidate_post)

        assert result == CollectionResult.IGNORED
        assert pipeline.received == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_vacancy_that_requests_resume_is_not_filtered(tmp_path) -> None:
    db = await Database.open(tmp_path / "collector.sqlite3")
    pipeline = RecordingPipeline()
    vacancy_post = replace(
        post(),
        text=(
            "Вакансия: Project Manager. Мы ищем специалиста в продуктовую "
            "команду. Требования: опыт delivery. Присылайте резюме @hr_alex"
        )
    )
    try:
        await db.add_channel(-1009, "jobs")

        result = await Collector(db, pipeline).handle(vacancy_post)

        assert result == CollectionResult.PROCESSED
        assert pipeline.received == [vacancy_post]
    finally:
        await db.close()
