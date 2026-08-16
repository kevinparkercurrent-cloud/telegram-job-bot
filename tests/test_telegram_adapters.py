from pathlib import Path
from job_bot import telegram_adapters
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest

from job_bot.channel_management import ChannelManagementError


class FakeTelegramClient:
    def __init__(self, entity) -> None:
        self.entity = entity
        self.requests = []

    async def __call__(self, request):
        self.requests.append(request)
        if isinstance(request, ImportChatInviteRequest):
            return SimpleNamespace(chats=[self.entity])
        return SimpleNamespace(chats=[])

    async def get_entity(self, reference):
        return self.entity


def adapter_with(client: FakeTelegramClient) -> telegram_adapters.TelethonUserAdapter:
    adapter = telegram_adapters.TelethonUserAdapter(Path("unused"), 1, "hash", None)
    adapter._client = client
    return adapter


def test_public_post_url_uses_channel_username() -> None:
    assert telegram_adapters.telegram_post_url(-1001234567890, 77, "jobs_feed") == (
        "https://t.me/jobs_feed/77"
    )


def test_private_post_url_uses_internal_channel_id() -> None:
    assert telegram_adapters.telegram_post_url(-1001234567890, 77, None) == (
        "https://t.me/c/1234567890/77"
    )


def test_basic_group_without_username_has_no_post_url() -> None:
    assert telegram_adapters.telegram_post_url(-12345, 77, None) is None


def test_event_mapping_preserves_original_post_url() -> None:
    event = SimpleNamespace(
        chat_id=-1001234567890,
        message=SimpleNamespace(
            id=77,
            date=datetime(2026, 8, 16, tzinfo=timezone.utc),
            raw_text="Project Manager",
        ),
    )

    post = telegram_adapters.telethon_event_to_post(event, username="jobs_feed")

    assert post.source_post_url == "https://t.me/jobs_feed/77"


@pytest.mark.asyncio
async def test_join_public_broadcast_channel() -> None:
    client = FakeTelegramClient(
        SimpleNamespace(
            id=123,
            title="Public jobs",
            username="jobs_feed",
            broadcast=True,
            megagroup=False,
        )
    )

    resolved = await adapter_with(client).join_channel("https://t.me/jobs_feed")

    assert resolved.channel_id == -1000000000123
    assert resolved.title == "Public jobs"
    assert resolved.username == "jobs_feed"
    assert resolved.joined_now is True
    assert isinstance(client.requests[0], JoinChannelRequest)


@pytest.mark.asyncio
async def test_join_private_broadcast_channel() -> None:
    client = FakeTelegramClient(
        SimpleNamespace(
            id=456,
            title="Private jobs",
            username=None,
            broadcast=True,
            megagroup=False,
        )
    )

    resolved = await adapter_with(client).join_channel(
        "https://t.me/+private_secret"
    )

    assert resolved.channel_id == -1000000000456
    assert resolved.username is None
    assert isinstance(client.requests[0], ImportChatInviteRequest)


@pytest.mark.asyncio
async def test_join_rejects_a_group_and_leaves_it() -> None:
    client = FakeTelegramClient(
        SimpleNamespace(
            id=789,
            title="Not a channel",
            username="group_name",
            broadcast=False,
            megagroup=True,
        )
    )

    with pytest.raises(ChannelManagementError, match="not_broadcast"):
        await adapter_with(client).join_channel("@group_name")

    assert any(isinstance(request, LeaveChannelRequest) for request in client.requests)


@pytest.mark.asyncio
async def test_leave_channel_uses_stored_id() -> None:
    client = FakeTelegramClient(None)

    await adapter_with(client).leave_channel(-1000000000123)

    assert isinstance(client.requests[0], LeaveChannelRequest)
