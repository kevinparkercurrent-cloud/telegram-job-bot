from pathlib import Path

import pytest

from job_bot.config import load_candidate_profile
from job_bot.domain import Assessment, MatchClass
from job_bot.drafting import (
    Enrichment,
    OpenAIDrafter,
    TemplateDrafter,
    UngroundedOutput,
)
from job_bot.sources import ExternalVacancy


class RecordingAI:
    def __init__(self, evidence_id: str) -> None:
        self.evidence_id = evidence_id
        self.last_external_text = ""
        self.last_profile_facts: dict[str, str] = {}

    async def enrich(
        self,
        external_text: str,
        profile_facts: dict[str, str],
        model: str,
    ) -> Enrichment:
        self.last_external_text = external_text
        self.last_profile_facts = profile_facts
        return Enrichment(
            score_adjustment=5,
            reasons=["Совпадает опыт delivery"],
            warnings=[],
            draft="Здравствуйте! Готов обсудить вакансию.",
            evidence_ids=[self.evidence_id],
        )


def test_template_uses_only_profile_evidence(vacancy) -> None:
    profile = load_candidate_profile(Path("config/candidate-profile.full.json"))
    assessment = Assessment(
        score=80,
        match_class=MatchClass.STRONG,
        reasons=["Целевая роль"],
    )
    draft = TemplateDrafter().create(vacancy, assessment, profile)
    assert draft.origin == "telegram_rules_template"
    assert "C1" not in draft.text
    assert draft.evidence_ids
    assert all(evidence_id in profile.fact_ids for evidence_id in draft.evidence_ids)


@pytest.mark.asyncio
async def test_ai_receives_external_text_not_telegram_text(vacancy) -> None:
    profile = load_candidate_profile(Path("config/candidate-profile.full.json"))
    evidence_id = "achievements.0.statement"
    ai = RecordingAI(evidence_id)
    vacancy = vacancy.model_copy(update={"raw_text": "TELEGRAM_SENTINEL"})

    result = await OpenAIDrafter(ai).create(
        ExternalVacancy(url="https://jobs.example/7", text="EXTERNAL_SOURCE"),
        vacancy,
        profile,
    )

    assert result.evidence_ids == [evidence_id]
    assert "EXTERNAL_SOURCE" in ai.last_external_text
    assert "TELEGRAM_SENTINEL" not in ai.last_external_text
    assert "TELEGRAM_SENTINEL" not in " ".join(ai.last_profile_facts.values())


@pytest.mark.asyncio
async def test_ai_output_with_unknown_evidence_is_rejected(vacancy) -> None:
    profile = load_candidate_profile(Path("config/candidate-profile.full.json"))
    ai = RecordingAI("invented.fact")
    with pytest.raises(UngroundedOutput):
        await OpenAIDrafter(ai).create(
            ExternalVacancy(url="https://jobs.example/7", text="External vacancy"),
            vacancy,
            profile,
        )
