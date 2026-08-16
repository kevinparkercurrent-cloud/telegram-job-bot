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
async def test_allowlist_is_limited_to_one_hundred_channels(tmp_path) -> None:
    db = await Database.open(tmp_path / "limit.sqlite3")
    try:
        for index in range(100):
            await db.add_channel(
                -1000 - index, f"channel-{index}", f"name{index}"
            )
        with pytest.raises(ValueError, match="100"):
            await db.add_channel(-5000, "too-many")
        await db.add_channel(-1000, "renamed", "renamed_channel")

        assert len(await db.list_channels()) == 100
        stored = await db.get_channel(-1000)
        assert stored is not None
        assert stored.label == "renamed"
        assert stored.username == "renamed_channel"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_channel_username_round_trips_and_can_be_null(tmp_path) -> None:
    db = await Database.open(tmp_path / "username.sqlite3")
    try:
        await db.add_channel(-100123, "public jobs", "jobs_feed")
        await db.add_channel(-100456, "private jobs")

        channels = await db.list_channels()
        assert [channel.username for channel in channels] == [None, "jobs_feed"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_open_migrates_existing_channels_table_for_username(tmp_path) -> None:
    path = tmp_path / "legacy-channels.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE channels ("
        "channel_id INTEGER PRIMARY KEY, "
        "label TEXT NOT NULL, "
        "created_at TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO channels(channel_id, label, created_at) VALUES (?, ?, ?)",
        (-100123, "legacy", "2026-08-16T00:00:00+00:00"),
    )
    connection.commit()
    connection.close()

    db = await Database.open(path)
    try:
        stored = await db.get_channel(-100123)
        assert stored is not None
        assert stored.username is None
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
