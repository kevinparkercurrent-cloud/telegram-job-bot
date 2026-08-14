from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from job_bot.approvals import ApprovalInvalid, ApprovalService
from job_bot.db import Database
from job_bot.domain import Draft
from job_bot.sender import SafeSender


@dataclass(frozen=True)
class ControlRequest:
    user_id: int
    text: str | None = None
    callback_data: str | None = None


@dataclass(frozen=True)
class ControlResponse:
    text: str = ""
    alert: str | None = None
    mutated: bool = False


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
    ) -> None:
        self._database = database
        self._admin_user_id = admin_user_id
        self._actions = actions

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
        if self._actions is None:
            return ControlResponse(text="Действие пока недоступно")
        action, separator, vacancy_id = data.partition(":")
        if not separator or not vacancy_id:
            return ControlResponse(text="Некорректное действие")
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
        parts = text.split(maxsplit=3)
        if len(parts) == 1:
            channels = await self._database.list_channels()
            body = "\n".join(
                f"{channel_id} — {label}" for channel_id, label in channels
            )
            return ControlResponse(text=body or "Белый список пуст")
        if len(parts) >= 3 and parts[1] == "add":
            try:
                channel_id = int(parts[2])
            except ValueError:
                return ControlResponse(text="ID канала должен быть числом")
            if channel_id >= 0 or len(parts) != 4 or not parts[3].strip():
                return ControlResponse(
                    text="Формат: /channels add <negative_id> <label>"
                )
            label = parts[3].strip()
            try:
                await self._database.add_channel(channel_id, label)
            except ValueError:
                return ControlResponse(text="Можно добавить не более 20 каналов")
            return ControlResponse(text=f"Канал {label} добавлен", mutated=True)
        if len(parts) == 3 and parts[1] == "remove":
            try:
                channel_id = int(parts[2])
            except ValueError:
                return ControlResponse(text="ID канала должен быть числом")
            await self._database.remove_channel(channel_id)
            return ControlResponse(text="Канал удалён", mutated=True)
        return ControlResponse(text="Формат: /channels [add|remove]")

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
