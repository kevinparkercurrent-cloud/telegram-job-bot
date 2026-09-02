from pathlib import Path
from job_bot import telegram_adapters
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.tl.functions.messages import CheckChatInviteRequest
from telethon.tl.types import (
    ChatInvite,
    ChatInviteAlready,
    ChatInvitePeek,
    ChatPhotoEmpty,
)

from job_bot.channel_management import ChannelManagementError


class FakeTelegramClient:
    def __init__(self, entity, *, invite_preview=None) -> None:
        self.entity = entity
        self.invite_preview = invite_preview
        self.requests = []
        self.sent_files = []

    async def __call__(self, request):
        self.requests.append(request)
        if isinstance(request, CheckChatInviteRequest):
            return self.invite_preview or ChatInvite(
                title="Private jobs",
                photo=ChatPhotoEmpty(),
                participants_count=0,
                color=0,
                channel=True,
                broadcast=True,
            )
        if isinstance(request, ImportChatInviteRequest):
            return SimpleNamespace(chats=[self.entity])
        return SimpleNamespace(chats=[])

    async def get_entity(self, reference):
        return self.entity

    async def send_file(self, entity, file, *, caption, force_document):
        self.sent_files.append((entity, file, caption, force_document))
        return SimpleNamespace(id=777)


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
    assert isinstance(client.requests[0], CheckChatInviteRequest)
    assert isinstance(client.requests[1], ImportChatInviteRequest)


@pytest.mark.asyncio
async def test_private_basic_group_is_rejected_before_joining() -> None:
    preview = ChatInvite(
        title="Private group",
        photo=ChatPhotoEmpty(),
        participants_count=0,
        color=0,
        channel=False,
        broadcast=False,
        megagroup=False,
    )
    client = FakeTelegramClient(None, invite_preview=preview)

    with pytest.raises(ChannelManagementError, match="not_broadcast"):
        await adapter_with(client).join_channel("https://t.me/+private_secret")

    assert [type(request) for request in client.requests] == [CheckChatInviteRequest]


@pytest.mark.asyncio
async def test_private_join_request_is_rejected_before_requesting_access() -> None:
    preview = ChatInvite(
        title="Approval channel",
        photo=ChatPhotoEmpty(),
        participants_count=0,
        color=0,
        channel=True,
        broadcast=True,
        request_needed=True,
    )
    client = FakeTelegramClient(None, invite_preview=preview)

    with pytest.raises(ChannelManagementError, match="join_request_required"):
        await adapter_with(client).join_channel("https://t.me/+private_secret")

    assert [type(request) for request in client.requests] == [CheckChatInviteRequest]


@pytest.mark.asyncio
async def test_public_join_request_is_rejected_before_requesting_access() -> None:
    client = FakeTelegramClient(
        SimpleNamespace(
            id=456,
            title="Approval channel",
            username="approval_jobs",
            broadcast=True,
            megagroup=False,
            join_request=True,
            left=True,
        )
    )

    with pytest.raises(ChannelManagementError, match="join_request_required"):
        await adapter_with(client).join_channel("@approval_jobs")

    assert client.requests == []


@pytest.mark.asyncio
async def test_already_joined_private_channel_is_resolved_without_import() -> None:
    entity = SimpleNamespace(
        id=456,
        title="Private jobs",
        username=None,
        broadcast=True,
        megagroup=False,
    )
    client = FakeTelegramClient(
        entity, invite_preview=ChatInviteAlready(chat=entity)
    )

    resolved = await adapter_with(client).join_channel(
        "https://t.me/+private_secret"
    )

    assert resolved.joined_now is False
    assert [type(request) for request in client.requests] == [CheckChatInviteRequest]


@pytest.mark.asyncio
async def test_private_peek_is_imported_before_persistence() -> None:
    entity = SimpleNamespace(
        id=456,
        title="Private jobs",
        username=None,
        broadcast=True,
        megagroup=False,
    )
    preview = ChatInvitePeek(
        chat=entity,
        expires=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    client = FakeTelegramClient(entity, invite_preview=preview)

    resolved = await adapter_with(client).join_channel(
        "https://t.me/+private_secret"
    )

    assert resolved.joined_now is True
    assert [type(request) for request in client.requests] == [
        CheckChatInviteRequest,
        ImportChatInviteRequest,
    ]


@pytest.mark.asyncio
async def test_join_rejects_a_public_group_before_joining() -> None:
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

    assert client.requests == []


@pytest.mark.asyncio
async def test_leave_channel_uses_stored_id() -> None:
    client = FakeTelegramClient(None)

    await adapter_with(client).leave_channel(-1000000000123)

    assert isinstance(client.requests[0], LeaveChannelRequest)


@pytest.mark.asyncio
async def test_send_private_with_document_uses_pdf_as_captioned_file(tmp_path) -> None:
    client = FakeTelegramClient(None)
    path = tmp_path / "resume.pdf"
    path.write_bytes(b"%PDF-1.7")

    message_id = await adapter_with(client).send_private_with_document(
        "hr_alex", "Здравствуйте!", path
    )

    assert message_id == 777
    assert client.sent_files == [
        ("hr_alex", str(path), "Здравствуйте!", True)
    ]
