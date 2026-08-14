from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from job_bot.db import Database


@dataclass(frozen=True)
class ChannelPost:
    channel_id: int
    message_id: int
    published_at: datetime
    text: str


class CollectionResult(StrEnum):
    IGNORED = "ignored"
    PROCESSED = "processed"
    DUPLICATE = "duplicate"


class VacancyPipelineProtocol(Protocol):
    async def process_post(self, post: ChannelPost) -> bool:
        raise NotImplementedError


class Collector:
    def __init__(self, database: Database, pipeline: VacancyPipelineProtocol) -> None:
        self._database = database
        self._pipeline = pipeline

    async def handle(self, post: ChannelPost) -> CollectionResult:
        if not await self._database.is_allowed_channel(post.channel_id):
            return CollectionResult.IGNORED
        accepted = await self._pipeline.process_post(post)
        return CollectionResult.PROCESSED if accepted else CollectionResult.DUPLICATE

