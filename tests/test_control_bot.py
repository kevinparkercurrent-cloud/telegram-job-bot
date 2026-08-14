import pytest

from job_bot.control_bot import ControlBotService, ControlRequest, RuntimeControlActions
from job_bot.db import Database
from job_bot.domain import Draft

ADMIN_ID = 42


class RecordingActions:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []

    async def approve(self, vacancy_id: str) -> str:
        self.calls.append(("approve", vacancy_id, None))
        return "Отклик отправлен"

    async def edit(self, vacancy_id: str, text: str) -> str:
        self.calls.append(("edit", vacancy_id, text))
        return "Черновик обновлён"

    async def decide(self, vacancy_id: str, decision: str) -> str:
        self.calls.append((decision, vacancy_id, None))
        return "Решение сохранено"


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


@pytest.mark.asyncio
async def test_approve_callback_delegates_to_safe_action(tmp_path) -> None:
    db = await Database.open(tmp_path / "control.sqlite3")
    actions = RecordingActions()
    try:
        service = ControlBotService(db, ADMIN_ID, actions)

        response = await service.dispatch(
            ControlRequest(user_id=ADMIN_ID, callback_data="approve:v1")
        )

        assert response.text == "Отклик отправлен"
        assert response.mutated is True
        assert actions.calls == [("approve", "v1", None)]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_edit_command_replaces_bound_draft(tmp_path) -> None:
    db = await Database.open(tmp_path / "control.sqlite3")
    actions = RecordingActions()
    try:
        service = ControlBotService(db, ADMIN_ID, actions)

        response = await service.dispatch(
            ControlRequest(user_id=ADMIN_ID, text="/edit v1 Новый текст")
        )

        assert response.text == "Черновик обновлён"
        assert actions.calls == [("edit", "v1", "Новый текст")]
    finally:
        await db.close()


class RecordingApprovalService:
    def __init__(self) -> None:
        self.replacements: list[tuple[str, Draft]] = []

    async def replace_draft(self, vacancy_id: str, draft: Draft) -> str:
        self.replacements.append((vacancy_id, draft))
        return "draft-id"

    async def issue(self, vacancy_id: str, recipient: str):
        return type("Approval", (), {"id": "approval-id"})()


class RecordingSafeSender:
    async def send(self, approval_id: str):
        assert approval_id == "approval-id"
        return type("Result", (), {"status": "sent"})()


@pytest.mark.asyncio
async def test_runtime_action_requires_recruiter_username(tmp_path, vacancy) -> None:
    db = await Database.open(tmp_path / "actions.sqlite3")
    approvals = RecordingApprovalService()
    actions = RuntimeControlActions(db, approvals, RecordingSafeSender())
    try:
        await db.insert_vacancy(vacancy.model_copy(update={"recruiter_username": None}))
        assert (
            await actions.approve(vacancy.id)
            == "В вакансии нет Telegram-контакта рекрутера"
        )
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_runtime_edit_creates_user_origin_draft(tmp_path) -> None:
    db = await Database.open(tmp_path / "actions.sqlite3")
    approvals = RecordingApprovalService()
    actions = RuntimeControlActions(db, approvals, RecordingSafeSender())
    try:
        result = await actions.edit("v1", "Новый текст")
        assert result == "Черновик обновлён; подтвердите отклик заново"
        assert approvals.replacements[0][1].origin == "user"
    finally:
        await db.close()
