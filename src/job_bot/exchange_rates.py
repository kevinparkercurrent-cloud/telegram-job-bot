from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from xml.etree import ElementTree

import httpx

from job_bot.db import Database


CBR_DAILY_URL = "https://www.cbr.ru/scripts/XML_daily.asp"


class CbrExchangeRates:
    def __init__(self, client: httpx.AsyncClient, database: Database) -> None:
        self._client = client
        self._database = database
        self._rates: dict[str, Decimal] = {"RUB": Decimal("1")}

    async def close(self) -> None:
        await self._client.aclose()

    async def refresh(self) -> None:
        try:
            response = await self._client.get(CBR_DAILY_URL, timeout=10.0)
            response.raise_for_status()
            rate_date, rates = self._parse(response.content)
        except (httpx.HTTPError, ElementTree.ParseError, ValueError):
            cached = await self._database.latest_exchange_rates()
            if cached is not None:
                _, rates = cached
                self._rates = {"RUB": Decimal("1"), **rates}
            return

        await self._database.save_exchange_rates(rate_date, rates)
        self._rates = {"RUB": Decimal("1"), **rates}

    async def rub_per_unit(self, currency: str) -> Decimal | None:
        return self._rates.get(currency.upper())

    @staticmethod
    def _parse(content: bytes) -> tuple[str, dict[str, Decimal]]:
        root = ElementTree.fromstring(content)
        rate_date = datetime.strptime(root.attrib["Date"], "%d.%m.%Y").date().isoformat()
        rates: dict[str, Decimal] = {}
        for item in root.findall("Valute"):
            currency = (item.findtext("CharCode") or "").strip().upper()
            nominal = Decimal((item.findtext("Nominal") or "0").replace(",", "."))
            value = Decimal((item.findtext("Value") or "0").replace(",", "."))
            if currency and nominal > 0:
                rates[currency] = value / nominal
        if not rates:
            raise ValueError("CBR response contains no rates")
        return rate_date, rates
