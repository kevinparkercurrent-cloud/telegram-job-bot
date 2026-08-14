from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from telethon import TelegramClient, events
from telethon.events.newmessage import NewMessage

from job_bot.collector import ChannelPost
from job_bot.sender import ResolvedUser


PostHandler = Callable[[ChannelPost], Awaitable[object]]


def telethon_event_to_post(event: NewMessage.Event) -> ChannelPost:
    message = event.message
    return ChannelPost(
        channel_id=int(event.chat_id),
        message_id=int(message.id),
        published_at=message.date,
        text=message.raw_text or "",
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
            await on_post(telethon_event_to_post(event))

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
