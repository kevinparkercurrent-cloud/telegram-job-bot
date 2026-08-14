import pytest

from job_bot.app import JobBotApplication, build_application
from job_bot.config import Settings


class RecordingRuntime:
    def __init__(self, name: str, events: list[str]) -> None:
        self.name = name
        self.events = events

    async def start(self) -> None:
        self.events.append(f"{self.name}.start")

    async def stop(self) -> None:
        self.events.append(f"{self.name}.stop")


class RecordingDatabase:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def close(self) -> None:
        self.events.append("db.close")


@pytest.mark.asyncio
async def test_shutdown_stops_ingress_before_closing_database() -> None:
    events: list[str] = []
    application = JobBotApplication(
        collector=RecordingRuntime("collector", events),
        control_bot=RecordingRuntime("bot", events),
        database=RecordingDatabase(events),
    )

    await application.start()
    await application.stop()

    assert events == [
        "collector.start",
        "bot.start",
        "collector.stop",
        "bot.stop",
        "db.close",
    ]


@pytest.mark.asyncio
async def test_shutdown_is_idempotent() -> None:
    events: list[str] = []
    application = JobBotApplication(
        collector=RecordingRuntime("collector", events),
        control_bot=RecordingRuntime("bot", events),
        database=RecordingDatabase(events),
    )

    await application.start()
    await application.stop()
    await application.stop()

    assert events.count("db.close") == 1


@pytest.mark.asyncio
async def test_application_factory_wires_without_network(
    tmp_path, profile_path
) -> None:
    settings = Settings(
        _env_file=None,
        telegram_api_id=12345,
        telegram_api_hash="hash",
        control_bot_token="123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
        admin_telegram_id=42,
        telegram_session_path=tmp_path / "telegram.session",
        database_path=tmp_path / "app.sqlite3",
        candidate_profile_path=profile_path,
    )

    application = await build_application(settings)
    await application.stop()
