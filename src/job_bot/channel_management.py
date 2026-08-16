from __future__ import annotations

import re
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Literal, Protocol
from urllib.parse import urlparse

from job_bot.db import Database, StoredChannel


_USERNAME = re.compile(r"^[A-Za-z0-9_]{5,32}$")
_INVITE_HASH = re.compile(r"^[A-Za-z0-9_-]+$")


class ChannelManagementError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ChannelReference:
    kind: Literal["public", "private"]
    value: str = field(repr=False)


@dataclass(frozen=True)
class ResolvedChannel:
    channel_id: int
    title: str
    username: str | None
    joined_now: bool


class ChannelMembership(Protocol):
    async def join_channel(self, reference: str) -> ResolvedChannel: ...
    async def leave_channel(self, channel_id: int) -> None: ...


@dataclass(frozen=True)
class RemovalResult:
    channel: StoredChannel
    left: bool


class ChannelManagementService:
    def __init__(self, database: Database, membership: ChannelMembership) -> None:
        self._database = database
        self._membership = membership

    async def add(self, reference: str) -> StoredChannel:
        resolved = await self._membership.join_channel(reference)
        try:
            await self._database.add_channel(
                resolved.channel_id, resolved.title, resolved.username
            )
        except ValueError:
            await self._rollback_join(resolved)
            raise ChannelManagementError("capacity_reached") from None
        except Exception:
            await self._rollback_join(resolved)
            raise ChannelManagementError("persistence_failed") from None
        stored = await self._database.get_channel(resolved.channel_id)
        if stored is None:
            await self._rollback_join(resolved)
            raise ChannelManagementError("persistence_failed")
        return stored

    async def remove(self, channel_id: int) -> RemovalResult:
        stored = await self._database.get_channel(channel_id)
        if stored is None:
            raise ChannelManagementError("not_found")
        try:
            await self._database.remove_channel(channel_id)
        except Exception:
            raise ChannelManagementError("persistence_failed") from None
        try:
            await self._membership.leave_channel(channel_id)
        except Exception:
            return RemovalResult(channel=stored, left=False)
        return RemovalResult(channel=stored, left=True)

    async def _rollback_join(self, resolved: ResolvedChannel) -> None:
        if resolved.joined_now:
            with suppress(Exception):
                await self._membership.leave_channel(resolved.channel_id)


def parse_channel_reference(raw: str) -> ChannelReference:
    value = raw.strip()
    if value.startswith("@"):
        return _public_reference(value[1:])
    if _USERNAME.fullmatch(value):
        return ChannelReference("public", value)

    candidate = value
    if candidate.casefold().startswith("t.me/"):
        candidate = f"https://{candidate}"
    parsed = urlparse(candidate)
    if (
        parsed.scheme.casefold() != "https"
        or parsed.netloc.casefold() not in {"t.me", "www.t.me"}
        or parsed.query
        or parsed.fragment
    ):
        raise ChannelManagementError("invalid_reference")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) == 1 and parts[0].startswith("+"):
        return _private_reference(parts[0][1:])
    if len(parts) == 2 and parts[0].casefold() == "joinchat":
        return _private_reference(parts[1])
    if len(parts) == 1:
        return _public_reference(parts[0])
    raise ChannelManagementError("invalid_reference")


def _public_reference(value: str) -> ChannelReference:
    if not _USERNAME.fullmatch(value):
        raise ChannelManagementError("invalid_reference")
    return ChannelReference("public", value)


def _private_reference(value: str) -> ChannelReference:
    if not _INVITE_HASH.fullmatch(value):
        raise ChannelManagementError("invalid_reference")
    return ChannelReference("private", value)
