from __future__ import annotations

from pathlib import Path

import aiosqlite


async def inspect_database(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {"database_ok": False, "schema_version": None}
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    try:
        async with aiosqlite.connect(uri, uri=True) as connection:
            cursor = await connection.execute("SELECT MAX(version) FROM schema_version")
            row = await cursor.fetchone()
            channel_row = await (
                await connection.execute("SELECT COUNT(*) FROM channels")
            ).fetchone()
            queue_row = await (
                await connection.execute(
                    "SELECT COUNT(*) FROM vacancies WHERE status = 'queued'"
                )
            ).fetchone()
            collection_row = await (
                await connection.execute("SELECT MAX(created_at) FROM vacancies")
            ).fetchone()
            digest_row = await (
                await connection.execute(
                    "SELECT value FROM settings WHERE key = 'last_digest_slot'"
                )
            ).fetchone()
    except (aiosqlite.Error, OSError):
        return {"database_ok": False, "schema_version": None}
    version = int(row[0]) if row and row[0] is not None else None
    return {
        "database_ok": version is not None,
        "schema_version": version,
        "channel_count": int(channel_row[0]),
        "queue_count": int(queue_row[0]),
        "last_collection_at": collection_row[0],
        "last_digest_slot": digest_row[0] if digest_row else None,
    }
