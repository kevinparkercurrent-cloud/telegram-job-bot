# Control Bot Channel Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the owner add and remove up to 100 public or private Telegram broadcast channels from an interactive `/channels` menu.

**Architecture:** Add a focused channel-management service between the control bot and Telethon. The database remains the monitoring source of truth; the aiogram runtime owns only the 15-minute conversational state and renders UI-neutral menu responses returned by the control service.

**Tech Stack:** Python 3.12, aiogram 3, Telethon 1.x, aiosqlite, pytest, pytest-asyncio, Docker Compose.

## Global Constraints

- Only the configured administrator may manage channels.
- Accept public `@username`/`t.me` references and private Telegram invite links.
- Accept broadcast channels only; reject groups, users, and bots.
- Never persist or log private invite links or hashes.
- Store at most 100 channels and show 10 channels per page.
- The dedicated reader account joins on add and leaves on confirmed removal.
- Pending add input expires after 15 minutes and supports `/cancel`.
- Existing channel rows and collector behavior remain compatible.

---

### Task 1: Expand channel persistence to 100 entries

**Files:**
- Modify: `src/job_bot/db.py`
- Modify: `tests/test_db.py`

**Interfaces:**
- Produces: `StoredChannel(channel_id: int, label: str, username: str | None)`.
- Produces: `Database.add_channel(channel_id: int, label: str, username: str | None = None) -> None`.
- Produces: `Database.get_channel(channel_id: int) -> StoredChannel | None` and `Database.list_channels() -> list[StoredChannel]`.

- [ ] **Step 1: Write failing persistence and migration tests**

Add tests that insert 100 distinct channels, reject the 101st with a message containing `100`, refresh an existing row at capacity, round-trip a nullable username, and open a legacy `channels(channel_id, label, created_at)` table.

```python
for index in range(100):
    await db.add_channel(-1000 - index, f"channel-{index}", f"name{index}")
with pytest.raises(ValueError, match="100"):
    await db.add_channel(-5000, "too-many")
await db.add_channel(-1000, "renamed", "renamed_channel")
assert (await db.get_channel(-1000)).username == "renamed_channel"
```

- [ ] **Step 2: Run the database tests and confirm they fail**

Run: `uv run pytest tests/test_db.py -v`

Expected: failure because the limit is 20, `StoredChannel` does not exist, and the `username` column is absent.

- [ ] **Step 3: Implement the additive migration and typed rows**

Add an immutable record and change the limit while preserving idempotent updates:

```python
@dataclass(frozen=True)
class StoredChannel:
    channel_id: int
    label: str
    username: str | None = None

CHANNEL_LIMIT = 100
```

Add nullable `username TEXT` to the create-table statement and to the existing idempotent migration routine. Make `add_channel` upsert `label` and `username`; make `list_channels` and `get_channel` return `StoredChannel` values.

- [ ] **Step 4: Run database tests**

Run: `uv run pytest tests/test_db.py tests/test_collector.py tests/test_smoke.py -v`

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/job_bot/db.py tests/test_db.py tests/test_collector.py tests/test_smoke.py
git commit -m "feat: expand managed channel storage"
```

---

### Task 2: Add safe Telethon channel membership operations

**Files:**
- Create: `src/job_bot/channel_management.py`
- Modify: `src/job_bot/telegram_adapters.py`
- Create: `tests/test_channel_management.py`
- Modify: `tests/test_telegram_adapters.py`

**Interfaces:**
- Produces: `ResolvedChannel(channel_id: int, title: str, username: str | None, joined_now: bool)`.
- Produces: `ChannelMembership.join_channel(reference: str) -> ResolvedChannel` and `ChannelMembership.leave_channel(channel_id: int) -> None` protocol methods.
- Produces: `TelethonUserAdapter.join_channel(reference: str) -> ResolvedChannel` and `leave_channel(channel_id: int) -> None`.
- Produces: `ChannelManagementError(code: str)` containing stable, non-secret application codes.

- [ ] **Step 1: Write failing parsing and adapter tests**

Cover `@jobs`, `jobs`, `https://t.me/jobs`, `t.me/jobs`, `https://t.me/+inviteHash`, and `https://t.me/joinchat/inviteHash`. Reject other hosts, post links, blank values, users, bots, basic groups, and megagroups. Assert that exceptions and their string representations never contain the submitted invite hash.

Use a fake Telethon client and validate the public/private request boundary:

```python
resolved = await adapter.join_channel("https://t.me/+privateSecret")
assert resolved.channel_id == -100123
assert resolved.title == "Private jobs"
assert "privateSecret" not in repr(resolved)
```

- [ ] **Step 2: Run focused tests and confirm they fail**

Run: `uv run pytest tests/test_channel_management.py tests/test_telegram_adapters.py -v`

Expected: failure because membership interfaces and reference parsing do not exist.

- [ ] **Step 3: Implement reference parsing and stable errors**

In `channel_management.py`, define:

```python
@dataclass(frozen=True)
class ResolvedChannel:
    channel_id: int
    title: str
    username: str | None
    joined_now: bool

class ChannelManagementError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)

class ChannelMembership(Protocol):
    async def join_channel(self, reference: str) -> ResolvedChannel: ...
    async def leave_channel(self, channel_id: int) -> None: ...
```

Parse only Telegram references. Return a public username or a private invite hash internally without storing the original input in an exception.

- [ ] **Step 4: Implement Telethon joining, validation, and leaving**

Use `JoinChannelRequest` for public channels, `ImportChatInviteRequest` for private invites, `CheckChatInviteRequest` to resolve already-used invites when possible, and `LeaveChannelRequest` for removal. Convert entities with `utils.get_peer_id`; accept only `Channel` entities where `broadcast is True` and `megagroup is False`.

Map invalid/expired invites, flood waits, access failures, and unsupported entities to stable `ChannelManagementError` codes such as `invalid_reference`, `invite_expired`, `not_broadcast`, `rate_limited`, and `telegram_unavailable`. Do not interpolate the input or invite hash into logs or exceptions.

- [ ] **Step 5: Run focused tests**

Run: `uv run pytest tests/test_channel_management.py tests/test_telegram_adapters.py -v`

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/job_bot/channel_management.py src/job_bot/telegram_adapters.py tests/test_channel_management.py tests/test_telegram_adapters.py
git commit -m "feat: manage Telegram channel membership"
```

---

### Task 3: Coordinate membership and allowlist consistency

**Files:**
- Modify: `src/job_bot/channel_management.py`
- Modify: `tests/test_channel_management.py`

**Interfaces:**
- Consumes: `ChannelMembership`, `ResolvedChannel`, `StoredChannel`, and `Database` from Tasks 1-2.
- Produces: `ChannelManagementService.add(reference: str) -> StoredChannel`.
- Produces: `ChannelManagementService.remove(channel_id: int) -> RemovalResult` where `RemovalResult.left: bool` reports best-effort leave success.

- [ ] **Step 1: Write failing orchestration tests**

Cover successful add, duplicate refresh, capacity failure, join failure without persistence, database failure followed by rollback leave, successful removal, and leave failure after immediate database removal.

```python
result = await service.remove(-100123)
assert not await db.is_allowed_channel(-100123)
assert result.left is False
assert membership.leave_calls == [-100123]
```

Assert that rollback leave occurs only when the current request actually joined a new channel.

- [ ] **Step 2: Run tests and confirm they fail**

Run: `uv run pytest tests/test_channel_management.py -v`

Expected: failure because `ChannelManagementService` and `RemovalResult` do not exist.

- [ ] **Step 3: Implement the coordinator**

```python
@dataclass(frozen=True)
class RemovalResult:
    channel: StoredChannel
    left: bool

class ChannelManagementService:
    def __init__(self, database: Database, membership: ChannelMembership) -> None:
        self._database = database
        self._membership = membership
```

`add` calls `join_channel` and validates before persistence. On persistence failure, call `leave_channel` only when `ResolvedChannel.joined_now` is true, suppress rollback errors, and re-raise a stable capacity or persistence error. `remove` loads the record, deletes it first, then attempts `leave_channel` and returns `left=False` if Telegram rejects the leave.

- [ ] **Step 4: Run service and database tests**

Run: `uv run pytest tests/test_channel_management.py tests/test_db.py -v`

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/job_bot/channel_management.py tests/test_channel_management.py
git commit -m "feat: coordinate channel allowlist changes"
```

---

### Task 4: Expose UI-neutral channel menu actions from the control service

**Files:**
- Modify: `src/job_bot/control_bot.py`
- Modify: `tests/test_control_bot.py`

**Interfaces:**
- Consumes: `ChannelManagementService` and typed stored channel rows.
- Produces: `ChannelMenu(items: tuple[StoredChannel, ...], page: int, pages: int, total: int, mode: str)`.
- Extends: `ControlResponse.channel_menu`, `begin_channel_add`, and `remove_confirmation`.
- Produces: `ControlBotService.add_channel(reference: str) -> ControlResponse`.

- [ ] **Step 1: Write failing control-service tests**

Test administrator authorization, empty and paginated `/channels` menus, `channels:list:<page>`, `channels:add`, removal selection, confirmation, cancellation, fallback `/channels add <reference>`, and fallback `/channels remove <id>`. Verify 10 rows per page and page clamping at 0, 10, 11, and 100 rows.

```python
response = await service.dispatch(
    ControlRequest(user_id=ADMIN_ID, callback_data="channels:add")
)
assert response.begin_channel_add is True
assert "ссылку" in response.text.casefold()
```

- [ ] **Step 2: Run tests and confirm they fail**

Run: `uv run pytest tests/test_control_bot.py -v`

Expected: failure because the control response has no channel-menu state.

- [ ] **Step 3: Add menu response types and callback routing**

Use ten rows per page and callback payloads shorter than Telegram's 64-byte limit:

```python
channels:list:<page>
channels:add
channels:remove:<page>
channels:pick:<channel_id>:<page>
channels:confirm:<channel_id>:<page>
channels:cancel:<page>
```

Return a confirmation response before calling `ChannelManagementService.remove`. Translate stable management error codes to concise Russian owner messages. Never include the submitted reference in an error message.

- [ ] **Step 4: Implement direct add input and fallback commands**

`add_channel(reference)` calls the coordinator and returns the refreshed first menu page. `/channels add <reference>` delegates to it. `/channels remove <channel_id>` returns the same confirmation object as the button flow and does not delete immediately.

- [ ] **Step 5: Run control-service tests**

Run: `uv run pytest tests/test_control_bot.py tests/test_channel_management.py -v`

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/job_bot/control_bot.py tests/test_control_bot.py
git commit -m "feat: add channel management control actions"
```

---

### Task 5: Render the interactive menu and 15-minute add flow

**Files:**
- Modify: `src/job_bot/runtime.py`
- Modify: `tests/test_runtime.py`

**Interfaces:**
- Consumes: UI-neutral `ControlResponse` channel fields from Task 4.
- Produces: `channel_menu_keyboard(menu: ChannelMenu) -> InlineKeyboardMarkup`.
- Produces: `channel_confirmation_keyboard(channel_id: int, page: int) -> InlineKeyboardMarkup`.
- Extends: runtime pending state with a mutually exclusive `PendingChannelAdd(expires_at: datetime)`.

- [ ] **Step 1: Write failing keyboard and conversation tests**

Assert menu buttons and callback payloads, pagination, add prompt, next-text routing, `/cancel`, commands not consumed as input, non-text retention, timeout, a new vacancy-edit flow replacing channel-add state, and a new channel-add flow replacing vacancy-edit state.

```python
await runtime._on_callback(FakeCallback("channels:add"))
await runtime._on_message(FakeMessage("https://t.me/jobs_feed"))
assert service.channel_additions == ["https://t.me/jobs_feed"]
```

Extend fake messages so `answer(text, reply_markup=None)` records both text and markup.

- [ ] **Step 2: Run runtime tests and confirm they fail**

Run: `uv run pytest tests/test_runtime.py -v`

Expected: failure because channel keyboards and pending-add state do not exist.

- [ ] **Step 3: Implement rendering and state routing**

Render channel menus from `ControlResponse.channel_menu`, including `Добавить канал`, `Удалить канал`, and page buttons. Render removal confirmation with `Удалить` and `Отмена` buttons. When `begin_channel_add` is returned, store an expiry of `now + timedelta(minutes=15)`.

Route the next eligible text to `ControlBotService.add_channel`. `/cancel` clears either pending flow; commands are dispatched normally without clearing it; non-text messages request text; expiry clears the state and reports it. Ensure only the configured administrator can create pending state.

- [ ] **Step 4: Run runtime and control tests**

Run: `uv run pytest tests/test_runtime.py tests/test_control_bot.py -v`

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/job_bot/runtime.py tests/test_runtime.py
git commit -m "feat: add interactive channel menu"
```

---

### Task 6: Wire production, document usage, and verify deployment

**Files:**
- Modify: `src/job_bot/app.py`
- Modify: `README.md`
- Modify: `tests/test_app.py`
- Modify: `tests/test_smoke.py`

**Interfaces:**
- Consumes: one shared `TelethonUserAdapter` for collector, response sending, and channel membership.
- Produces: production `ControlBotService(..., channel_manager=ChannelManagementService(database, telegram))` wiring.

- [ ] **Step 1: Write a failing application wiring test**

Patch construction dependencies and assert that the control service receives a channel manager backed by the same Telethon adapter used by the collector. This prevents a second Telegram session/client from being introduced.

- [ ] **Step 2: Run the application test and confirm it fails**

Run: `uv run pytest tests/test_app.py -v`

Expected: failure because `build_application` does not create or inject the channel manager.

- [ ] **Step 3: Wire the coordinator and update documentation**

Construct `ChannelManagementService(database, telegram)` in `build_application` and inject it into `ControlBotService`. Update README usage to show `/channels`, menu addition, accepted public/private references, confirmed removal, 100-channel limit, `/cancel`, and the fact that invite links are not stored.

- [ ] **Step 4: Run the full local verification**

Run:

```bash
uv run pytest -q
uv run python -m compileall -q src scripts
git diff --check
```

Expected: all tests pass, compilation succeeds, and `git diff --check` prints nothing.

- [ ] **Step 5: Commit**

```bash
git add src/job_bot/app.py README.md tests/test_app.py tests/test_smoke.py
git commit -m "docs: explain in-bot channel management"
```

- [ ] **Step 6: Push and deploy the exact verified revision**

Push `codex/telegram-job-bot`, pull the exact revision in `/opt/telegram-job-bot`, rebuild the `job-bot` service, and wait for Docker health to report `healthy`. Do not print `.env`, Telegram sessions, bot tokens, API hashes, passwords, or invite links.

- [ ] **Step 7: Run production-safe verification**

Run the existing smoke command inside the container and check:

```text
database_ok=true
channel_count=20
channels_username_column=1
container_state=running
container_health=healthy
container_restarts=0
```

Then ask the owner to open `/channels`. Do not add or remove a real production channel without the owner providing a channel reference through the bot.
