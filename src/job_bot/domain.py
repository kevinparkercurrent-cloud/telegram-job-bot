from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, HttpUrl


class MatchClass(StrEnum):
    STRONG = "strong"
    BORDERLINE = "borderline"
    WEAK = "weak"
    REJECTED = "rejected"


class VacancyStatus(StrEnum):
    NEW = "new"
    ASSESSED = "assessed"
    QUEUED = "queued"
    SENT = "sent"
    SKIPPED = "skipped"
    NOT_RELEVANT = "not_relevant"
    FAILED = "failed"


class Vacancy(BaseModel):
    id: str
    channel_id: int
    message_id: int
    published_at: datetime
    fingerprint: str
    raw_text: str
    title: str | None = None
    company: str | None = None
    salary_min_rub: int | None = None
    salary_present: bool = False
    remote: bool | None = None
    locations: list[str] = Field(default_factory=list)
    english_required: str | None = None
    recruiter_username: str | None = None
    external_urls: list[HttpUrl] = Field(default_factory=list)
    extraction_warnings: list[str] = Field(default_factory=list)


class Assessment(BaseModel):
    score: int = Field(ge=0, le=100)
    match_class: MatchClass
    reasons: list[str]
    warnings: list[str] = Field(default_factory=list)
    hard_rejection: str | None = None


class Draft(BaseModel):
    text: str
    origin: str
    evidence_ids: list[str]
