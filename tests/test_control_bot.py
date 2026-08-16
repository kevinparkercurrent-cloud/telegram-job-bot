import pytest
from datetime import datetime, timezone

from job_bot.approvals import ApprovalService
from job_bot.control_bot import ControlBotService, ControlRequest, RuntimeControlActions
from job_bot.db import Database
from job_bot.domain import Assessment, Draft, MatchClass

ADMIN_ID = 42
NOW = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)


class FixedClock:
    def now(self) -> datetime:
        return NOW


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


@pytest.mark.asyncio
async def test_edit_prompt_returns_active_draft_and_starts_session(
    tmp_path, vacancy
) -> None:
    db = await Database.open(tmp_path / "edit-prompt.sqlite3")
    actions = RecordingActions()
    try:
        await db.insert_vacancy(vacancy)
        await db.save_draft(
            "draft-1",
            vacancy.id,
            Draft(text="Здравствуйте!", origin="template", evidence_ids=[]),
            "hash",
        )
        service = ControlBotService(db, ADMIN_ID, actions)

        response = await service.dispatch(
            ControlRequest(
                user_id=ADMIN_ID,
                callback_data=f"edit_prompt:{vacancy.id}",
            )
        )

        assert response.begin_edit_vacancy_id == vacancy.id
        assert "Текущий черновик" in response.text
        assert "Здравствуйте!" in response.text
        assert response.mutated is False
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_edit_prompt_does_not_start_without_active_draft(
    tmp_path, vacancy
) -> None:
    db = await Database.open(tmp_path / "missing-edit-prompt.sqlite3")
    try:
        await db.insert_vacancy(vacancy)
        service = ControlBotService(db, ADMIN_ID, RecordingActions())

        response = await service.dispatch(
            ControlRequest(
                user_id=ADMIN_ID,
                callback_data=f"edit_prompt:{vacancy.id}",
            )
        )

        assert response.begin_edit_vacancy_id is None
        assert "черновик" in response.text.casefold()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_replace_draft_returns_updated_vacancy_card(tmp_path, vacancy) -> None:
    db = await Database.open(tmp_path / "replace-draft.sqlite3")
    approvals = ApprovalService(db, FixedClock())
    linked = vacancy.model_copy(
        update={"source_post_url": "https://t.me/jobs_feed/7"}
    )
    try:
        await db.insert_vacancy(linked)
        await db.save_assessment(
            linked.id,
            Assessment(
                score=80,
                match_class=MatchClass.STRONG,
                reasons=["Роль подходит"],
            ),
        )
        await approvals.replace_draft(
            linked.id,
            Draft(text="Старый текст", origin="template", evidence_ids=[]),
        )
        actions = RuntimeControlActions(db, approvals, RecordingSafeSender())
        service = ControlBotService(db, ADMIN_ID, actions)

        response = await service.replace_draft(linked.id, "Новый текст")

        assert response.mutated is True
        assert response.card is not None
        assert response.card.draft_text == "Новый текст"
        assert response.card.source_post_url == "https://t.me/jobs_feed/7"
        active = await db.get_active_draft(linked.id)
        assert active is not None
        assert active[1].text == "Новый текст"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_replace_draft_rejects_text_over_3500_characters(tmp_path) -> None:
    db = await Database.open(tmp_path / "long-draft.sqlite3")
    actions = RecordingActions()
    try:
        service = ControlBotService(db, ADMIN_ID, actions)

        response = await service.replace_draft("v1", "x" * 3501)

        assert response.mutated is False
        assert "3500" in response.text
        assert actions.calls == []
    finally:
        await db.close()
