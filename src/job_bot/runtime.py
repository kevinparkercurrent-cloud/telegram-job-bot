from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from aiogram import Bot, Dispatcher
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from job_bot.collector import Collector
from job_bot.control_bot import (
    ChannelMenu,
    ControlBotService,
    ControlRequest,
    ControlResponse,
    RemovalConfirmation,
)
from job_bot.domain import MAX_DRAFT_LENGTH
from job_bot.pipeline import VacancyCard
from job_bot.scheduler import DigestItem, Scheduler
from job_bot.telegram_adapters import TelethonUserAdapter


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True)
class PendingEdit:
    vacancy_id: str
    expires_at: datetime


@dataclass(frozen=True)
class PendingChannelAdd:
    expires_at: datetime


def channel_menu_keyboard(menu: ChannelMenu) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if menu.mode == "remove":
        for channel in menu.items:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=channel.label[:50],
                        callback_data=(
                            f"channels:pick:{channel.channel_id}:{menu.page}"
                        ),
                    )
                ]
            )
    if menu.pages > 1:
        navigation: list[InlineKeyboardButton] = []
        action = "remove" if menu.mode == "remove" else "list"
        if menu.page > 0:
            navigation.append(
                InlineKeyboardButton(
                    text="←",
                    callback_data=f"channels:{action}:{menu.page - 1}",
                )
            )
        navigation.append(
            InlineKeyboardButton(
                text=f"{menu.page + 1}/{menu.pages}",
                callback_data=f"channels:{action}:{menu.page}",
            )
        )
        if menu.page + 1 < menu.pages:
            navigation.append(
                InlineKeyboardButton(
                    text="→",
                    callback_data=f"channels:{action}:{menu.page + 1}",
                )
            )
        rows.append(navigation)
    if menu.mode == "remove":
        rows.append(
            [
                InlineKeyboardButton(
                    text="Назад", callback_data=f"channels:list:{menu.page}"
                )
            ]
        )
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Добавить канал", callback_data="channels:add"
                )
            ]
        )
        if menu.total:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="Удалить канал",
                        callback_data=f"channels:remove:{menu.page}",
                    )
                ]
            )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def channel_confirmation_keyboard(
    confirmation: RemovalConfirmation,
) -> InlineKeyboardMarkup:
    channel_id = confirmation.channel.channel_id
    page = confirmation.page
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Удалить",
                    callback_data=f"channels:confirm:{channel_id}:{page}",
                ),
                InlineKeyboardButton(
                    text="Отмена", callback_data=f"channels:cancel:{page}"
                ),
            ]
        ]
    )


def vacancy_keyboard(
    vacancy_id: str, source_post_url: str | None
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if source_post_url:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Открыть вакансию", url=source_post_url
                )
            ]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="Редактировать отклик",
                    callback_data=f"edit_prompt:{vacancy_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Откликнуться", callback_data=f"approve:{vacancy_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Пропустить", callback_data=f"skip:{vacancy_id}"
                ),
                InlineKeyboardButton(
                    text="Не подходит", callback_data=f"not_relevant:{vacancy_id}"
                ),
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_card(card: VacancyCard) -> str:
    reasons = "\n".join(f"• {item}" for item in card.reasons) or "• нет"
    warnings = "\n".join(f"• {item}" for item in card.warnings) or "• нет"
    contact = f"@{card.recruiter_username}" if card.recruiter_username else "не найден"
    return (
        f"{card.title}\nСовпадение: {card.score}/100 ({card.match_class})\n\n"
        f"Почему подходит:\n{reasons}\n\nПредупреждения:\n{warnings}\n\n"
        f"Контакт: {contact}\nЧерновик:\n{card.draft_text}"
    )[:4096]


class AiogramControlRuntime:
    def __init__(
        self,
        token: str,
        admin_user_id: int,
        service: ControlBotService,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._bot = Bot(token=token)
        self._dispatcher = Dispatcher()
        self._admin_user_id = admin_user_id
        self._service = service
        self._now = now or (lambda: datetime.now(UTC))
        self._pending_edits: dict[int, PendingEdit] = {}
        self._pending_channel_adds: dict[int, PendingChannelAdd] = {}
        self._task: asyncio.Task[None] | None = None
        self.polling = False
        self._dispatcher.message.register(self._on_message)
        self._dispatcher.callback_query.register(self._on_callback)

    async def _on_message(self, message: Message) -> None:
        if message.from_user is None:
            return
        user_id = message.from_user.id
        text = message.text
        if text == "/cancel":
            edit = self._pending_edits.pop(user_id, None)
            channel = self._pending_channel_adds.pop(user_id, None)
            if edit is not None:
                await message.answer("Редактирование отменено")
            elif channel is not None:
                await message.answer("Добавление канала отменено")
            else:
                await message.answer("Нет активного редактирования")
            return
        if text and text.startswith("/"):
            response = await self._service.dispatch(
                ControlRequest(user_id=user_id, text=text)
            )
            await self._answer_message(message, response)
            return

        pending_channel = self._pending_channel_adds.get(user_id)
        if pending_channel is not None:
            if pending_channel.expires_at <= self._now():
                self._pending_channel_adds.pop(user_id, None)
                await message.answer(
                    "Время добавления канала истекло; нажмите кнопку ещё раз"
                )
                return
            if not text or not text.strip():
                await message.answer("Отправьте ссылку на канал текстом")
                return
            response = await self._service.add_channel(text.strip())
            if response.mutated:
                self._pending_channel_adds.pop(user_id, None)
            await self._answer_message(message, response)
            return

        pending = self._pending_edits.get(user_id)
        if pending is not None:
            if pending.expires_at <= self._now():
                self._pending_edits.pop(user_id, None)
                await message.answer(
                    "Время редактирования истекло; нажмите кнопку ещё раз"
                )
                return
            if not text or not text.strip():
                await message.answer("Отправьте новый текст отклика сообщением")
                return
            replacement = text.strip()
            if len(replacement) > MAX_DRAFT_LENGTH:
                await message.answer(
                    f"Текст отклика не должен превышать {MAX_DRAFT_LENGTH} символов"
                )
                return
            response = await self._service.replace_draft(
                pending.vacancy_id, replacement
            )
            if response.mutated:
                self._pending_edits.pop(user_id, None)
            await self._answer_message(message, response)
            if response.card is not None:
                await self.send_card(response.card)
            return

        response = await self._service.dispatch(
            ControlRequest(user_id=user_id, text=text)
        )
        await self._answer_message(message, response)

    async def _on_callback(self, query: CallbackQuery) -> None:
        if query.from_user is None:
            return
        response = await self._service.dispatch(
            ControlRequest(user_id=query.from_user.id, callback_data=query.data)
        )
        if (
            query.from_user.id == self._admin_user_id
            and response.begin_edit_vacancy_id is not None
        ):
            self._pending_channel_adds.pop(query.from_user.id, None)
            self._pending_edits[query.from_user.id] = PendingEdit(
                vacancy_id=response.begin_edit_vacancy_id,
                expires_at=self._now() + timedelta(minutes=15),
            )
        if (
            query.from_user.id == self._admin_user_id
            and response.begin_channel_add
        ):
            self._pending_edits.pop(query.from_user.id, None)
            self._pending_channel_adds[query.from_user.id] = PendingChannelAdd(
                expires_at=self._now() + timedelta(minutes=15)
            )
        await query.answer(response.alert or "", show_alert=bool(response.alert))
        if response.text and query.message is not None:
            await self._answer_message(query.message, response)

    async def _answer_message(
        self, message: Message, response: ControlResponse
    ) -> None:
        text = response.alert or response.text
        if not text:
            return
        markup = None
        if response.channel_menu is not None:
            text = self._format_channel_menu(text, response.channel_menu)
            markup = channel_menu_keyboard(response.channel_menu)
        elif response.remove_confirmation is not None:
            markup = channel_confirmation_keyboard(response.remove_confirmation)
        await message.answer(text, reply_markup=markup)

    @staticmethod
    def _format_channel_menu(text: str, menu: ChannelMenu) -> str:
        if menu.mode == "remove" or not menu.items:
            return text
        lines = []
        for channel in menu.items:
            reference = f"@{channel.username}" if channel.username else "приватный"
            lines.append(f"• {channel.label} — {reference}")
        return f"{text}\n\n" + "\n".join(lines)

    async def send_card(self, card: VacancyCard) -> None:
        await self._bot.send_message(
            self._admin_user_id,
            format_card(card),
            reply_markup=vacancy_keyboard(
                card.vacancy_id, card.source_post_url
            ),
        )

    async def send_digest(self, items: list[DigestItem]) -> None:
        await self._bot.send_message(
            self._admin_user_id,
            f"Пограничные вакансии: {len(items)}",
        )
        for item in items:
            card = VacancyCard(
                vacancy_id=item.vacancy_id,
                title=item.title,
                score=item.score,
                match_class="borderline",
                reasons=item.reasons,
                warnings=item.warnings,
                recruiter_username=None,
                source_post_url=item.source_post_url,
                draft_text=item.draft_text,
                draft_origin="stored",
            )
            await self.send_card(card)

    async def start(self) -> None:
        if self._task is not None:
            return
        self.polling = True
        self._task = asyncio.create_task(
            self._dispatcher.start_polling(self._bot, handle_signals=False),
            name="control-bot-polling",
        )
        await asyncio.sleep(0)

    async def stop(self) -> None:
        self.polling = False
        if self._task is not None and not self._task.done():
            await self._dispatcher.stop_polling()
        if self._task is not None:
            with suppress(asyncio.CancelledError):
                await self._task
        await self._bot.session.close()


class CollectorIngress:
    def __init__(self, telegram: TelethonUserAdapter, collector: Collector) -> None:
        self._telegram = telegram
        self._collector = collector

    async def start(self) -> None:
        await self._telegram.start(self._collector.handle)

    async def stop(self) -> None:
        await self._telegram.stop()


class PeriodicScheduler:
    def __init__(
        self, scheduler: Scheduler, refresh_rates, interval: float = 30.0
    ) -> None:
        self._scheduler = scheduler
        self._refresh_rates = refresh_rates
        self._interval = interval
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        await self._refresh_rates()
        self._task = asyncio.create_task(self._run(), name="job-bot-scheduler")

    async def _run(self) -> None:
        last_retention_date = None
        while not self._stopping.is_set():
            now = datetime.now(UTC)
            await self._scheduler.tick(now)
            if last_retention_date != now.date():
                await self._scheduler.run_retention(now)
                last_retention_date = now.date()
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self._interval)
            except TimeoutError:
                pass

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            await self._task


class AsyncCloser:
    def __init__(self, close) -> None:
        self._close = close

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        await self._close()
