from __future__ import annotations

import hashlib
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol, Sequence
from urllib.parse import urlparse

from job_bot.domain import Vacancy


class ExchangeRates(Protocol):
    async def rub_per_unit(self, currency: str) -> Decimal | None:
        raise NotImplementedError


URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
USERNAME_RE = re.compile(r"(?<![\w@])@([A-Za-z][A-Za-z0-9_]{4,31})\b")
NO_SALARY_RE = re.compile(r"(?:зарплат[аы]|зп)\s+(?:не\s+)?указан[аы]?", re.IGNORECASE)
USD_RE = re.compile(r"(?:от\s*)?\$\s*([\d\s]+(?:[.,]\d+)?)", re.IGNORECASE)
EUR_RE = re.compile(r"(?:от\s*)?€\s*([\d\s]+(?:[.,]\d+)?)", re.IGNORECASE)
RUB_THOUSANDS_RE = re.compile(
    r"(?:от\s*)?(\d+(?:[.,]\d+)?)\s*(?:[–—-]\s*\d+(?:[.,]\d+)?\s*)?(?:тыс(?:\.|яч[аи]?)?)\s*(?:₽|руб)",
    re.IGNORECASE,
)
RUB_RE = re.compile(r"(?:от\s*)?([\d][\d\s]{3,})\s*(?:₽|руб)", re.IGNORECASE)


def _number(value: str) -> Decimal:
    return Decimal(value.replace(" ", "").replace(",", "."))


def fingerprint(text: str, urls: Sequence[str]) -> str:
    normalized_text = " ".join(text.casefold().split())
    canonical_urls = sorted(url.rstrip("/").casefold() for url in urls)
    payload = normalized_text + "\n" + "\n".join(canonical_urls)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _salary(
    text: str, rates: ExchangeRates
) -> tuple[bool, int | None, list[str]]:
    if NO_SALARY_RE.search(text):
        return False, None, []
    candidates = (
        (RUB_THOUSANDS_RE, "RUB", Decimal("1000")),
        (RUB_RE, "RUB", Decimal("1")),
        (USD_RE, "USD", Decimal("1")),
        (EUR_RE, "EUR", Decimal("1")),
    )
    for pattern, currency, multiplier in candidates:
        match = pattern.search(text)
        if match is None:
            continue
        try:
            amount = _number(match.group(1)) * multiplier
        except InvalidOperation:
            return True, None, ["Не удалось разобрать указанную зарплату"]
        rate = await rates.rub_per_unit(currency)
        if rate is None:
            return True, None, [f"Нет курса для {currency}; зарплата не отфильтрована"]
        return True, int(amount * rate), []
    return False, None, []


def _external_urls(text: str) -> list[str]:
    urls: list[str] = []
    for match in URL_RE.findall(text):
        url = match.rstrip(".,);]")
        host = (urlparse(url).hostname or "").casefold()
        if host in {"t.me", "telegram.me", "telegram.dog"}:
            continue
        if url not in urls:
            urls.append(url)
    return urls


async def parse_vacancy(
    channel_id: int,
    message_id: int,
    published_at: datetime,
    text: str,
    rates: ExchangeRates,
    *,
    source_post_url: str | None = None,
) -> Vacancy:
    urls = _external_urls(text)
    salary_present, salary_min_rub, warnings = await _salary(text, rates)
    username_match = USERNAME_RE.search(text)
    folded = text.casefold()
    title = next(
        (
            title
            for title in (
                "Technical Project Manager",
                "Product Project Manager",
                "IT Project Manager",
                "Project Manager",
                "Product Manager",
                "Affiliate Manager",
            )
            if title.casefold() in folded
        ),
        None,
    )
    english_match = re.search(r"\b(B1|B2|C1|C2|advanced|upper[ -]intermediate)\b", text, re.I)
    locations = [
        location
        for marker, location in (
            ("вьетнам", "Вьетнам"),
            ("vietnam", "Вьетнам"),
            ("санкт-петербург", "Санкт-Петербург"),
            ("saint petersburg", "Санкт-Петербург"),
            ("юго-восточ", "Юго-Восточная Азия"),
            ("southeast asia", "Юго-Восточная Азия"),
        )
        if marker in folded
    ]
    remote = True if re.search(r"\b(remote|удал[её]нн\w*)\b", folded) else None
    return Vacancy(
        id=f"{channel_id}:{message_id}",
        channel_id=channel_id,
        message_id=message_id,
        published_at=published_at,
        fingerprint=fingerprint(text, urls),
        raw_text=text,
        title=title,
        salary_min_rub=salary_min_rub,
        salary_present=salary_present,
        remote=remote,
        locations=list(dict.fromkeys(locations)),
        english_required=english_match.group(1).upper() if english_match else None,
        recruiter_username=username_match.group(1) if username_match else None,
        source_post_url=source_post_url,
        external_urls=urls,
        extraction_warnings=warnings,
    )
