from datetime import datetime, timezone
import sqlite3

import pytest

from job_bot.db import Database


@pytest.mark.asyncio
async def test_allowlist_and_fingerprint_are_idempotent(tmp_path, vacancy) -> None:
    db = await Database.open(tmp_path / "bot.sqlite3")
    try:
        await db.add_channel(-100123, "jobs")
        assert await db.is_allowed_channel(-100123)
        assert await db.insert_vacancy(vacancy) is True
        assert await db.insert_vacancy(vacancy) is False
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_allowlist_is_limited_to_twenty_channels(tmp_path) -> None:
    db = await Database.open(tmp_path / "limit.sqlite3")
    try:
        for index in range(20):
            await db.add_channel(-1000 - index, f"channel-{index}")
        with pytest.raises(ValueError, match="20"):
            await db.add_channel(-2000, "too-many")
        assert len(await db.list_channels()) == 20
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_remove_channel_stops_allowing_it(tmp_path) -> None:
    db = await Database.open(tmp_path / "bot.sqlite3")
    try:
        await db.add_channel(-100123, "jobs")
        await db.remove_channel(-100123)
        assert not await db.is_allowed_channel(-100123)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_raw_text_can_be_purged_without_losing_decision(tmp_path, vacancy) -> None:
    db = await Database.open(tmp_path / "bot.sqlite3")
    try:
        await db.insert_vacancy(vacancy)
        await db.record_decision(vacancy.id, "skipped")
        await db.purge_raw_text(datetime(2026, 2, 1, tzinfo=timezone.utc))

        stored = await db.get_vacancy(vacancy.id)
        assert stored is not None
        assert stored.vacancy.raw_text == ""
        assert stored.status == "skipped"
        assert stored.vacancy.fingerprint == "fingerprint-1"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_only_one_send_can_be_reserved_per_vacancy(tmp_path, vacancy) -> None:
    db = await Database.open(tmp_path / "bot.sqlite3")
    try:
        await db.insert_vacancy(vacancy)
        assert await db.reserve_send(vacancy.id, "approval-1") is True
        assert await db.reserve_send(vacancy.id, "approval-2") is False
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_original_post_url_round_trips_through_database(tmp_path, vacancy) -> None:
    db = await Database.open(tmp_path / "source-url.sqlite3")
    linked = vacancy.model_copy(
        update={"source_post_url": "https://t.me/jobs_feed/7"}
    )
    try:
        assert await db.insert_vacancy(linked)
        stored = await db.get_vacancy(linked.id)
        assert stored is not None
        assert str(stored.vacancy.source_post_url) == "https://t.me/jobs_feed/7"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_open_migrates_existing_vacancies_table_for_source_url(tmp_path) -> None:
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE vacancies (id TEXT PRIMARY KEY)")
    connection.commit()
    connection.close()

    db = await Database.open(path)
    await db.close()

    connection = sqlite3.connect(path)
    try:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(vacancies)").fetchall()
        }
        assert "source_post_url" in columns
    finally:
        connection.close()
