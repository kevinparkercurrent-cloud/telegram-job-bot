import pytest
from datetime import datetime, timezone

from job_bot.approvals import ApprovalService
from job_bot.channel_management import ChannelManagementService, ResolvedChannel
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


class ChannelMembership:
    def __init__(self) -> None:
        self.left: list[int] = []

    async def join_channel(self, reference: str) -> ResolvedChannel:
        assert reference == "https://t.me/jobs_feed"
        return ResolvedChannel(
            channel_id=-1000000000123,
            title="Jobs feed",
            username="jobs_feed",
            joined_now=True,
        )

    async def leave_channel(self, channel_id: int) -> None:
        self.left.append(channel_id)


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
async def test_channels_add_accepts_public_link(tmp_path) -> None:
    db = await Database.open(tmp_path / "control.sqlite3")
    membership = ChannelMembership()
    try:
        service = ControlBotService(
            db,
            ADMIN_ID,
            channel_manager=ChannelManagementService(db, membership),
        )
        response = await service.dispatch(
            ControlRequest(
                user_id=ADMIN_ID,
                text="/channels add https://t.me/jobs_feed",
            )
        )
        assert "Jobs feed" in response.text
        assert response.mutated is True
        assert response.channel_menu is not None
        assert await db.is_allowed_channel(-1000000000123)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_channels_duplicate_addition_is_reported(tmp_path) -> None:
    db = await Database.open(tmp_path / "duplicate-control.sqlite3")
    membership = ChannelMembership()
    try:
        service = ControlBotService(
            db,
            ADMIN_ID,
            channel_manager=ChannelManagementService(db, membership),
        )
        request = ControlRequest(
            user_id=ADMIN_ID,
            text="/channels add https://t.me/jobs_feed",
        )

        await service.dispatch(request)
        duplicate = await service.dispatch(request)

        assert "уже" in duplicate.text.casefold()
        assert len(await db.list_channels()) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_malformed_channels_command_does_not_change_state(tmp_path) -> None:
    db = await Database.open(tmp_path / "control.sqlite3")
    try:
        service = ControlBotService(db, ADMIN_ID)
        response = await service.dispatch(
            ControlRequest(user_id=ADMIN_ID, text="/channels add")
        )
        assert response.mutated is False
        assert await db.list_channels() == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_channels_menu_paginates_ten_items(tmp_path) -> None:
    db = await Database.open(tmp_path / "pages.sqlite3")
    try:
        for index in range(11):
            await db.add_channel(-1000 - index, f"Channel {index:02}")
        service = ControlBotService(db, ADMIN_ID)

        first = await service.dispatch(
            ControlRequest(user_id=ADMIN_ID, text="/channels")
        )
        second = await service.dispatch(
            ControlRequest(user_id=ADMIN_ID, callback_data="channels:list:1")
        )

        assert first.channel_menu is not None
        assert first.channel_menu.total == 11
        assert first.channel_menu.pages == 2
        assert len(first.channel_menu.items) == 10
        assert second.channel_menu is not None
        assert len(second.channel_menu.items) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("count", "pages", "last_page_items"),
    ((0, 1, 0), (10, 1, 10), (100, 10, 10)),
)
async def test_channels_menu_pagination_boundaries(
    tmp_path, count: int, pages: int, last_page_items: int
) -> None:
    db = await Database.open(tmp_path / f"boundary-{count}.sqlite3")
    try:
        for index in range(count):
            await db.add_channel(-1000 - index, f"Channel {index:03}")
        service = ControlBotService(db, ADMIN_ID)

        response = await service.dispatch(
            ControlRequest(
                user_id=ADMIN_ID,
                callback_data=f"channels:list:{pages - 1}",
            )
        )

        assert response.channel_menu is not None
        assert response.channel_menu.pages == pages
        assert len(response.channel_menu.items) == last_page_items
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_channels_add_callback_starts_input_flow(tmp_path) -> None:
    db = await Database.open(tmp_path / "add-prompt.sqlite3")
    try:
        service = ControlBotService(db, ADMIN_ID)

        response = await service.dispatch(
            ControlRequest(user_id=ADMIN_ID, callback_data="channels:add")
        )

        assert response.begin_channel_add is True
        assert "ссылку" in response.text.casefold()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_channels_remove_requires_confirmation_then_leaves(tmp_path) -> None:
    db = await Database.open(tmp_path / "remove-channel.sqlite3")
    membership = ChannelMembership()
    try:
        await db.add_channel(-1000000000123, "Jobs feed", "jobs_feed")
        service = ControlBotService(
            db,
            ADMIN_ID,
            channel_manager=ChannelManagementService(db, membership),
        )

        prompt = await service.dispatch(
            ControlRequest(
                user_id=ADMIN_ID,
                callback_data="channels:pick:-1000000000123:0",
            )
        )
        assert prompt.remove_confirmation is not None
        assert await db.is_allowed_channel(-1000000000123)

        removed = await service.dispatch(
            ControlRequest(
                user_id=ADMIN_ID,
                callback_data="channels:confirm:-1000000000123:0",
            )
        )

        assert removed.mutated is True
        assert not await db.is_allowed_channel(-1000000000123)
        assert membership.left == [-1000000000123]
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
async def test_replace_draft_rejects_text_over_1000_characters(tmp_path) -> None:
    db = await Database.open(tmp_path / "long-draft.sqlite3")
    actions = RecordingActions()
    try:
        service = ControlBotService(db, ADMIN_ID, actions)

        response = await service.replace_draft("v1", "x" * 1001)

        assert response.mutated is False
        assert "1000" in response.text
        assert actions.calls == []
    finally:
        await db.close()
