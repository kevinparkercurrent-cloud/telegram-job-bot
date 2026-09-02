import pytest

from job_bot.db import Database
from job_bot.smoke import inspect_database, inspect_resume_pdf


@pytest.mark.asyncio
async def test_smoke_inspection_accepts_initialized_database(tmp_path) -> None:
    path = tmp_path / "smoke.sqlite3"
    database = await Database.open(path)
    await database.close()

    result = await inspect_database(path)

    assert result["database_ok"] is True
    assert result["schema_version"] == 1
    assert result["channel_count"] == 0
    assert result["channels_username_column"] == 1
    assert result["queue_count"] == 0
    assert "last_digest_slot" in result


def test_resume_inspection_requires_real_pdf_header(tmp_path) -> None:
    valid = tmp_path / "resume.pdf"
    invalid = tmp_path / "not-resume.pdf"
    valid.write_bytes(b"%PDF-1.7\nresume")
    invalid.write_text("not a pdf", encoding="utf-8")

    assert inspect_resume_pdf(valid) is True
    assert inspect_resume_pdf(invalid) is False
    assert inspect_resume_pdf(tmp_path / "missing.pdf") is False
