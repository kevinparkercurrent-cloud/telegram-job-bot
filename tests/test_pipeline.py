from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from job_bot.collector import ChannelPost
from job_bot.config import load_candidate_profile
from job_bot.db import Database
from job_bot.drafting import TemplateDrafter
from job_bot.pipeline import VacancyPipeline
from job_bot.scoring import ScoringPolicy


class FixedRates:
    async def rub_per_unit(self, currency: str) -> Decimal | None:
        return {"RUB": Decimal("1"), "USD": Decimal("100")}.get(currency)


class NoExternalSource:
    async def fetch(self, url: str):
        return None


class RecordingNotifier:
    def __init__(self) -> None:
        self.cards = []

    async def send_card(self, card) -> None:
        self.cards.append(card)


def make_post(
    text: str,
    message_id: int = 1,
    source_post_url: str | None = None,
) -> ChannelPost:
    return ChannelPost(
        channel_id=-100123,
        message_id=message_id,
        published_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
        text=text,
        source_post_url=source_post_url,
    )


async def build_pipeline(tmp_path):
    db = await Database.open(tmp_path / "pipeline.sqlite3")
    notifier = RecordingNotifier()
    pipeline = VacancyPipeline(
        database=db,
        rates=FixedRates(),
        profile=load_candidate_profile(Path("config/candidate-profile.full.json")),
        policy=ScoringPolicy(),
        source_fetcher=NoExternalSource(),
        template_drafter=TemplateDrafter(),
        ai_drafter=None,
        notifier=notifier,
    )
    return db, notifier, pipeline


@pytest.mark.asyncio
async def test_strong_match_notifies_immediately(tmp_path) -> None:
    db, notifier, pipeline = await build_pipeline(tmp_path)
    try:
        accepted = await pipeline.process_post(
            make_post("Technical Project Manager mobile web delivery QA API remote")
        )
        assert accepted is True
        assert len(notifier.cards) == 1
        assert notifier.cards[0].match_class == "strong"
        assert notifier.cards[0].draft_origin == "telegram_rules_template"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_borderline_match_waits_for_digest(tmp_path) -> None:
    db, notifier, pipeline = await build_pipeline(tmp_path)
    try:
        await pipeline.process_post(
            make_post("Technical Project Manager mobile web delivery QA API remote English C1")
        )
        assert notifier.cards == []
        assert await db.count_digest_pending() == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_same_post_is_not_processed_twice(tmp_path) -> None:
    db, notifier, pipeline = await build_pipeline(tmp_path)
    post = make_post("Technical Project Manager mobile web delivery QA API remote")
    try:
        assert await pipeline.process_post(post) is True
        assert await pipeline.process_post(post) is False
        assert len(notifier.cards) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_strong_card_links_to_original_telegram_post(tmp_path) -> None:
    db, notifier, pipeline = await build_pipeline(tmp_path)
    try:
        await pipeline.process_post(
            make_post(
                "Technical Project Manager mobile web delivery QA API remote",
                source_post_url="https://t.me/jobs_feed/7",
            )
        )

        assert notifier.cards[0].source_post_url == "https://t.me/jobs_feed/7"
    finally:
        await db.close()
