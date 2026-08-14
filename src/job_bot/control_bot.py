from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from job_bot.db import Database


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


class ControlBotService:
    def __init__(self, database: Database, admin_user_id: int) -> None:
        self._database = database
        self._admin_user_id = admin_user_id

    async def dispatch(self, request: ControlRequest) -> ControlResponse:
        if request.user_id != self._admin_user_id:
            return ControlResponse(alert="Доступ запрещён")
        if request.callback_data:
            return ControlResponse(text="Действие пока недоступно")
        text = (request.text or "").strip()
        if text.startswith("/channels"):
            return await self._channels(text)
        if text.startswith("/settings"):
            return await self._settings(text)
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
            return ControlResponse(text=f"База доступна. Пограничных вакансий: {pending}")
        return ControlResponse(
            text="Команды: /channels, /settings, /queue, /history, /status"
        )

    async def _channels(self, text: str) -> ControlResponse:
        parts = text.split(maxsplit=3)
        if len(parts) == 1:
            channels = await self._database.list_channels()
            body = "\n".join(f"{channel_id} — {label}" for channel_id, label in channels)
            return ControlResponse(text=body or "Белый список пуст")
        if len(parts) >= 3 and parts[1] == "add":
            try:
                channel_id = int(parts[2])
            except ValueError:
                return ControlResponse(text="ID канала должен быть числом")
            if channel_id >= 0 or len(parts) != 4 or not parts[3].strip():
                return ControlResponse(text="Формат: /channels add <negative_id> <label>")
            label = parts[3].strip()
            await self._database.add_channel(channel_id, label)
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
            return ControlResponse(text=f"Часовой пояс: {timezone}; дайджесты: {digests}")
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
        return ControlResponse(text="Формат: /settings timezone <IANA> или /settings digest <HH:MM> <HH:MM>")

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
        return title + ":\n" + "\n".join(
            f"{row.vacancy.id} — {row.vacancy.title or 'без названия'} [{row.status}]"
            for row in rows
        )
