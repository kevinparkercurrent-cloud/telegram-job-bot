from __future__ import annotations

from typing import Protocol

from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from job_bot.config import CandidateProfile
from job_bot.domain import Assessment, Draft, Vacancy
from job_bot.sources import ExternalVacancy


class UngroundedOutput(ValueError):
    pass


class Enrichment(BaseModel):
    score_adjustment: int = Field(ge=-20, le=20)
    reasons: list[str]
    warnings: list[str]
    draft: str = Field(min_length=1, max_length=2000)
    evidence_ids: list[str] = Field(min_length=1)


class StructuredAI(Protocol):
    async def enrich(
        self,
        external_text: str,
        profile_facts: dict[str, str],
        model: str,
    ) -> Enrichment:
        raise NotImplementedError


class TemplateDrafter:
    SUMMARY_FACT = "professional_summary.summary_ru"
    ACHIEVEMENT_FACT = "achievements.0.statement"

    def create(
        self,
        vacancy: Vacancy,
        assessment: Assessment,
        profile: CandidateProfile,
    ) -> Draft:
        del assessment
        evidence_ids = [self.SUMMARY_FACT, self.ACHIEVEMENT_FACT]
        summary = profile.fact_text(self.SUMMARY_FACT)
        achievement = profile.fact_text(self.ACHIEVEMENT_FACT)
        title = vacancy.title or "Project Manager"
        text = (
            f"Здравствуйте! Меня заинтересовала вакансия {title}. "
            f"{summary} {achievement} Буду рад обсудить задачи и ожидания от роли."
        )
        return Draft(
            text=text,
            origin="telegram_rules_template",
            evidence_ids=evidence_ids,
        )


class OpenAIDrafter:
    def __init__(self, client: StructuredAI, model: str = "gpt-5.4-mini") -> None:
        self._client = client
        self._model = model

    async def create(
        self,
        external: ExternalVacancy,
        vacancy: Vacancy,
        profile: CandidateProfile,
    ) -> Draft:
        del vacancy
        profile_facts = {
            fact_id: profile.fact_text(fact_id) for fact_id in sorted(profile.fact_ids)
        }
        enrichment = await self._client.enrich(
            external.text,
            profile_facts,
            self._model,
        )
        unknown = set(enrichment.evidence_ids) - profile.fact_ids
        if unknown:
            raise UngroundedOutput(
                f"AI referenced unknown candidate facts: {sorted(unknown)}"
            )
        return Draft(
            text=enrichment.draft,
            origin="external_source_ai",
            evidence_ids=enrichment.evidence_ids,
        )


class OpenAIResponsesClient:
    def __init__(self, client: AsyncOpenAI) -> None:
        self._client = client

    async def enrich(
        self,
        external_text: str,
        profile_facts: dict[str, str],
        model: str,
    ) -> Enrichment:
        facts = "\n".join(f"{key}: {value}" for key, value in profile_facts.items())
        response = await self._client.responses.parse(
            model=model,
            input=[
                {
                    "role": "system",
                    "content": (
                        "Analyze only the supplied external vacancy. Draft a concise Russian response. "
                        "Use only candidate facts listed below and cite their exact IDs. "
                        "Never infer missing experience.\n\nCandidate facts:\n" + facts
                    ),
                },
                {"role": "user", "content": external_text},
            ],
            text_format=Enrichment,
        )
        parsed = response.output_parsed
        if parsed is None:
            raise UngroundedOutput("OpenAI response did not contain structured output")
        return parsed
