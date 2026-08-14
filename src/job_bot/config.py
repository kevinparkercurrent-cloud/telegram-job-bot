from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr
from pydantic_settings import BaseSettings, SettingsConfigDict


class MinimumIncome(BaseModel):
    model_config = ConfigDict(extra="allow")

    amount: int
    currency: str
    period: str
    gross_or_net: str | None = None


class JobSearch(BaseModel):
    model_config = ConfigDict(extra="allow")

    minimum_income: MinimumIncome


class Language(BaseModel):
    model_config = ConfigDict(extra="allow")

    language: str
    level: str
    limitations: str | None = None


def _flatten_facts(value: Any, prefix: str = "") -> dict[str, str]:
    facts: dict[str, str] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            facts.update(_flatten_facts(child, child_prefix))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            child_prefix = f"{prefix}.{index}" if prefix else str(index)
            facts.update(_flatten_facts(child, child_prefix))
    elif value is not None:
        facts[prefix] = str(value)
    return facts


class CandidateProfile(BaseModel):
    model_config = ConfigDict(extra="allow")

    job_search: JobSearch
    languages: list[Language]
    _facts: dict[str, str] = PrivateAttr(default_factory=dict)

    def model_post_init(self, context: Any, /) -> None:
        evidence_roots = {
            "identity",
            "job_search",
            "professional_summary",
            "career_tracks",
            "experience",
            "projects",
            "achievements",
            "skills",
            "tools_and_technologies",
            "education",
            "languages",
            "portfolio",
            "career_context",
        }
        profile_data = self.model_dump(mode="json")
        self._facts = _flatten_facts(
            {key: value for key, value in profile_data.items() if key in evidence_roots}
        )

    @property
    def fact_ids(self) -> set[str]:
        return set(self._facts)

    def fact_text(self, fact_id: str) -> str:
        return self._facts[fact_id]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    telegram_api_id: int
    telegram_api_hash: str = Field(min_length=1)
    control_bot_token: str = Field(min_length=1)
    admin_telegram_id: int
    telegram_phone: str | None = None
    telegram_session_path: Path = Path("data/telegram.session")
    openai_api_key: str | None = None
    openai_model: str = "gpt-5.4-mini"
    external_source_domains: tuple[str, ...] = ()
    candidate_profile_path: Path = Path("config/candidate-profile.full.json")
    database_path: Path = Path("data/job-bot.sqlite3")
    app_timezone: str = "Europe/Moscow"
    digest_times: tuple[str, str] = ("12:00", "19:00")
    strong_threshold: int = 75
    borderline_threshold: int = 50
    hourly_send_limit: int = 5
    daily_send_limit: int = 15
    backup_age_recipient: str | None = None

    @classmethod
    def load(cls) -> "Settings":
        return cls()


def load_candidate_profile(path: Path) -> CandidateProfile:
    return CandidateProfile.model_validate_json(path.read_text(encoding="utf-8"))
