from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime

from aiogram import Bot, Dispatcher
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from job_bot.collector import Collector
from job_bot.control_bot import ControlBotService, ControlRequest
from job_bot.pipeline import VacancyCard
from job_bot.scheduler import DigestItem, Scheduler
from job_bot.telegram_adapters import TelethonUserAdapter


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def vacancy_keyboard(vacancy_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
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
        self, token: str, admin_user_id: int, service: ControlBotService
    ) -> None:
        self._bot = Bot(token=token)
        self._dispatcher = Dispatcher()
        self._admin_user_id = admin_user_id
        self._service = service
        self._task: asyncio.Task[None] | None = None
        self.polling = False
        self._dispatcher.message.register(self._on_message)
        self._dispatcher.callback_query.register(self._on_callback)

    async def _on_message(self, message: Message) -> None:
        if message.from_user is None:
            return
        response = await self._service.dispatch(
            ControlRequest(user_id=message.from_user.id, text=message.text)
        )
        if response.alert:
            await message.answer(response.alert)
        elif response.text:
            await message.answer(response.text)

    async def _on_callback(self, query: CallbackQuery) -> None:
        if query.from_user is None:
            return
        response = await self._service.dispatch(
            ControlRequest(user_id=query.from_user.id, callback_data=query.data)
        )
        await query.answer(response.alert or "", show_alert=bool(response.alert))
        if response.text and query.message is not None:
            await query.message.answer(response.text)

    async def send_card(self, card: VacancyCard) -> None:
        await self._bot.send_message(
            self._admin_user_id,
            format_card(card),
            reply_markup=vacancy_keyboard(card.vacancy_id),
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
                source_url=None,
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
