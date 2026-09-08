from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from job_bot.db import Database
from job_bot.domain import Assessment, Draft, Vacancy
from job_bot.hr_discovery import HRContactCard, HR_OUTREACH_TEXT


class DigestItem(BaseModel):
    vacancy_id: str
    title: str
    score: int
    reasons: list[str]
    warnings: list[str]
    draft_text: str
    source_post_url: str | None = None
    summary: str | None = None


class ManualItem(BaseModel):
    vacancy_id: str
    title: str
    source_post_url: str | None = None
    summary: str | None = None


class DigestNotifier(Protocol):
    async def send_digest(self, items: list[DigestItem]) -> None:
        raise NotImplementedError

    async def send_manual_digest(self, items: list[ManualItem]) -> None:
        raise NotImplementedError

    async def send_hr_digest(self, items: list[HRContactCard]) -> None:
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
            vacancy_payload["source_post_url"] = row["source_post_url"]
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
                    source_post_url=(
                        str(vacancy.source_post_url)
                        if vacancy.source_post_url
                        else None
                    ),
                    summary=vacancy.summary,
                )
            )
        if items:
            await self._notifier.send_digest(items)
            await self._database.mark_notified(
                [item.vacancy_id for item in items], now
            )

        manual_items: list[ManualItem] = []
        for row in await self._database.list_manual_pending():
            vacancy_payload = json.loads(str(row["vacancy_json"]))
            vacancy_payload["raw_text"] = str(row["raw_text"])
            vacancy_payload["source_post_url"] = row["source_post_url"]
            vacancy = Vacancy.model_validate(vacancy_payload)
            manual_items.append(
                ManualItem(
                    vacancy_id=vacancy.id,
                    title=vacancy.title or "Вакансия без указанного названия",
                    source_post_url=(
                        str(vacancy.source_post_url)
                        if vacancy.source_post_url
                        else None
                    ),
                    summary=vacancy.summary,
                )
            )
        if manual_items:
            await self._notifier.send_manual_digest(manual_items)
            await self._database.mark_notified(
                [item.vacancy_id for item in manual_items], now
            )
        if current_time == max(self._digest_times):
            await self._send_daily_hr_digest(local.date().isoformat(), now)
        await self._database.set_setting("last_digest_slot", slot)
        return True

    async def _send_daily_hr_digest(self, local_date: str, now: datetime) -> None:
        if await self._database.get_setting("last_hr_digest_date") == local_date:
            return
        contacts = await self._database.list_hr_pending(limit=None)
        if contacts:
            items = [
                HRContactCard(
                    id=contact.id,
                    contact=contact.contact,
                    company=contact.company,
                    role=contact.role,
                    relevance_reason=contact.relevance_reason,
                    source_post_url=contact.source_post_url,
                    outreach_text=HR_OUTREACH_TEXT,
                )
                for contact in contacts
            ]
            await self._notifier.send_hr_digest(items)
            await self._database.mark_hr_notified(
                [contact.id for contact in contacts], now
            )
        await self._database.set_setting("last_hr_digest_date", local_date)

    async def run_retention(self, now: datetime) -> int:
        return await self._database.purge_raw_text(now - timedelta(days=30))
