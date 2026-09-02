from __future__ import annotations

import asyncio
import json
from pathlib import Path

from telethon import TelegramClient, utils
from telethon.errors import FloodWaitError, UserAlreadyParticipantError
from telethon.tl.functions.channels import JoinChannelRequest

from job_bot.config import Settings
from job_bot.db import Database


CHANNELS_PATH = Path("/app/config/channel-allowlist.json")


async def join_channel(client: TelegramClient, username: str) -> None:
    try:
        await client(JoinChannelRequest(username))
    except UserAlreadyParticipantError:
        return
    except FloodWaitError as error:
        wait_seconds = min(int(error.seconds) + 1, 120)
        print(f"RATE_LIMIT @{username}: waiting {wait_seconds}s", flush=True)
        await asyncio.sleep(wait_seconds)
        await client(JoinChannelRequest(username))


async def main() -> None:
    settings = Settings.load()
    channels = json.loads(CHANNELS_PATH.read_text(encoding="utf-8"))
    client = TelegramClient(
        str(settings.telegram_session_path),
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )
    database = await Database.open(settings.database_path)
    await client.connect()
    added = 0
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized")
        for item in channels:
            username = str(item["username"])
            label = str(item["label"])
            try:
                await join_channel(client, username)
                entity = await client.get_entity(username)
                channel_id = int(utils.get_peer_id(entity))
                if channel_id >= 0:
                    raise ValueError("resolved entity is not a channel")
                await database.add_channel(channel_id, label)
                added += 1
                print(f"ADDED @{username}", flush=True)
            except Exception as error:
                print(f"SKIPPED @{username}: {type(error).__name__}", flush=True)
            await asyncio.sleep(4)
        print(f"CHANNEL_IMPORT_OK={added}", flush=True)
    finally:
        await client.disconnect()
        await database.close()


if __name__ == "__main__":
    asyncio.run(main())
