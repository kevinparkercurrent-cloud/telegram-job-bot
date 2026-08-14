import logging

import pytest

from job_bot.db import Database
from job_bot.observability import HealthService, RedactingFilter


class TelegramState:
    session_valid = True
    last_collected_at = None


class BotState:
    polling = True


class BackupState:
    last_success_at = None


def test_log_filter_redacts_credentials_and_contacts(caplog) -> None:
    logger = logging.getLogger("job-bot-redaction-test")
    logger.addFilter(RedactingFilter())
    with caplog.at_level(logging.INFO, logger=logger.name):
        logger.info(
            "token=123456:ABCdef_secret phone=+79991234567 email=a@example.com key=sk-testsecret"
        )
    assert "123456:ABCdef_secret" not in caplog.text
    assert "+79991234567" not in caplog.text
    assert "a@example.com" not in caplog.text
    assert "sk-testsecret" not in caplog.text
    assert "[REDACTED]" in caplog.text


@pytest.mark.asyncio
async def test_revoked_session_marks_sending_unhealthy(tmp_path) -> None:
    db = await Database.open(tmp_path / "health.sqlite3")
    telegram = TelegramState()
    telegram.session_valid = False
    try:
        snapshot = await HealthService(
            db, telegram, BotState(), BackupState(), ai_available=True
        ).snapshot()
        assert snapshot.database_ok is True
        assert snapshot.can_collect is False
        assert snapshot.can_send is False
    finally:
        await db.close()
