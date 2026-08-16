from datetime import datetime, timezone
from decimal import Decimal

import pytest

from job_bot.parsing import fingerprint, parse_vacancy


NOW = datetime(2026, 8, 14, tzinfo=timezone.utc)


class FixedRates:
    async def rub_per_unit(self, currency: str) -> Decimal | None:
        return {"RUB": Decimal("1"), "USD": Decimal("100")}.get(currency)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected", "salary_present"),
    [
        ("ЗП 120–160 тыс. ₽ net", 120_000, True),
        ("от $1500 gross", 150_000, True),
        ("зарплата не указана", None, False),
    ],
)
async def test_salary_lower_bound(text, expected, salary_present) -> None:
    vacancy = await parse_vacancy(-1, 7, NOW, text, FixedRates())
    assert vacancy.salary_min_rub == expected
    assert vacancy.salary_present is salary_present


@pytest.mark.asyncio
async def test_extracts_contact_and_external_url() -> None:
    vacancy = await parse_vacancy(
        -1001,
        8,
        NOW,
        "Technical Project Manager. Пишите @hr_alex. https://jobs.example/v/7",
        FixedRates(),
    )
    assert vacancy.recruiter_username == "hr_alex"
    assert [str(url) for url in vacancy.external_urls] == ["https://jobs.example/v/7"]


@pytest.mark.asyncio
async def test_preserves_original_telegram_post_url() -> None:
    vacancy = await parse_vacancy(
        -1001,
        9,
        NOW,
        "Project Manager, удалённо",
        FixedRates(),
        source_post_url="https://t.me/jobs_feed/9",
    )

    assert str(vacancy.source_post_url) == "https://t.me/jobs_feed/9"


def test_fingerprint_ignores_whitespace_and_url_order() -> None:
    first = fingerprint("Project   Manager\nremote", ["https://b.example", "https://a.example"])
    second = fingerprint(" project manager remote ", ["https://a.example", "https://b.example"])
    assert first == second
