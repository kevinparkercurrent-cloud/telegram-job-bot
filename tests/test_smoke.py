import pytest

from job_bot.db import Database
from job_bot.smoke import inspect_database


@pytest.mark.asyncio
async def test_smoke_inspection_accepts_initialized_database(tmp_path) -> None:
    path = tmp_path / "smoke.sqlite3"
    database = await Database.open(path)
    await database.close()

    result = await inspect_database(path)

    assert result["database_ok"] is True
    assert result["schema_version"] == 1
    assert result["channel_count"] == 0
    assert result["queue_count"] == 0
    assert "last_digest_slot" in result
