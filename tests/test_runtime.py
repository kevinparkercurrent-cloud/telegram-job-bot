from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from job_bot.control_bot import ChannelMenu, ControlResponse, RemovalConfirmation
from job_bot.db import StoredChannel
from job_bot.pipeline import VacancyCard
from job_bot.runtime import (
    AiogramControlRuntime,
    channel_confirmation_keyboard,
    channel_menu_keyboard,
    vacancy_keyboard,
)


ADMIN_ID = 42
NOW = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)


def card(vacancy_id: str, text: str) -> VacancyCard:
    return VacancyCard(
        vacancy_id=vacancy_id,
        title="Project Manager",
        score=80,
        match_class="strong",
        reasons=["Роль подходит"],
        warnings=[],
        recruiter_username="hr_alex",
        source_post_url="https://t.me/jobs_feed/7",
        draft_text=text,
        draft_origin="user",
    )


class MutableClock:
    def __init__(self) -> None:
        self.value = NOW

    def now(self) -> datetime:
        return self.value


class RecordingService:
    def __init__(self) -> None:
        self.dispatched = []
        self.replacements: list[tuple[str, str]] = []
        self.channel_additions: list[str] = []

    async def dispatch(self, request):
        self.dispatched.append(request)
        if request.user_id != ADMIN_ID:
            return ControlResponse(alert="Доступ запрещён")
        if request.callback_data:
            if request.callback_data == "channels:add":
                return ControlResponse(
                    text="Отправьте ссылку",
                    begin_channel_add=True,
                )
            vacancy_id = request.callback_data.partition(":")[2]
            return ControlResponse(
                text="Текущий черновик: Старый текст",
                begin_edit_vacancy_id=vacancy_id,
            )
        return ControlResponse(text=f"Команда: {request.text}")

    async def add_channel(self, reference: str) -> ControlResponse:
        self.channel_additions.append(reference)
        channel = StoredChannel(-100123, "Jobs", "jobs_feed")
        return ControlResponse(
            text="Канал добавлен",
            mutated=True,
            channel_menu=ChannelMenu((channel,), 0, 1, 1),
        )

    async def replace_draft(self, vacancy_id: str, text: str) -> ControlResponse:
        self.replacements.append((vacancy_id, text))
        return ControlResponse(
            text="Черновик обновлён",
            mutated=True,
            card=card(vacancy_id, text),
        )


class FakeMessage:
    def __init__(self, text: str | None, user_id: int = ADMIN_ID) -> None:
        self.from_user = SimpleNamespace(id=user_id)
        self.text = text
        self.answers: list[str] = []
        self.markups = []

    async def answer(self, text: str, reply_markup=None) -> None:
        self.answers.append(text)
        self.markups.append(reply_markup)


class FakeCallback:
    def __init__(self, data: str, user_id: int = ADMIN_ID) -> None:
        self.from_user = SimpleNamespace(id=user_id)
        self.data = data
        self.message = FakeMessage(None, user_id)
        self.answers: list[tuple[str, bool]] = []

    async def answer(self, text: str, show_alert: bool = False) -> None:
        self.answers.append((text, show_alert))


class RecordingRuntime(AiogramControlRuntime):
    def __init__(self, service: RecordingService, clock: MutableClock) -> None:
        super().__init__(
            "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
            ADMIN_ID,
            service,
            now=clock.now,
        )
        self.sent_cards: list[VacancyCard] = []

    async def send_card(self, item: VacancyCard) -> None:
        self.sent_cards.append(item)


def test_keyboard_contains_original_post_and_edit_buttons() -> None:
    keyboard = vacancy_keyboard("v1", "https://t.me/jobs_feed/7")
    buttons = [button for row in keyboard.inline_keyboard for button in row]

    assert any(
        button.text == "Открыть вакансию"
        and button.url == "https://t.me/jobs_feed/7"
        for button in buttons
    )
    assert any(
        button.text == "Редактировать отклик"
        and button.callback_data == "edit_prompt:v1"
        for button in buttons
    )


def test_keyboard_omits_url_button_without_source_link() -> None:
    keyboard = vacancy_keyboard("v1", None)

    assert all(
        button.url is None
        for row in keyboard.inline_keyboard
        for button in row
    )


def test_channel_menu_keyboard_contains_add_remove_and_navigation() -> None:
    menu = ChannelMenu(
        items=(StoredChannel(-100123, "Jobs", "jobs_feed"),),
        page=1,
        pages=3,
        total=21,
    )

    keyboard = channel_menu_keyboard(menu)
    buttons = [button for row in keyboard.inline_keyboard for button in row]

    assert any(button.callback_data == "channels:add" for button in buttons)
    assert any(button.callback_data == "channels:remove:1" for button in buttons)
    assert any(button.callback_data == "channels:list:0" for button in buttons)
    assert any(button.callback_data == "channels:list:2" for button in buttons)


def test_channel_remove_menu_selects_channel_and_confirmation_is_explicit() -> None:
    channel = StoredChannel(-100123, "Jobs", None)
    menu = ChannelMenu((channel,), 0, 1, 1, mode="remove")

    selection = channel_menu_keyboard(menu)
    confirmation = channel_confirmation_keyboard(
        RemovalConfirmation(channel=channel, page=0)
    )

    assert selection.inline_keyboard[0][0].callback_data == "channels:pick:-100123:0"
    callbacks = {
        button.callback_data
        for row in confirmation.inline_keyboard
        for button in row
    }
    assert callbacks == {"channels:confirm:-100123:0", "channels:cancel:0"}


@pytest.mark.asyncio
async def test_edit_button_routes_next_text_to_draft_replacement() -> None:
    service = RecordingService()
    runtime = RecordingRuntime(service, MutableClock())
    callback = FakeCallback("edit_prompt:v1")

    await runtime._on_callback(callback)
    replacement = FakeMessage("Новый отклик")
    await runtime._on_message(replacement)

    assert service.replacements == [("v1", "Новый отклик")]
    assert replacement.answers == ["Черновик обновлён"]
    assert runtime.sent_cards[-1].draft_text == "Новый отклик"


@pytest.mark.asyncio
async def test_cancel_discards_pending_edit() -> None:
    service = RecordingService()
    runtime = RecordingRuntime(service, MutableClock())
    await runtime._on_callback(FakeCallback("edit_prompt:v1"))

    cancel = FakeMessage("/cancel")
    await runtime._on_message(cancel)
    ordinary = FakeMessage("Не менять")
    await runtime._on_message(ordinary)

    assert cancel.answers == ["Редактирование отменено"]
    assert service.replacements == []
    assert service.dispatched[-1].text == "Не менять"


@pytest.mark.asyncio
async def test_command_does_not_overwrite_pending_draft() -> None:
    service = RecordingService()
    runtime = RecordingRuntime(service, MutableClock())
    await runtime._on_callback(FakeCallback("edit_prompt:v1"))

    await runtime._on_message(FakeMessage("/status"))
    await runtime._on_message(FakeMessage("Новый текст"))

    assert service.replacements == [("v1", "Новый текст")]
    assert any(request.text == "/status" for request in service.dispatched)


@pytest.mark.asyncio
async def test_non_text_message_keeps_pending_edit() -> None:
    service = RecordingService()
    runtime = RecordingRuntime(service, MutableClock())
    await runtime._on_callback(FakeCallback("edit_prompt:v1"))

    non_text = FakeMessage(None)
    await runtime._on_message(non_text)
    await runtime._on_message(FakeMessage("Новый текст"))

    assert "текст" in non_text.answers[0].casefold()
    assert service.replacements == [("v1", "Новый текст")]


@pytest.mark.asyncio
async def test_too_long_message_keeps_pending_edit() -> None:
    service = RecordingService()
    runtime = RecordingRuntime(service, MutableClock())
    await runtime._on_callback(FakeCallback("edit_prompt:v1"))

    too_long = FakeMessage("x" * 1001)
    await runtime._on_message(too_long)
    await runtime._on_message(FakeMessage("Короткий текст"))

    assert "1000" in too_long.answers[0]
    assert service.replacements == [("v1", "Короткий текст")]


@pytest.mark.asyncio
async def test_pending_edit_expires_after_fifteen_minutes() -> None:
    service = RecordingService()
    clock = MutableClock()
    runtime = RecordingRuntime(service, clock)
    await runtime._on_callback(FakeCallback("edit_prompt:v1"))
    clock.value = NOW + timedelta(minutes=15, seconds=1)

    expired = FakeMessage("Поздний текст")
    await runtime._on_message(expired)

    assert "истекло" in expired.answers[0].casefold()
    assert service.replacements == []


@pytest.mark.asyncio
async def test_new_edit_button_replaces_previous_pending_vacancy() -> None:
    service = RecordingService()
    runtime = RecordingRuntime(service, MutableClock())
    await runtime._on_callback(FakeCallback("edit_prompt:v1"))
    await runtime._on_callback(FakeCallback("edit_prompt:v2"))

    await runtime._on_message(FakeMessage("Новый текст"))

    assert service.replacements == [("v2", "Новый текст")]


@pytest.mark.asyncio
async def test_channel_add_button_routes_next_text_to_channel_service() -> None:
    service = RecordingService()
    runtime = RecordingRuntime(service, MutableClock())

    await runtime._on_callback(FakeCallback("channels:add"))
    message = FakeMessage("https://t.me/jobs_feed")
    await runtime._on_message(message)

    assert service.channel_additions == ["https://t.me/jobs_feed"]
    assert message.answers[0].startswith("Канал добавлен")
    assert message.markups[0] is not None


@pytest.mark.asyncio
async def test_channel_add_can_be_cancelled_and_expires() -> None:
    service = RecordingService()
    clock = MutableClock()
    runtime = RecordingRuntime(service, clock)
    await runtime._on_callback(FakeCallback("channels:add"))

    await runtime._on_message(FakeMessage("/status"))
    clock.value = NOW + timedelta(minutes=15, seconds=1)
    expired = FakeMessage("@jobs_feed")
    await runtime._on_message(expired)

    assert service.channel_additions == []
    assert "истекло" in expired.answers[0].casefold()

    await runtime._on_callback(FakeCallback("channels:add"))
    cancelled = FakeMessage("/cancel")
    await runtime._on_message(cancelled)
    assert "отмен" in cancelled.answers[0].casefold()


@pytest.mark.asyncio
async def test_channel_add_and_vacancy_edit_states_are_mutually_exclusive() -> None:
    service = RecordingService()
    runtime = RecordingRuntime(service, MutableClock())

    await runtime._on_callback(FakeCallback("edit_prompt:v1"))
    await runtime._on_callback(FakeCallback("channels:add"))
    await runtime._on_message(FakeMessage("@jobs_feed"))

    assert service.replacements == []
    assert service.channel_additions == ["@jobs_feed"]
