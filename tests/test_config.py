from pathlib import Path

import pytest

from job_bot.config import Settings, load_candidate_profile


def test_settings_reject_missing_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "TELEGRAM_API_ID",
        "TELEGRAM_API_HASH",
        "CONTROL_BOT_TOKEN",
        "ADMIN_TELEGRAM_ID",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValueError):
        Settings.load()


def test_settings_loads_safe_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "hash")
    monkeypatch.setenv("CONTROL_BOT_TOKEN", "token")
    monkeypatch.setenv("ADMIN_TELEGRAM_ID", "42")

    settings = Settings.load()

    assert settings.openai_model == "gpt-5.4-mini"
    assert settings.strong_threshold == 75
    assert settings.borderline_threshold == 50
    assert settings.digest_times == ("12:00", "19:00")


def test_candidate_profile_keeps_confirmed_search_facts(profile_path: Path) -> None:
    profile = load_candidate_profile(profile_path)

    english = next(item for item in profile.languages if item.language == "Английский")
    assert english.level == "B1"
    assert profile.job_search.minimum_income.amount == 1500
    assert profile.job_search.minimum_income.currency == "USD"


def test_candidate_profile_exposes_stable_verified_fact_ids(profile_path: Path) -> None:
    profile = load_candidate_profile(profile_path)

    fact_id = "job_search.minimum_income.amount"
    assert fact_id in profile.fact_ids
    assert profile.fact_text(fact_id) == "1500"
    with pytest.raises(KeyError):
        profile.fact_text("not.a.real.fact")

