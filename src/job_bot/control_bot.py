from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from job_bot.approvals import ApprovalInvalid, ApprovalService
from job_bot.channel_management import (
    ChannelManagementError,
    ChannelManagementService,
)
from job_bot.db import CHANNEL_LIMIT, Database, StoredChannel
from job_bot.domain import Draft
from job_bot.pipeline import VacancyCard
from job_bot.sender import SafeSender


@dataclass(frozen=True)
class ControlRequest:
    user_id: int
    text: str | None = None
    callback_data: str | None = None


@dataclass(frozen=True)
class ChannelMenu:
    items: tuple[StoredChannel, ...]
    page: int
    pages: int
    total: int
    mode: Literal["list", "remove"] = "list"


@dataclass(frozen=True)
class RemovalConfirmation:
    channel: StoredChannel
    page: int


@dataclass(frozen=True)
class ControlResponse:
    text: str = ""
    alert: str | None = None
    mutated: bool = False
    begin_edit_vacancy_id: str | None = None
    card: VacancyCard | None = None
    channel_menu: ChannelMenu | None = None
    begin_channel_add: bool = False
    remove_confirmation: RemovalConfirmation | None = None


class ControlActions(Protocol):
    async def approve(self, vacancy_id: str) -> str: ...
    async def edit(self, vacancy_id: str, text: str) -> str: ...
    async def decide(self, vacancy_id: str, decision: str) -> str: ...


class RuntimeControlActions:
    def __init__(
        self, database: Database, approvals: ApprovalService, sender: SafeSender
    ) -> None:
        self._database = database
        self._approvals = approvals
        self._sender = sender

    async def approve(self, vacancy_id: str) -> str:
        stored = await self._database.get_vacancy(vacancy_id)
        if stored is None:
            return "Вакансия не найдена"
        recipient = stored.vacancy.recruiter_username
        if not recipient:
            return "В вакансии нет Telegram-контакта рекрутера"
        try:
            approval = await self._approvals.issue(vacancy_id, recipient)
        except ApprovalInvalid as error:
            return f"Нельзя подтвердить отклик: {error.code}"
        result = await self._sender.send(approval.id)
        labels = {
            "sent": "Отклик отправлен",
            "rate_limited": "Лимит отправок исчерпан; отклик не отправлен",
            "invalid_recipient": "Контакт рекрутера не является личным Telegram-аккаунтом",
            "unknown": "Статус отправки неизвестен; автоматического повтора не будет",
        }
        return labels.get(result.status, f"Отклик не отправлен: {result.status}")

    async def edit(self, vacancy_id: str, text: str) -> str:
        await self._approvals.replace_draft(
            vacancy_id,
            Draft(text=text, origin="user", evidence_ids=[]),
        )
        return "Черновик обновлён; подтвердите отклик заново"

    async def decide(self, vacancy_id: str, decision: str) -> str:
        await self._database.record_decision(vacancy_id, decision)
        return "Решение сохранено"


class ControlBotService:
    def __init__(
        self,
        database: Database,
        admin_user_id: int,
        actions: ControlActions | None = None,
        channel_manager: ChannelManagementService | None = None,
    ) -> None:
        self._database = database
        self._admin_user_id = admin_user_id
        self._actions = actions
        self._channel_manager = channel_manager

    async def replace_draft(
        self, vacancy_id: str, text: str
    ) -> ControlResponse:
        if self._actions is None:
            return ControlResponse(text="Редактирование пока недоступно")
        if len(text) > 3500:
            return ControlResponse(
                text="Текст отклика не должен превышать 3500 символов"
            )
        result = await self._actions.edit(vacancy_id, text)
        return ControlResponse(
            text=result,
            card=await self._vacancy_card(vacancy_id),
            mutated=True,
        )

    async def _vacancy_card(self, vacancy_id: str) -> VacancyCard | None:
        stored = await self._database.get_vacancy(vacancy_id)
        assessment = await self._database.get_assessment(vacancy_id)
        active = await self._database.get_active_draft(vacancy_id)
        if stored is None or assessment is None or active is None:
            return None
        _, draft, _ = active
        vacancy = stored.vacancy
        return VacancyCard(
            vacancy_id=vacancy.id,
            title=vacancy.title or "Вакансия без указанного названия",
            score=assessment.score,
            match_class=assessment.match_class.value,
            reasons=assessment.reasons,
            warnings=assessment.warnings,
            recruiter_username=vacancy.recruiter_username,
            source_post_url=(
                str(vacancy.source_post_url)
                if vacancy.source_post_url
                else None
            ),
            draft_text=draft.text,
            draft_origin=draft.origin,
        )

    async def dispatch(self, request: ControlRequest) -> ControlResponse:
        if request.user_id != self._admin_user_id:
            return ControlResponse(alert="Доступ запрещён")
        if request.callback_data:
            return await self._callback(request.callback_data)
        text = (request.text or "").strip()
        if text.startswith("/channels"):
            return await self._channels(text)
        if text.startswith("/settings"):
            return await self._settings(text)
        if text.startswith("/edit "):
            return await self._edit(text)
        if text == "/queue":
            rows = await self._database.list_by_statuses(("queued",))
            return ControlResponse(text=self._format_vacancies("Очередь", rows))
        if text == "/history":
            rows = await self._database.list_by_statuses(
                ("sent", "skipped", "not_relevant", "failed", "assessed")
            )
            return ControlResponse(text=self._format_vacancies("История", rows))
        if text == "/status":
            pending = await self._database.count_digest_pending()
            return ControlResponse(
                text=f"База доступна. Пограничных вакансий: {pending}"
            )
        return ControlResponse(
            text="Команды: /channels, /settings, /queue, /history, /status, /edit"
        )

    async def _callback(self, data: str) -> ControlResponse:
        if data.startswith("channels:"):
            return await self._channel_callback(data)
        if self._actions is None:
            return ControlResponse(text="Действие пока недоступно")
        action, separator, vacancy_id = data.partition(":")
        if not separator or not vacancy_id:
            return ControlResponse(text="Некорректное действие")
        if action == "edit_prompt":
            active = await self._database.get_active_draft(vacancy_id)
            if active is None:
                return ControlResponse(text="Активный черновик не найден")
            _, draft, _ = active
            return ControlResponse(
                text=(
                    f"Текущий черновик:\n\n{draft.text}\n\n"
                    "Отправьте новый текст одним сообщением или /cancel"
                ),
                begin_edit_vacancy_id=vacancy_id,
            )
        if action == "approve":
            result = await self._actions.approve(vacancy_id)
        elif action in {"skip", "not_relevant"}:
            result = await self._actions.decide(vacancy_id, action)
        else:
            return ControlResponse(text="Неизвестное действие")
        return ControlResponse(text=result, mutated=True)

    async def _edit(self, text: str) -> ControlResponse:
        if self._actions is None:
            return ControlResponse(text="Редактирование пока недоступно")
        parts = text.split(maxsplit=2)
        if len(parts) != 3 or not parts[2].strip():
            return ControlResponse(text="Формат: /edit <vacancy_id> <новый текст>")
        result = await self._actions.edit(parts[1], parts[2].strip())
        return ControlResponse(text=result, mutated=True)

    async def _channels(self, text: str) -> ControlResponse:
        parts = text.split(maxsplit=2)
        if len(parts) == 1:
            return await self._channel_menu(0)
        if len(parts) == 3 and parts[1] == "add":
            return await self.add_channel(parts[2])
        if len(parts) == 3 and parts[1] == "remove":
            try:
                channel_id = int(parts[2])
            except ValueError:
                return ControlResponse(text="ID канала должен быть числом")
            return await self._remove_prompt(channel_id, 0)
        return ControlResponse(
            text="Формат: /channels add <ссылка> или /channels remove <ID>"
        )

    async def add_channel(self, reference: str) -> ControlResponse:
        if self._channel_manager is None:
            return ControlResponse(text="Добавление каналов пока недоступно")
        try:
            channel = await self._channel_manager.add(reference.strip())
        except ChannelManagementError as error:
            return ControlResponse(text=self._channel_error(error.code))
        menu = await self._channel_menu(0)
        return ControlResponse(
            text=f"Канал «{channel.label}» добавлен",
            mutated=True,
            channel_menu=menu.channel_menu,
        )

    async def _channel_callback(self, data: str) -> ControlResponse:
        parts = data.split(":")
        if parts == ["channels", "add"]:
            return ControlResponse(
                text=(
                    "Отправьте @username, публичную ссылку или приватную "
                    "пригласительную ссылку. Для отмены: /cancel"
                ),
                begin_channel_add=True,
            )
        try:
            action = parts[1]
            if action in {"list", "remove", "cancel"} and len(parts) == 3:
                page = int(parts[2])
                return await self._channel_menu(
                    page, mode="remove" if action == "remove" else "list"
                )
            if action == "pick" and len(parts) == 4:
                return await self._remove_prompt(int(parts[2]), int(parts[3]))
            if action == "confirm" and len(parts) == 4:
                return await self._confirm_remove(int(parts[2]), int(parts[3]))
        except (ValueError, IndexError):
            pass
        return ControlResponse(text="Некорректное действие с каналом")

    async def _channel_menu(
        self, page: int, *, mode: Literal["list", "remove"] = "list"
    ) -> ControlResponse:
        channels = await self._database.list_channels()
        total = len(channels)
        pages = max(1, (total + 9) // 10)
        page = min(max(page, 0), pages - 1)
        start = page * 10
        menu = ChannelMenu(
            items=tuple(channels[start : start + 10]),
            page=page,
            pages=pages,
            total=total,
            mode=mode,
        )
        text = (
            f"Каналы: {total}/{CHANNEL_LIMIT}"
            if mode == "list"
            else "Выберите канал для удаления"
        )
        return ControlResponse(text=text, channel_menu=menu)

    async def _remove_prompt(
        self, channel_id: int, page: int
    ) -> ControlResponse:
        channel = await self._database.get_channel(channel_id)
        if channel is None:
            return ControlResponse(text="Канал не найден")
        return ControlResponse(
            text=(
                f"Удалить канал «{channel.label}»? Отдельный аккаунт выйдет "
                "из него. Для повторного входа в приватный канал может "
                "понадобиться новая ссылка."
            ),
            remove_confirmation=RemovalConfirmation(channel=channel, page=page),
        )

    async def _confirm_remove(
        self, channel_id: int, page: int
    ) -> ControlResponse:
        if self._channel_manager is None:
            return ControlResponse(text="Удаление каналов пока недоступно")
        try:
            result = await self._channel_manager.remove(channel_id)
        except ChannelManagementError as error:
            return ControlResponse(text=self._channel_error(error.code))
        menu = await self._channel_menu(page)
        suffix = "" if result.left else "; мониторинг остановлен, но аккаунт не вышел"
        return ControlResponse(
            text=f"Канал «{result.channel.label}» удалён{suffix}",
            mutated=True,
            channel_menu=menu.channel_menu,
        )

    @staticmethod
    def _channel_error(code: str) -> str:
        messages = {
            "invalid_reference": "Не удалось распознать ссылку на канал",
            "invite_expired": "Пригласительная ссылка недействительна или истекла",
            "not_broadcast": "Это не Telegram-канал",
            "rate_limited": "Telegram временно ограничил добавление каналов; попробуйте позже",
            "telegram_unavailable": "Telegram не разрешил выполнить действие с каналом",
            "capacity_reached": f"Можно добавить не более {CHANNEL_LIMIT} каналов",
            "persistence_failed": "Не удалось сохранить изменение каналов",
            "not_found": "Канал не найден",
        }
        return messages.get(code, "Не удалось выполнить действие с каналом")

    async def _settings(self, text: str) -> ControlResponse:
        parts = text.split()
        if len(parts) == 1:
            timezone = await self._database.get_setting("timezone") or "Europe/Moscow"
            digests = await self._database.get_setting("digest_times") or "12:00,19:00"
            return ControlResponse(
                text=f"Часовой пояс: {timezone}; дайджесты: {digests}"
            )
        if len(parts) == 3 and parts[1] == "timezone":
            try:
                ZoneInfo(parts[2])
            except ZoneInfoNotFoundError:
                return ControlResponse(text="Неизвестный часовой пояс")
            await self._database.set_setting("timezone", parts[2])
            return ControlResponse(text="Часовой пояс обновлён", mutated=True)
        if len(parts) == 4 and parts[1] == "digest":
            if not all(self._valid_time(value) for value in parts[2:]):
                return ControlResponse(text="Время должно быть в формате HH:MM")
            await self._database.set_setting("digest_times", f"{parts[2]},{parts[3]}")
            return ControlResponse(text="Расписание обновлено", mutated=True)
        return ControlResponse(
            text="Формат: /settings timezone <IANA> или /settings digest <HH:MM> <HH:MM>"
        )

    @staticmethod
    def _valid_time(value: str) -> bool:
        try:
            hour, minute = (int(part) for part in value.split(":"))
        except (ValueError, TypeError):
            return False
        return 0 <= hour <= 23 and 0 <= minute <= 59 and len(value) == 5

    @staticmethod
    def _format_vacancies(title: str, rows) -> str:
        if not rows:
            return f"{title}: пусто"
        return (
            title
            + ":\n"
            + "\n".join(
                f"{row.vacancy.id} — {row.vacancy.title or 'без названия'} [{row.status}]"
                for row in rows
            )
        )
