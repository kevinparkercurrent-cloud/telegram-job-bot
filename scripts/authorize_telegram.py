from __future__ import annotations

import asyncio
import getpass
import os

import qrcode
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError


async def authorize() -> None:
    client = TelegramClient(
        os.environ["TELEGRAM_SESSION_PATH"],
        int(os.environ["TELEGRAM_API_ID"]),
        os.environ["TELEGRAM_API_HASH"],
    )
    await client.connect()
    try:
        if await client.is_user_authorized():
            print("AUTHORIZATION_OK", flush=True)
            return

        login = await client.qr_login()
        qr = qrcode.QRCode(border=2)
        qr.add_data(login.url)
        qr.make(fit=True)
        print("Scan this QR code from Telegram: Settings > Devices > Link Desktop Device")
        qr.print_ascii(tty=True, invert=True)
        try:
            await login.wait(timeout=120)
        except SessionPasswordNeededError:
            password = getpass.getpass("Enter the Telegram 2FA password: ")
            await client.sign_in(password=password)
        print("AUTHORIZATION_OK", flush=True)
    finally:
        await client.disconnect()


def main() -> None:
    asyncio.run(authorize())


if __name__ == "__main__":
    main()
