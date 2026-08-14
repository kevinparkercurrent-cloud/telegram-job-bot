from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from job_bot.db import Database
from job_bot.domain import Assessment, Draft, Vacancy


class DigestItem(BaseModel):
    vacancy_id: str
    title: str
    score: int
    reasons: list[str]
    warnings: list[str]
    draft_text: str


class DigestNotifier(Protocol):
    async def send_digest(self, items: list[DigestItem]) -> None:
        raise NotImplementedError


class Scheduler:
    def __init__(
        self,
        database: Database,
        notifier: DigestNotifier,
        timezone_name: str,
        digest_times: tuple[str, str],
    ) -> None:
        self._database = database
        self._notifier = notifier
        self._timezone = ZoneInfo(timezone_name)
        self._digest_times = frozenset(digest_times)

    async def tick(self, now: datetime) -> bool:
        local = now.astimezone(self._timezone)
        current_time = local.strftime("%H:%M")
        if current_time not in self._digest_times:
            return False
        slot = f"{local.date().isoformat()}T{current_time}@{self._timezone.key}"
        if await self._database.get_setting("last_digest_slot") == slot:
            return False

        rows = await self._database.list_digest_pending()
        items: list[DigestItem] = []
        for row in rows:
            vacancy_payload = json.loads(str(row["vacancy_json"]))
            vacancy_payload["raw_text"] = str(row["raw_text"])
            vacancy = Vacancy.model_validate(vacancy_payload)
            assessment = Assessment.model_validate_json(str(row["assessment_json"]))
            draft = Draft.model_validate_json(str(row["draft_json"]))
            items.append(
                DigestItem(
                    vacancy_id=vacancy.id,
                    title=vacancy.title or "Вакансия без указанного названия",
                    score=assessment.score,
                    reasons=assessment.reasons,
                    warnings=assessment.warnings,
                    draft_text=draft.text,
                )
            )
        if items:
            await self._notifier.send_digest(items)
            await self._database.mark_notified(
                [item.vacancy_id for item in items], now
            )
        await self._database.set_setting("last_digest_slot", slot)
        return True

    async def run_retention(self, now: datetime) -> int:
        return await self._database.purge_raw_text(now - timedelta(days=30))
