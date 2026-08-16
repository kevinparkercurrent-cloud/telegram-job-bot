from __future__ import annotations

import hashlib
import uuid
from typing import Protocol

from pydantic import BaseModel

from job_bot.collector import ChannelPost
from job_bot.config import CandidateProfile
from job_bot.db import Database
from job_bot.domain import Assessment, Draft, MatchClass, VacancyStatus
from job_bot.drafting import OpenAIDrafter, TemplateDrafter
from job_bot.parsing import ExchangeRates, parse_vacancy
from job_bot.scoring import ScoringPolicy, score_vacancy
from job_bot.sources import ExternalVacancy


class VacancyCard(BaseModel):
    vacancy_id: str
    title: str
    score: int
    match_class: str
    reasons: list[str]
    warnings: list[str]
    recruiter_username: str | None
    source_post_url: str | None
    draft_text: str
    draft_origin: str


class SourceFetcherProtocol(Protocol):
    async def fetch(self, url: str) -> ExternalVacancy | None:
        raise NotImplementedError


class Notifier(Protocol):
    async def send_card(self, card: VacancyCard) -> None:
        raise NotImplementedError


class VacancyPipeline:
    def __init__(
        self,
        database: Database,
        rates: ExchangeRates,
        profile: CandidateProfile,
        policy: ScoringPolicy,
        source_fetcher: SourceFetcherProtocol,
        template_drafter: TemplateDrafter,
        ai_drafter: OpenAIDrafter | None,
        notifier: Notifier,
    ) -> None:
        self.db = database
        self._rates = rates
        self._profile = profile
        self._policy = policy
        self._source_fetcher = source_fetcher
        self._template_drafter = template_drafter
        self._ai_drafter = ai_drafter
        self._notifier = notifier

    async def process_post(self, post: ChannelPost) -> bool:
        vacancy = await parse_vacancy(
            post.channel_id,
            post.message_id,
            post.published_at,
            post.text,
            self._rates,
            source_post_url=post.source_post_url,
        )
        if not await self.db.insert_vacancy(vacancy):
            return False

        assessment = score_vacancy(vacancy, self._profile, self._policy)
        await self.db.save_assessment(vacancy.id, assessment)
        draft = await self._create_draft(vacancy, assessment)
        draft_hash = hashlib.sha256(draft.text.encode("utf-8")).hexdigest()
        await self.db.save_draft(str(uuid.uuid4()), vacancy.id, draft, draft_hash)

        status = (
            VacancyStatus.QUEUED.value
            if assessment.match_class in {MatchClass.STRONG, MatchClass.BORDERLINE}
            else VacancyStatus.ASSESSED.value
        )
        await self.db.set_vacancy_status(vacancy.id, status)
        if assessment.match_class == MatchClass.STRONG:
            await self._notifier.send_card(
                VacancyCard(
                    vacancy_id=vacancy.id,
                    title=vacancy.title or "Вакансия без указанного названия",
                    score=assessment.score,
                    match_class=assessment.match_class.value,
                    reasons=assessment.reasons,
                    warnings=assessment.warnings,
                    recruiter_username=vacancy.recruiter_username,
                    source_post_url=(
                        str(vacancy.source_post_url)
                        if vacancy.source_post_url
                        else None
                    ),
                    draft_text=draft.text,
                    draft_origin=draft.origin,
                )
            )
        return True

    async def _create_draft(self, vacancy, assessment: Assessment) -> Draft:
        if self._ai_drafter is not None and vacancy.external_urls:
            external = await self._source_fetcher.fetch(str(vacancy.external_urls[0]))
            if external is not None:
                try:
                    return await self._ai_drafter.create(
                        external, vacancy, self._profile
                    )
                except Exception:
                    pass
        return self._template_drafter.create(vacancy, assessment, self._profile)
