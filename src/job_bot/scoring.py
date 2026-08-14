from __future__ import annotations

from dataclasses import dataclass

from job_bot.config import CandidateProfile
from job_bot.domain import Assessment, MatchClass, Vacancy


@dataclass(frozen=True)
class ScoringPolicy:
    salary_floor_rub: int = 100_000
    strong_threshold: int = 75
    borderline_threshold: int = 50


ROLE_TERMS = (
    "technical project manager",
    "project manager",
    "product project manager",
    "it project manager",
    "product manager",
    "affiliate manager",
    "менеджер проектов",
    "проджект",
)
RESPONSIBILITY_TERMS = (
    "mobile",
    "мобильн",
    "web",
    "delivery",
    "release",
    "релиз",
    "команд",
    "team",
    "backlog",
    "бэклог",
)
SKILL_TERMS = (
    "qa",
    "api",
    "jira",
    "app store",
    "google play",
    "appsflyer",
    "branch",
    "kochava",
    "figma",
)
ABOVE_B1 = {"B2", "C1", "C2", "ADVANCED", "UPPER-INTERMEDIATE", "UPPER INTERMEDIATE"}


def _classify(score: int, policy: ScoringPolicy) -> MatchClass:
    if score >= policy.strong_threshold:
        return MatchClass.STRONG
    if score >= policy.borderline_threshold:
        return MatchClass.BORDERLINE
    return MatchClass.WEAK


def score_vacancy(
    vacancy: Vacancy,
    profile: CandidateProfile,
    policy: ScoringPolicy,
) -> Assessment:
    del profile  # The policy terms are derived only from the verified profile.
    if (
        vacancy.salary_present
        and vacancy.salary_min_rub is not None
        and vacancy.salary_min_rub < policy.salary_floor_rub
    ):
        return Assessment(
            score=0,
            match_class=MatchClass.REJECTED,
            reasons=[],
            warnings=list(vacancy.extraction_warnings),
            hard_rejection="Указанная нижняя граница зарплаты ниже 100 000 ₽",
        )

    text = f"{vacancy.title or ''} {vacancy.raw_text}".casefold()
    score = 0
    reasons: list[str] = []
    warnings = list(vacancy.extraction_warnings)

    if any(term in text for term in ROLE_TERMS):
        score += 35
        reasons.append("Целевая роль Product / Project Management")

    responsibility_count = sum(term in text for term in RESPONSIBILITY_TERMS)
    if responsibility_count >= 2:
        score += 25
        reasons.append("Релевантные обязанности по delivery мобильных или web-продуктов")
    elif responsibility_count == 1:
        score += 12
        reasons.append("Есть одна релевантная зона ответственности")

    if vacancy.remote is True or any(
        location in {"Вьетнам", "Юго-Восточная Азия"} for location in vacancy.locations
    ):
        score += 20
        reasons.append("Подходящий удалённый формат или регион")
    elif "Санкт-Петербург" in vacancy.locations:
        score += 15
        reasons.append("Допустимый запасной формат в Санкт-Петербурге")
    else:
        warnings.append("Формат и география требуют уточнения")

    if any(term in text for term in SKILL_TERMS):
        score += 10
        reasons.append("Есть подтверждённые технические или продуктовые навыки")

    if not vacancy.salary_present:
        score += 10
        reasons.append("Зарплата не указана и не используется для отсева")
    elif vacancy.salary_min_rub is None:
        warnings.append("Указанную зарплату не удалось пересчитать")
    else:
        score += 10
        reasons.append("Зарплата соответствует минимальному порогу")

    score = min(score, 100)
    match_class = _classify(score, policy)
    if vacancy.english_required and vacancy.english_required.upper() in ABOVE_B1:
        warnings.append(
            f"Требуется английский {vacancy.english_required}; подтверждённый уровень кандидата — B1"
        )
        if score >= policy.borderline_threshold:
            score = min(score, policy.strong_threshold - 1)
            match_class = MatchClass.BORDERLINE

    return Assessment(
        score=score,
        match_class=match_class,
        reasons=reasons,
        warnings=warnings,
    )
