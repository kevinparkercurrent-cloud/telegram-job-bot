from job_bot import telegram_adapters
from datetime import datetime, timezone
from types import SimpleNamespace


def test_public_post_url_uses_channel_username() -> None:
    assert telegram_adapters.telegram_post_url(-1001234567890, 77, "jobs_feed") == (
        "https://t.me/jobs_feed/77"
    )


def test_private_post_url_uses_internal_channel_id() -> None:
    assert telegram_adapters.telegram_post_url(-1001234567890, 77, None) == (
        "https://t.me/c/1234567890/77"
    )


def test_basic_group_without_username_has_no_post_url() -> None:
    assert telegram_adapters.telegram_post_url(-12345, 77, None) is None


def test_event_mapping_preserves_original_post_url() -> None:
    event = SimpleNamespace(
        chat_id=-1001234567890,
        message=SimpleNamespace(
            id=77,
            date=datetime(2026, 8, 16, tzinfo=timezone.utc),
            raw_text="Project Manager",
        ),
    )

    post = telegram_adapters.telethon_event_to_post(event, username="jobs_feed")

    assert post.source_post_url == "https://t.me/jobs_feed/77"
