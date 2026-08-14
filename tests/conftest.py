from pathlib import Path
from datetime import datetime, timezone

import pytest

from job_bot.domain import Vacancy


@pytest.fixture
def profile_path() -> Path:
    return Path(__file__).parents[1] / "config" / "candidate-profile.full.json"


@pytest.fixture
def vacancy() -> Vacancy:
    return Vacancy(
        id="vacancy-1",
        channel_id=-100123,
        message_id=7,
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        fingerprint="fingerprint-1",
        raw_text="Technical Project Manager, удалённо",
        title="Technical Project Manager",
        recruiter_username="hr_alex",
    )
