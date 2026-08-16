from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from telethon import TelegramClient, events
from telethon.errors import (
    ChannelPrivateError,
    FloodWaitError,
    InviteHashExpiredError,
    InviteHashInvalidError,
    UserAlreadyParticipantError,
)
from telethon.events.newmessage import NewMessage
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest
from telethon.tl.functions.messages import (
    CheckChatInviteRequest,
    ImportChatInviteRequest,
)
from telethon.tl.types import PeerChannel

from job_bot.channel_management import (
    ChannelManagementError,
    ResolvedChannel,
    parse_channel_reference,
)
from job_bot.collector import ChannelPost
from job_bot.sender import ResolvedUser


PostHandler = Callable[[ChannelPost], Awaitable[object]]


def telegram_post_url(
    chat_id: int, message_id: int, username: str | None
) -> str | None:
    if username:
        return f"https://t.me/{username.lstrip('@')}/{message_id}"
    value = str(chat_id)
    if value.startswith("-100") and len(value) > 4:
        return f"https://t.me/c/{value[4:]}/{message_id}"
    return None


def telethon_event_to_post(
    event: NewMessage.Event, *, username: str | None = None
) -> ChannelPost:
    message = event.message
    return ChannelPost(
        channel_id=int(event.chat_id),
        message_id=int(message.id),
        published_at=message.date,
        text=message.raw_text or "",
        source_post_url=telegram_post_url(
            int(event.chat_id), int(message.id), username
        ),
    )


class TelethonUserAdapter:
    def __init__(
        self,
        session_path: Path,
        api_id: int,
        api_hash: str,
        phone: str | None,
    ) -> None:
        self._client = TelegramClient(str(session_path), api_id, api_hash)
        self._phone = phone
        self._handler: Callable[[NewMessage.Event], Awaitable[None]] | None = None

    async def start(self, on_post: PostHandler) -> None:
        await self._client.start(phone=self._phone)

        async def receive(event: NewMessage.Event) -> None:
            if not event.is_channel:
                return
            chat = await event.get_chat()
            await on_post(
                telethon_event_to_post(
                    event, username=getattr(chat, "username", None)
                )
            )

        self._handler = receive
        self._client.add_event_handler(receive, events.NewMessage(incoming=True))

    async def stop(self) -> None:
        if self._handler is not None:
            self._client.remove_event_handler(self._handler)
        await self._client.disconnect()

    async def resolve_user(self, username: str) -> ResolvedUser:
        entity = await self._client.get_entity(username)
        if not hasattr(entity, "id") or not hasattr(entity, "bot"):
            raise LookupError("Recipient is not a Telegram user")
        resolved_username = getattr(entity, "username", None) or username
        return ResolvedUser(
            user_id=int(entity.id),
            username=str(resolved_username),
            is_bot=bool(entity.bot),
        )

    async def send_private(self, username: str, text: str) -> int:
        message = await self._client.send_message(username, text)
        return int(message.id)

    async def join_channel(self, reference: str) -> ResolvedChannel:
        parsed = parse_channel_reference(reference)
        joined_now = False
        try:
            if parsed.kind == "public":
                try:
                    await self._client(JoinChannelRequest(parsed.value))
                    joined_now = True
                except UserAlreadyParticipantError:
                    pass
                entity = await self._client.get_entity(parsed.value)
            else:
                try:
                    updates = await self._client(
                        ImportChatInviteRequest(parsed.value)
                    )
                    joined_now = True
                    entity = updates.chats[0]
                except UserAlreadyParticipantError:
                    invite = await self._client(
                        CheckChatInviteRequest(parsed.value)
                    )
                    entity = invite.chat
        except (InviteHashExpiredError, InviteHashInvalidError):
            raise ChannelManagementError("invite_expired") from None
        except FloodWaitError:
            raise ChannelManagementError("rate_limited") from None
        except ChannelPrivateError:
            raise ChannelManagementError("telegram_unavailable") from None
        except ChannelManagementError:
            raise
        except Exception:
            raise ChannelManagementError("telegram_unavailable") from None

        if not _is_broadcast_channel(entity):
            if joined_now:
                await self._leave_entity_safely(entity)
            raise ChannelManagementError("not_broadcast")
        return ResolvedChannel(
            channel_id=_marked_channel_id(int(entity.id)),
            title=str(entity.title),
            username=(
                str(entity.username) if getattr(entity, "username", None) else None
            ),
            joined_now=joined_now,
        )

    async def leave_channel(self, channel_id: int) -> None:
        real_id, peer_type = _resolve_marked_channel_id(channel_id)
        if peer_type is not PeerChannel:
            raise ChannelManagementError("not_broadcast")
        try:
            await self._client(LeaveChannelRequest(PeerChannel(real_id)))
        except FloodWaitError:
            raise ChannelManagementError("rate_limited") from None
        except Exception:
            raise ChannelManagementError("telegram_unavailable") from None

    async def _leave_entity_safely(self, entity: object) -> None:
        try:
            await self._client(LeaveChannelRequest(entity))
        except Exception:
            pass


def _is_broadcast_channel(entity: object) -> bool:
    return (
        hasattr(entity, "id")
        and hasattr(entity, "title")
        and getattr(entity, "broadcast", False) is True
        and getattr(entity, "megagroup", False) is False
    )


def _marked_channel_id(channel_id: int) -> int:
    return -(1_000_000_000_000 + channel_id)


def _resolve_marked_channel_id(channel_id: int) -> tuple[int, type]:
    if channel_id <= -1_000_000_000_000:
        return -channel_id - 1_000_000_000_000, PeerChannel
    return channel_id, object
