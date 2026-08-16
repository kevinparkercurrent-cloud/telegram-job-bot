import pytest

from job_bot.channel_management import (
    ChannelManagementError,
    ChannelManagementService,
    ResolvedChannel,
    parse_channel_reference,
)
from job_bot.db import Database


class RecordingMembership:
    def __init__(self, *, joined_now: bool = True, leave_fails: bool = False) -> None:
        self.joined_now = joined_now
        self.leave_fails = leave_fails
        self.joined_references: list[str] = []
        self.left_channel_ids: list[int] = []

    async def join_channel(self, reference: str) -> ResolvedChannel:
        self.joined_references.append(reference)
        return ResolvedChannel(
            channel_id=-1000000000123,
            title="Jobs",
            username="jobs_feed",
            joined_now=self.joined_now,
        )

    async def leave_channel(self, channel_id: int) -> None:
        self.left_channel_ids.append(channel_id)
        if self.leave_fails:
            raise ChannelManagementError("telegram_unavailable")


@pytest.mark.parametrize(
    ("raw", "value"),
    (
        ("@jobs_feed", "jobs_feed"),
        ("jobs_feed", "jobs_feed"),
        ("t.me/jobs_feed", "jobs_feed"),
        ("https://t.me/jobs_feed", "jobs_feed"),
    ),
)
def test_parse_public_channel_reference(raw: str, value: str) -> None:
    parsed = parse_channel_reference(raw)

    assert parsed.kind == "public"
    assert parsed.value == value


@pytest.mark.parametrize(
    ("raw", "value"),
    (
        ("https://t.me/+private_secret", "private_secret"),
        ("https://t.me/joinchat/private_secret", "private_secret"),
    ),
)
def test_parse_private_invite_without_exposing_it(raw: str, value: str) -> None:
    parsed = parse_channel_reference(raw)

    assert parsed.kind == "private"
    assert parsed.value == value
    assert value not in repr(parsed)


@pytest.mark.parametrize(
    "raw",
    (
        "",
        "https://example.com/jobs_feed",
        "https://t.me/jobs_feed/123",
        "https://t.me/",
        "@bad-name",
    ),
)
def test_parse_rejects_unsupported_references_without_echoing_input(raw: str) -> None:
    with pytest.raises(ChannelManagementError) as captured:
        parse_channel_reference(raw)

    assert captured.value.code == "invalid_reference"
    if raw:
        assert raw not in str(captured.value)


@pytest.mark.asyncio
async def test_add_joins_then_persists_resolved_channel(tmp_path) -> None:
    db = await Database.open(tmp_path / "add.sqlite3")
    membership = RecordingMembership()
    try:
        stored = await ChannelManagementService(db, membership).add(
            "https://t.me/jobs_feed"
        )

        assert stored.channel.channel_id == -1000000000123
        assert stored.channel.label == "Jobs"
        assert stored.channel.username == "jobs_feed"
        assert stored.already_present is False
        assert membership.joined_references == ["https://t.me/jobs_feed"]
        assert await db.is_allowed_channel(stored.channel.channel_id)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_duplicate_addition_reports_existing_channel(tmp_path) -> None:
    db = await Database.open(tmp_path / "duplicate.sqlite3")
    membership = RecordingMembership(joined_now=False)
    try:
        service = ChannelManagementService(db, membership)
        first = await service.add("@jobs_feed")
        second = await service.add("@jobs_feed")

        assert first.already_present is False
        assert second.already_present is True
        assert len(await db.list_channels()) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_add_rolls_back_new_membership_when_persistence_fails(tmp_path) -> None:
    db = await Database.open(tmp_path / "rollback.sqlite3")
    membership = RecordingMembership(joined_now=True)

    async def fail_add(*args, **kwargs):
        raise RuntimeError("database unavailable")

    db.add_channel = fail_add
    try:
        with pytest.raises(ChannelManagementError, match="persistence_failed"):
            await ChannelManagementService(db, membership).add("@jobs_feed")

        assert membership.left_channel_ids == [-1000000000123]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_add_does_not_leave_existing_membership_on_persistence_failure(
    tmp_path,
) -> None:
    db = await Database.open(tmp_path / "existing.sqlite3")
    membership = RecordingMembership(joined_now=False)

    async def fail_add(*args, **kwargs):
        raise RuntimeError("database unavailable")

    db.add_channel = fail_add
    try:
        with pytest.raises(ChannelManagementError, match="persistence_failed"):
            await ChannelManagementService(db, membership).add("@jobs_feed")

        assert membership.left_channel_ids == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_add_rolls_back_when_existing_channel_read_fails(tmp_path) -> None:
    db = await Database.open(tmp_path / "read-failure.sqlite3")
    membership = RecordingMembership(joined_now=True)

    async def fail_read(channel_id: int):
        raise RuntimeError("database unavailable")

    db.get_channel = fail_read
    try:
        with pytest.raises(ChannelManagementError, match="persistence_failed"):
            await ChannelManagementService(db, membership).add("@jobs_feed")

        assert membership.left_channel_ids == [-1000000000123]
        assert not await db.is_allowed_channel(-1000000000123)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_add_does_not_reread_after_committed_insert(tmp_path) -> None:
    db = await Database.open(tmp_path / "no-post-insert-read.sqlite3")
    membership = RecordingMembership(joined_now=True)
    original_get = db.get_channel
    calls = 0

    async def fail_second_read(channel_id: int):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError("database unavailable")
        return await original_get(channel_id)

    db.get_channel = fail_second_read
    try:
        result = await ChannelManagementService(db, membership).add("@jobs_feed")

        assert result.channel.channel_id == -1000000000123
        assert calls == 1
        assert await db.is_allowed_channel(-1000000000123)
        assert membership.left_channel_ids == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_remove_stops_monitoring_before_best_effort_leave(tmp_path) -> None:
    db = await Database.open(tmp_path / "remove.sqlite3")
    membership = RecordingMembership(leave_fails=True)
    try:
        await db.add_channel(-1000000000123, "Jobs", "jobs_feed")

        result = await ChannelManagementService(db, membership).remove(
            -1000000000123
        )

        assert result.channel.label == "Jobs"
        assert result.left is False
        assert not await db.is_allowed_channel(-1000000000123)
        assert membership.left_channel_ids == [-1000000000123]
    finally:
        await db.close()
