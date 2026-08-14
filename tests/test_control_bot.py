import pytest

from job_bot.control_bot import ControlBotService, ControlRequest
from job_bot.db import Database


ADMIN_ID = 42


@pytest.mark.asyncio
async def test_non_admin_callback_is_rejected(tmp_path) -> None:
    db = await Database.open(tmp_path / "control.sqlite3")
    try:
        service = ControlBotService(db, ADMIN_ID)
        response = await service.dispatch(
            ControlRequest(user_id=999, callback_data="approve:v1")
        )
        assert response.alert == "Доступ запрещён"
        assert response.mutated is False
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_channels_add_persists_numeric_channel_id(tmp_path) -> None:
    db = await Database.open(tmp_path / "control.sqlite3")
    try:
        service = ControlBotService(db, ADMIN_ID)
        response = await service.dispatch(
            ControlRequest(user_id=ADMIN_ID, text="/channels add -100123 jobs")
        )
        assert response.text == "Канал jobs добавлен"
        assert response.mutated is True
        assert await db.is_allowed_channel(-100123)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_malformed_channels_command_does_not_change_state(tmp_path) -> None:
    db = await Database.open(tmp_path / "control.sqlite3")
    try:
        service = ControlBotService(db, ADMIN_ID)
        response = await service.dispatch(
            ControlRequest(user_id=ADMIN_ID, text="/channels add not-a-number jobs")
        )
        assert response.mutated is False
        assert await db.list_channels() == []
    finally:
        await db.close()
