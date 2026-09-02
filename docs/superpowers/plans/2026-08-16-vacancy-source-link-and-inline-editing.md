# Vacancy Source Link and Inline Editing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an original Telegram-post link to every available vacancy card and let the owner edit a draft by pressing a button and sending the replacement text as the next message.

**Architecture:** The Telethon ingress creates a canonical source-post URL and persists it with the vacancy. Card construction is centralized so immediate cards, digest cards, and post-edit cards carry the same data. Aiogram keeps a short-lived in-memory pending-edit session and delegates durable draft replacement to the existing approval service.

**Tech Stack:** Python 3.12, Telethon, aiogram 3, Pydantic 2, aiosqlite, pytest, pytest-asyncio, Docker Compose.

## Global Constraints

- `Открыть вакансию` must point to the original Telegram post, not an external job page.
- Public links use `https://t.me/<username>/<message_id>`.
- Private supergroup/channel links use `https://t.me/c/<internal_channel_id>/<message_id>` and may require membership.
- If no safe link can be formed, omit the URL button and keep the rest of the card functional.
- Inline editing is owner-only, accepts one text message, supports `/cancel`, and expires after 15 minutes.
- Commands and non-text messages must never overwrite a draft.
- Replacement drafts are limited to 3500 characters.
- Replacing a draft invalidates all previous unconsumed approvals for that vacancy.
- Existing `/edit <vacancy_id> <text>` remains supported.
- Sending limits remain 5 per hour and 15 per day.
- The feature must work for immediate strong-match cards and borderline digest cards.

---

### Task 1: Capture and Persist the Original Telegram Post URL

**Files:**
- Modify: `src/job_bot/collector.py`
- Modify: `src/job_bot/telegram_adapters.py`
- Modify: `src/job_bot/parsing.py`
- Modify: `src/job_bot/domain.py`
- Modify: `src/job_bot/db.py`
- Modify: `tests/test_collector.py`
- Modify: `tests/test_parsing.py`
- Modify: `tests/test_db.py`
- Create: `tests/test_telegram_adapters.py`

**Interfaces:**
- Produces: `telegram_post_url(chat_id: int, message_id: int, username: str | None) -> str | None`.
- Produces: `ChannelPost.source_post_url: str | None`.
- Produces: `Vacancy.source_post_url: HttpUrl | None`.
- Changes: `parse_vacancy(..., source_post_url: str | None, rates: ExchangeRates) -> Vacancy`.
- Persists: nullable `vacancies.source_post_url` with an idempotent migration for existing SQLite databases.

- [ ] **Step 1: Write failing URL-construction tests**

```python
from job_bot.telegram_adapters import telegram_post_url


def test_public_post_url_uses_channel_username() -> None:
    assert telegram_post_url(-1001234567890, 77, "jobs_feed") == (
        "https://t.me/jobs_feed/77"
    )


def test_private_post_url_uses_internal_channel_id() -> None:
    assert telegram_post_url(-1001234567890, 77, None) == (
        "https://t.me/c/1234567890/77"
    )


def test_basic_group_without_username_has_no_post_url() -> None:
    assert telegram_post_url(-12345, 77, None) is None
```

- [ ] **Step 2: Run the URL tests and verify they fail**

Run: `uv run pytest tests/test_telegram_adapters.py -q`

Expected: FAIL because `telegram_post_url` does not exist.

- [ ] **Step 3: Implement minimal link construction and ingress propagation**

```python
def telegram_post_url(
    chat_id: int, message_id: int, username: str | None
) -> str | None:
    if username:
        return f"https://t.me/{username.lstrip('@')}/{message_id}"
    value = str(chat_id)
    if value.startswith("-100") and len(value) > 4:
        return f"https://t.me/c/{value[4:]}/{message_id}"
    return None
```

Add `source_post_url: str | None = None` to `ChannelPost`. Change
`telethon_event_to_post` to accept the resolved chat username and set the URL.
In `TelethonUserAdapter.receive`, resolve `chat = await event.get_chat()` and pass
`getattr(chat, "username", None)`.

- [ ] **Step 4: Write failing parser and persistence tests**

Add to `tests/test_parsing.py`:

```python
vacancy = await parse_vacancy(
    -1009,
    7,
    published_at,
    "Project Manager, удалённо",
    "https://t.me/jobs_feed/7",
    rates,
)
assert str(vacancy.source_post_url) == "https://t.me/jobs_feed/7"
```

Add to `tests/test_db.py`:

```python
vacancy.source_post_url = "https://t.me/jobs_feed/7"
assert await db.insert_vacancy(vacancy)
stored = await db.get_vacancy(vacancy.id)
assert stored is not None
assert str(stored.vacancy.source_post_url) == "https://t.me/jobs_feed/7"
```

Create a version-1 SQLite database without `source_post_url`, open it through
`Database.open`, then assert `PRAGMA table_info(vacancies)` contains the new
column. This proves the migration is safe for the deployed database.

- [ ] **Step 5: Run parser/database tests and verify they fail**

Run: `uv run pytest tests/test_parsing.py tests/test_db.py -q`

Expected: FAIL because the model, parser signature, and database column have not
been updated.

- [ ] **Step 6: Implement the domain field and idempotent database migration**

Add to `Vacancy`:

```python
source_post_url: HttpUrl | None = None
```

Add `source_post_url TEXT` to the current `CREATE TABLE` statement. After the
schema script, inspect `PRAGMA table_info(vacancies)` and run this only when the
column is absent:

```python
await connection.execute(
    "ALTER TABLE vacancies ADD COLUMN source_post_url TEXT"
)
```

Exclude `source_post_url` from `payload_json`, insert it into the dedicated
column, and merge the column back into the payload in `get_vacancy`,
`list_by_statuses`, and `list_digest_pending`.

- [ ] **Step 7: Run focused tests**

Run: `uv run pytest tests/test_telegram_adapters.py tests/test_collector.py tests/test_parsing.py tests/test_db.py -q`

Expected: PASS.

- [ ] **Step 8: Commit Task 1**

```bash
git add src/job_bot/collector.py src/job_bot/telegram_adapters.py \
  src/job_bot/parsing.py src/job_bot/domain.py src/job_bot/db.py \
  tests/test_telegram_adapters.py tests/test_collector.py \
  tests/test_parsing.py tests/test_db.py
git commit -m "feat: persist original Telegram vacancy links"
```

---

### Task 2: Show the Source Link on Immediate and Digest Cards

**Files:**
- Modify: `src/job_bot/pipeline.py`
- Modify: `src/job_bot/scheduler.py`
- Modify: `src/job_bot/runtime.py`
- Modify: `tests/test_pipeline.py`
- Modify: `tests/test_scheduler.py`
- Create: `tests/test_runtime.py`

**Interfaces:**
- Consumes: `Vacancy.source_post_url` from Task 1.
- Changes: `VacancyCard.source_post_url: str | None` replaces the ambiguous card field `source_url`.
- Changes: `DigestItem.source_post_url: str | None`.
- Changes: `vacancy_keyboard(vacancy_id: str, source_post_url: str | None) -> InlineKeyboardMarkup`.

- [ ] **Step 1: Write failing pipeline and digest propagation tests**

In `tests/test_pipeline.py`, create a `ChannelPost` with
`source_post_url="https://t.me/jobs_feed/7"`, process a strong vacancy, and assert:

```python
assert notifier.cards[0].source_post_url == "https://t.me/jobs_feed/7"
```

In `tests/test_scheduler.py`, persist the same URL and assert:

```python
assert notifier.digests[0][0].source_post_url == "https://t.me/jobs_feed/7"
```

- [ ] **Step 2: Run propagation tests and verify they fail**

Run: `uv run pytest tests/test_pipeline.py tests/test_scheduler.py -q`

Expected: FAIL because the card and digest models do not carry the source-post URL.

- [ ] **Step 3: Propagate the URL through pipeline and scheduler**

Rename `VacancyCard.source_url` to `source_post_url`, populate it from
`vacancy.source_post_url`, add the same field to `DigestItem`, and preserve it
when `AiogramControlRuntime.send_digest` rebuilds a `VacancyCard`.

- [ ] **Step 4: Write failing keyboard tests**

```python
def test_keyboard_contains_original_post_and_edit_buttons() -> None:
    keyboard = vacancy_keyboard("v1", "https://t.me/jobs_feed/7")
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    assert any(
        button.text == "Открыть вакансию"
        and button.url == "https://t.me/jobs_feed/7"
        for button in buttons
    )
    assert any(
        button.text == "Редактировать отклик"
        and button.callback_data == "edit_prompt:v1"
        for button in buttons
    )


def test_keyboard_omits_url_button_without_source_link() -> None:
    keyboard = vacancy_keyboard("v1", None)
    assert all(
        button.url is None
        for row in keyboard.inline_keyboard
        for button in row
    )
```

- [ ] **Step 5: Run keyboard tests and verify they fail**

Run: `uv run pytest tests/test_runtime.py -q`

Expected: FAIL because the keyboard has neither the URL nor edit button.

- [ ] **Step 6: Implement the new keyboard layout**

Construct rows in this order:

```python
rows = []
if source_post_url:
    rows.append([
        InlineKeyboardButton(text="Открыть вакансию", url=source_post_url)
    ])
rows.extend([
    [InlineKeyboardButton(
        text="Редактировать отклик",
        callback_data=f"edit_prompt:{vacancy_id}",
    )],
    [InlineKeyboardButton(
        text="Откликнуться", callback_data=f"approve:{vacancy_id}"
    )],
    [
        InlineKeyboardButton(
            text="Пропустить", callback_data=f"skip:{vacancy_id}"
        ),
        InlineKeyboardButton(
            text="Не подходит", callback_data=f"not_relevant:{vacancy_id}"
        ),
    ],
])
```

Pass `card.source_post_url` from `send_card`.

- [ ] **Step 7: Run focused tests**

Run: `uv run pytest tests/test_pipeline.py tests/test_scheduler.py tests/test_runtime.py -q`

Expected: PASS.

- [ ] **Step 8: Commit Task 2**

```bash
git add src/job_bot/pipeline.py src/job_bot/scheduler.py src/job_bot/runtime.py \
  tests/test_pipeline.py tests/test_scheduler.py tests/test_runtime.py
git commit -m "feat: link vacancy cards to original posts"
```

---

### Task 3: Add Owner-Only Inline Draft Editing

**Files:**
- Modify: `src/job_bot/control_bot.py`
- Modify: `src/job_bot/runtime.py`
- Modify: `tests/test_control_bot.py`
- Modify: `tests/test_runtime.py`

**Interfaces:**
- Produces: `ControlResponse.begin_edit_vacancy_id: str | None`.
- Produces: `ControlResponse.card: VacancyCard | None`.
- Produces: `ControlBotService.replace_draft(vacancy_id: str, text: str) -> ControlResponse`.
- Produces: `PendingEdit(vacancy_id: str, expires_at: datetime)` stored by admin user ID.

- [ ] **Step 1: Write failing control-service tests for beginning an edit**

```python
response = await service.dispatch(
    ControlRequest(user_id=ADMIN_ID, callback_data="edit_prompt:v1")
)
assert response.begin_edit_vacancy_id == "v1"
assert "Текущий черновик" in response.text
assert "Здравствуйте" in response.text
```

Also assert a missing vacancy or draft returns a normal error response without
starting an edit, and a non-admin request still returns `Доступ запрещён`.

- [ ] **Step 2: Run control-service tests and verify they fail**

Run: `uv run pytest tests/test_control_bot.py -q`

Expected: FAIL because `edit_prompt` and response metadata do not exist.

- [ ] **Step 3: Implement edit prompt and centralized card reconstruction**

Extend `ControlResponse` with optional `begin_edit_vacancy_id` and `card` fields.
Add a private `_vacancy_card(vacancy_id)` method that loads the stored vacancy,
assessment, and active draft and returns the same `VacancyCard` shape used by the
pipeline. Handle `edit_prompt:<id>` in `_callback` by returning the active draft
and the vacancy ID.

Expose:

```python
async def replace_draft(self, vacancy_id: str, text: str) -> ControlResponse:
    if len(text) > 3500:
        return ControlResponse(text="Текст отклика не должен превышать 3500 символов")
    result = await self._actions.edit(vacancy_id, text)
    card = await self._vacancy_card(vacancy_id)
    return ControlResponse(text=result, card=card, mutated=True)
```

- [ ] **Step 4: Write failing pending-session and runtime tests**

Cover these state transitions in `tests/test_runtime.py` using a fake clock and
fake messages/callbacks:

```python
await runtime._on_callback(edit_callback)
assert runtime.pending_edit_for(ADMIN_ID) == "v1"

await runtime._on_message(text_message("Новый отклик"))
assert service.replacements == [("v1", "Новый отклик")]
assert runtime.pending_edit_for(ADMIN_ID) is None
assert sent_cards[-1].draft_text == "Новый отклик"
```

Add separate tests asserting:

- `/cancel` clears the state without editing;
- `/status` is dispatched as a command and does not overwrite the draft;
- a photo/non-text message leaves the pending edit active;
- a 3501-character message is rejected and leaves the edit active;
- a message after 15 minutes clears the expired state and does not edit;
- a callback from a non-admin user never creates pending state;
- pressing edit for another vacancy replaces the earlier pending vacancy.

- [ ] **Step 5: Run runtime tests and verify they fail**

Run: `uv run pytest tests/test_runtime.py tests/test_control_bot.py -q`

Expected: FAIL because no pending-edit state or replacement-message routing exists.

- [ ] **Step 6: Implement the in-memory edit state and routing**

Add:

```python
@dataclass(frozen=True)
class PendingEdit:
    vacancy_id: str
    expires_at: datetime
```

`AiogramControlRuntime` stores `dict[int, PendingEdit]`, accepts an injectable
`now: Callable[[], datetime]`, and uses `timedelta(minutes=15)`. Callback metadata
starts/replaces the session. `_on_message` applies this precedence:

1. Reject missing `from_user`.
2. `/cancel` clears pending state and responds.
3. Any other `/command` follows the existing command dispatcher without editing.
4. Expired state is cleared and reported.
5. Non-text and empty text are rejected without clearing the active state.
6. Text over 3500 characters is rejected without clearing the active state.
7. Valid text calls `replace_draft`, clears state only after a successful mutation,
   answers with the result, and sends `response.card` through `send_card`.

Expose a small `pending_edit_for(user_id)` read-only helper for deterministic tests.

- [ ] **Step 7: Run focused editing tests**

Run: `uv run pytest tests/test_runtime.py tests/test_control_bot.py tests/test_approvals_sender.py -q`

Expected: PASS, including the existing test proving draft replacement invalidates
an earlier approval.

- [ ] **Step 8: Commit Task 3**

```bash
git add src/job_bot/control_bot.py src/job_bot/runtime.py \
  tests/test_control_bot.py tests/test_runtime.py
git commit -m "feat: edit vacancy replies from inline cards"
```

---

### Task 4: Regression Verification, Documentation, and VPS Deployment

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes all Task 1-3 interfaces.
- Produces a deployed healthy Docker service using the existing persistent SQLite and Telethon volumes.

- [ ] **Step 1: Document the card workflow**

Update `README.md` to state that cards link to the original Telegram post, the
owner can edit via the inline button or `/edit`, `/cancel` cancels the pending
edit, and the pending state expires in 15 minutes.

- [ ] **Step 2: Run the full local verification suite**

Run:

```bash
uv run pytest -q
uv run python -m compileall -q src tests scripts
docker compose config --quiet
```

Expected: all tests pass, compilation produces no output, Compose configuration
is valid. If local Docker is unavailable, run the image build and Compose check
on the VPS before deployment.

- [ ] **Step 3: Commit documentation and push the completed branch**

```bash
git add README.md
git commit -m "docs: explain vacancy card editing workflow"
git push origin codex/telegram-job-bot
```

Do not create an empty documentation commit if the README was already committed
with an earlier task.

- [ ] **Step 4: Build the production image before replacing the service**

On the VPS:

```bash
cd /opt/telegram-job-bot
git fetch origin codex/telegram-job-bot
git merge --ff-only origin/codex/telegram-job-bot
docker compose build job-bot
```

Expected: build succeeds while the current container remains available.

- [ ] **Step 5: Recreate the service and verify the database migration**

Run:

```bash
docker compose up -d job-bot
docker compose exec -T job-bot python /app/scripts/smoke_test.py
docker compose ps
```

Expected: the existing `/data/job-bot.sqlite3` opens successfully, the
`source_post_url` migration is present, and the service becomes `healthy` with
zero unexpected restarts.

- [ ] **Step 6: Perform Telegram acceptance checks**

Wait for or inject a new test vacancy from an allowed public channel, then verify:

1. `Открыть вакансию` opens the exact source message.
2. `Редактировать отклик` shows the current text.
3. A replacement message yields a new card with the updated draft.
4. `/cancel` cancels a second edit attempt.
5. The old card cannot send the pre-edit draft.
