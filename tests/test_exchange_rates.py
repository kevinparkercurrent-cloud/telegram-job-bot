from decimal import Decimal

import httpx
import pytest

from job_bot.db import Database
from job_bot.exchange_rates import CBR_DAILY_URL, CbrExchangeRates


CBR_XML = """<?xml version="1.0" encoding="windows-1251"?>
<ValCurs Date="14.08.2026" name="Foreign Currency Market">
  <Valute ID="R01235"><NumCode>840</NumCode><CharCode>USD</CharCode><Nominal>1</Nominal><Name>Доллар США</Name><Value>90,0000</Value></Valute>
  <Valute ID="R01820"><NumCode>392</NumCode><CharCode>JPY</CharCode><Nominal>100</Nominal><Name>Иен</Name><Value>85,0000</Value></Valute>
</ValCurs>""".encode("cp1251")


@pytest.mark.asyncio
async def test_cbr_rates_are_nominal_aware(tmp_path, respx_mock) -> None:
    respx_mock.get(CBR_DAILY_URL).mock(
        return_value=httpx.Response(200, content=CBR_XML)
    )
    db = await Database.open(tmp_path / "rates.sqlite3")
    try:
        rates = CbrExchangeRates(httpx.AsyncClient(), db)
        await rates.refresh()

        assert await rates.rub_per_unit("RUB") == Decimal("1")
        assert await rates.rub_per_unit("USD") == Decimal("90.0000")
        assert await rates.rub_per_unit("JPY") == Decimal("0.8500")
    finally:
        await rates.close()
        await db.close()


@pytest.mark.asyncio
async def test_cbr_rates_fall_back_to_database_cache(tmp_path, respx_mock) -> None:
    db = await Database.open(tmp_path / "rates.sqlite3")
    try:
        respx_mock.get(CBR_DAILY_URL).mock(
            return_value=httpx.Response(200, content=CBR_XML)
        )
        first = CbrExchangeRates(httpx.AsyncClient(), db)
        await first.refresh()
        await first.close()

        respx_mock.get(CBR_DAILY_URL).mock(side_effect=httpx.ConnectError("offline"))
        cached = CbrExchangeRates(httpx.AsyncClient(), db)
        await cached.refresh()
        assert await cached.rub_per_unit("USD") == Decimal("90.0000")
        await cached.close()
    finally:
        await db.close()

