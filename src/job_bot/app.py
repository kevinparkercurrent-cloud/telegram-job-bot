from __future__ import annotations

import asyncio
import signal
from typing import Protocol

import httpx
from openai import AsyncOpenAI

from job_bot.approvals import ApprovalService
from job_bot.channel_management import ChannelManagementService
from job_bot.collector import Collector
from job_bot.config import Settings, load_candidate_profile
from job_bot.control_bot import ControlBotService, RuntimeControlActions
from job_bot.db import Database
from job_bot.drafting import OpenAIDrafter, OpenAIResponsesClient, TemplateDrafter
from job_bot.exchange_rates import CbrExchangeRates
from job_bot.observability import configure_logging
from job_bot.pipeline import VacancyPipeline
from job_bot.runtime import (
    AiogramControlRuntime,
    AsyncCloser,
    CollectorIngress,
    PeriodicScheduler,
    SystemClock,
)
from job_bot.scheduler import Scheduler
from job_bot.scoring import ScoringPolicy
from job_bot.sender import SafeSender
from job_bot.sources import SourceFetcher, SourcePolicy
from job_bot.telegram_adapters import TelethonUserAdapter


class Runtime(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...


class ClosableDatabase(Protocol):
    async def close(self) -> None: ...


class JobBotApplication:
    """Owns process lifecycle and stops ingress before persistent resources."""

    def __init__(
        self,
        *,
        collector: Runtime,
        control_bot: Runtime,
        database: ClosableDatabase,
        resources: tuple[Runtime, ...] = (),
    ) -> None:
        self._collector = collector
        self._control_bot = control_bot
        self._database = database
        self._resources = resources
        self._started = False
        self._stopped = False

    async def start(self) -> None:
        if self._started:
            return
        await self._collector.start()
        await self._control_bot.start()
        for resource in self._resources:
            await resource.start()
        self._started = True

    async def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        if self._started:
            await self._collector.stop()
        for resource in reversed(self._resources):
            await resource.stop()
        if self._started:
            await self._control_bot.stop()
        await self._database.close()


async def build_application(settings: Settings) -> JobBotApplication:
    database = await Database.open(settings.database_path)
    profile = load_candidate_profile(settings.candidate_profile_path)
    clock = SystemClock()
    telegram = TelethonUserAdapter(
        settings.telegram_session_path,
        settings.telegram_api_id,
        settings.telegram_api_hash,
        settings.telegram_phone,
    )
    approvals = ApprovalService(database, clock)
    sender = SafeSender(
        database,
        approvals,
        telegram,
        clock,
        timezone_name=settings.app_timezone,
        hourly_limit=settings.hourly_send_limit,
        daily_limit=settings.daily_send_limit,
    )
    actions = RuntimeControlActions(database, approvals, sender)
    channel_manager = ChannelManagementService(database, telegram)
    control_service = ControlBotService(
        database,
        settings.admin_telegram_id,
        actions,
        channel_manager=channel_manager,
    )
    control_bot = AiogramControlRuntime(
        settings.control_bot_token, settings.admin_telegram_id, control_service
    )

    rates_client = httpx.AsyncClient()
    rates = CbrExchangeRates(rates_client, database)
    source_client = httpx.AsyncClient()
    sources = SourceFetcher(
        source_client,
        SourcePolicy(allowed_domains=frozenset(settings.external_source_domains)),
    )
    ai_client = (
        AsyncOpenAI(api_key=settings.openai_api_key)
        if settings.openai_api_key
        else None
    )
    ai_drafter = (
        OpenAIDrafter(OpenAIResponsesClient(ai_client), settings.openai_model)
        if ai_client is not None
        else None
    )
    pipeline = VacancyPipeline(
        database,
        rates,
        profile,
        ScoringPolicy(
            salary_floor_rub=100_000,
            strong_threshold=settings.strong_threshold,
            borderline_threshold=settings.borderline_threshold,
        ),
        sources,
        TemplateDrafter(),
        ai_drafter,
        control_bot,
    )
    collector = CollectorIngress(telegram, Collector(database, pipeline))
    scheduler = PeriodicScheduler(
        Scheduler(database, control_bot, settings.app_timezone, settings.digest_times),
        rates.refresh,
    )
    resources = [AsyncCloser(source_client.aclose), AsyncCloser(rates.close)]
    if ai_client is not None:
        resources.append(AsyncCloser(ai_client.close))
    resources.append(scheduler)
    return JobBotApplication(
        collector=collector,
        control_bot=control_bot,
        database=database,
        resources=tuple(resources),
    )


async def main() -> None:
    configure_logging()
    settings = Settings.load()
    application = await build_application(settings)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for stop_signal in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(stop_signal, stop_event.set)
        except (NotImplementedError, RuntimeError):
            pass
    await application.start()
    try:
        await stop_event.wait()
    finally:
        await application.stop()


if __name__ == "__main__":
    asyncio.run(main())
