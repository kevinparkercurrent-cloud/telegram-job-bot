#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from job_bot.smoke import inspect_database, inspect_resume_pdf


async def main() -> int:
    result = await inspect_database(
        Path(os.environ.get("DATABASE_PATH", "/data/job-bot.sqlite3"))
    )
    required = (
        "TELEGRAM_API_ID",
        "TELEGRAM_API_HASH",
        "CONTROL_BOT_TOKEN",
        "ADMIN_TELEGRAM_ID",
    )
    result["required_environment_present"] = {
        name: bool(os.environ.get(name)) for name in required
    }
    resume_path = Path(os.environ.get("RESUME_PDF_PATH", "/data/resume.pdf"))
    result["resume_pdf_ok"] = inspect_resume_pdf(resume_path)
    backup_dir = Path(os.environ.get("BACKUP_DIR", "/backups"))
    backups = (
        sorted(backup_dir.glob("job-bot-*.sqlite3.age")) if backup_dir.is_dir() else []
    )
    result["last_backup_file"] = backups[-1].name if backups else None
    print(json.dumps(result, ensure_ascii=False))
    environment_ok = all(result["required_environment_present"].values())
    return 0 if result["database_ok"] and environment_ok and result["resume_pdf_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
