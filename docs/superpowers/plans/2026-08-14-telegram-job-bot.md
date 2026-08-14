# Telegram Job Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and deploy a single-user Telegram vacancy monitor that processes only allowlisted channels, ranks vacancies without sending Telegram content to AI, prepares grounded response drafts, and sends a recruiter message only after explicit approval.

**Architecture:** One asynchronous Python service hosts a Telegram user-client adapter, a separate administrator control bot, a deterministic vacancy pipeline, optional AI enrichment from permitted non-Telegram sources, and SQLite persistence. Every boundary is represented by a typed protocol so Telegram, HTTP, AI, time, and exchange rates are testable without external calls.

**Tech Stack:** Python 3.12+, `uv`, Telethon, aiogram 3, Pydantic 2, aiosqlite, httpx, Beautiful Soup 4, OpenAI Python SDK with the Responses API and structured outputs, pytest, pytest-asyncio, respx, Docker Compose.

## Global Constraints

- Process no more than 20 channels and only channels explicitly stored in the allowlist.
- Never send Telegram post content to an AI or ML system.
- AI enrichment may consume only content fetched directly from a permitted external source.
- Default OpenAI model is configurable and initially set to `gpt-5.4-mini`, which supports the Responses API and structured outputs.
- Salary floor is 100,000 RUB per month regardless of gross/net; a stated lower bound below it is rejected; missing salary remains eligible.
- English requirements above B1 cap the result at `borderline`.
- Scores 75–100 are `strong`, 50–74 are `borderline`, and 0–49 are `weak`.
- Only the configured administrator Telegram ID may view or mutate control state.
- Every outgoing message requires a non-expired, single-use approval bound to vacancy ID, recipient ID, and exact draft hash.
- Initial send limits are 5 messages per hour and 15 per day.
- No automatic retry is allowed after an ambiguous send timeout.
- Raw Telegram text is deleted after 30 days; daily encrypted database backups are retained for 7 days.
- Production runs on the Czech VPS without a public web port; the disclosed password must be rotated before access and must never enter Git.

## File Structure

```text
.
├── pyproject.toml                         # Runtime and development dependencies
├── uv.lock                                # Reproducible dependency lock
├── .env.example                           # Secret-free configuration contract
├── .gitignore                             # Sessions, databases, env files, caches
├── Dockerfile                             # Unprivileged production image
├── compose.yaml                           # App and encrypted-backup volumes
├── README.md                              # Setup, operation, recovery, deployment
├── config/
│   └── candidate-profile.full.json        # Canonical verified candidate facts
├── src/job_bot/
│   ├── __init__.py
│   ├── app.py                             # Dependency assembly and lifecycle
│   ├── config.py                          # Validated environment settings
│   ├── domain.py                          # Shared enums and Pydantic domain models
│   ├── db.py                              # SQLite schema, transactions, repositories
│   ├── exchange_rates.py                  # Daily Bank of Russia currency rates
│   ├── parsing.py                         # Deterministic Telegram vacancy extraction
│   ├── scoring.py                         # Hard constraints and rule score
│   ├── collector.py                       # Allowlist-aware Telegram event ingestion
│   ├── sources.py                         # Safe external-page retrieval and extraction
│   ├── drafting.py                        # Template and OpenAI external-source drafting
│   ├── pipeline.py                        # Idempotent end-to-end vacancy processing
│   ├── control_bot.py                     # Commands, cards, callback routing
│   ├── approvals.py                       # Draft hashes and single-use authorization
│   ├── sender.py                          # Rate-limited recruiter delivery
│   ├── scheduler.py                       # Digests, retention, backup triggers
│   ├── observability.py                   # Redacted logging and health snapshot
│   └── telegram_adapters.py               # Telethon and aiogram concrete adapters
├── tests/
│   ├── conftest.py
│   ├── test_config.py
│   ├── test_db.py
│   ├── test_exchange_rates.py
│   ├── test_parsing.py
│   ├── test_scoring.py
│   ├── test_collector.py
│   ├── test_sources.py
│   ├── test_drafting.py
│   ├── test_pipeline.py
│   ├── test_control_bot.py
│   ├── test_approvals_sender.py
│   ├── test_scheduler.py
│   └── test_observability.py
└── scripts/
    ├── backup.sh                          # Encrypted SQLite backup and rotation
    └── smoke_test.py                      # Read-only production health verification
```

---

### Task 1: Project Foundation, Configuration, and Candidate Profile

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `config/candidate-profile.full.json`
- Create: `src/job_bot/__init__.py`
- Create: `src/job_bot/config.py`
- Create: `src/job_bot/domain.py`
- Create: `tests/conftest.py`
- Create: `tests/test_config.py`

**Interfaces:**
- Consumes: the verified profile at `C:/Users/daner/OneDrive/Documents/ChatGPT/JOB JOB JOB/candidate-profile.full.json`.
- Produces: `Settings.load() -> Settings`, `load_candidate_profile(path: Path) -> CandidateProfile`, and the shared `Vacancy`, `Assessment`, `Draft`, `MatchClass`, and `VacancyStatus` models.

- [ ] **Step 1: Write failing configuration and profile tests**

```python
# tests/test_config.py
from pathlib import Path
import pytest
from job_bot.config import Settings, load_candidate_profile

def test_settings_reject_missing_secrets(monkeypatch):
    for name in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "CONTROL_BOT_TOKEN", "ADMIN_TELEGRAM_ID"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError):
        Settings.load()

def test_candidate_profile_keeps_confirmed_english_level(profile_path: Path):
    profile = load_candidate_profile(profile_path)
    english = next(item for item in profile.languages if item.language == "Английский")
    assert english.level == "B1"
    assert profile.job_search.minimum_income.amount == 1500
    assert profile.job_search.minimum_income.currency == "USD"
```

- [ ] **Step 2: Run the tests and confirm the expected import failure**

Run: `uv run pytest tests/test_config.py -v`  
Expected: FAIL because `job_bot.config` does not exist.

- [ ] **Step 3: Create the package, dependency manifest, secret contract, and shared types**

```toml
# pyproject.toml
[project]
name = "telegram-job-bot"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "aiogram>=3,<4", "aiosqlite>=0.20,<1", "beautifulsoup4>=4.12,<5",
  "httpx>=0.27,<1", "openai>=1,<3", "pydantic>=2,<3",
  "pydantic-settings>=2,<3", "telethon>=1.36,<2"
]
[dependency-groups]
dev = ["pytest>=8,<10", "pytest-asyncio>=0.24,<2", "respx>=0.21,<1"]
[tool.pytest.ini_options]
asyncio_mode = "auto"
pythonpath = ["src"]
```

```python
# src/job_bot/domain.py
from datetime import datetime
from enum import StrEnum
from pydantic import BaseModel, Field, HttpUrl

class MatchClass(StrEnum):
    STRONG = "strong"
    BORDERLINE = "borderline"
    WEAK = "weak"
    REJECTED = "rejected"

class VacancyStatus(StrEnum):
    NEW = "new"
    ASSESSED = "assessed"
    QUEUED = "queued"
    SENT = "sent"
    SKIPPED = "skipped"
    NOT_RELEVANT = "not_relevant"
    FAILED = "failed"

class Vacancy(BaseModel):
    id: str
    channel_id: int
    message_id: int
    published_at: datetime
    fingerprint: str
    raw_text: str
    title: str | None = None
    company: str | None = None
    salary_min_rub: int | None = None
    salary_present: bool = False
    remote: bool | None = None
    locations: list[str] = Field(default_factory=list)
    english_required: str | None = None
    recruiter_username: str | None = None
    external_urls: list[HttpUrl] = Field(default_factory=list)

class Assessment(BaseModel):
    score: int = Field(ge=0, le=100)
    match_class: MatchClass
    reasons: list[str]
    warnings: list[str] = Field(default_factory=list)
    hard_rejection: str | None = None

class Draft(BaseModel):
    text: str
    origin: str
    evidence_ids: list[str]
```

Implement `Settings` with required Telegram values, optional `OPENAI_API_KEY`, `OPENAI_MODEL="gpt-5.4-mini"`, paths, thresholds, timezone, digest times, and rate limits. Implement `load_candidate_profile()` as a validated Pydantic wrapper that preserves the source JSON without fabricating missing fields. Copy the supplied canonical JSON byte-for-byte into `config/`.

`CandidateProfile` must expose `fact_ids: set[str]` and `fact_text(fact_id: str) -> str`. Build the catalog deterministically from approved source paths such as `experience.apps_labs.achievements.launch_count`; reject unknown IDs rather than returning guessed content. `.env.example` contains names and safe defaults only: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_PHONE`, `TELEGRAM_SESSION_PATH`, `CONTROL_BOT_TOKEN`, `ADMIN_TELEGRAM_ID`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `DATABASE_PATH`, `APP_TIMEZONE`, `DIGEST_TIMES`, and `BACKUP_AGE_RECIPIENT`. `.gitignore` excludes `.env`, `*.session*`, `*.sqlite3*`, `/data/`, `/backups/`, `.venv/`, `.pytest_cache/`, and Python caches.

- [ ] **Step 4: Install, lock, and run tests**

Run: `uv sync && uv run pytest tests/test_config.py -v`  
Expected: PASS; `uv.lock` is created.

- [ ] **Step 5: Verify secrets and the disclosed password are absent**

Run: `git grep -n -I -E "(TELEGRAM_API_HASH|CONTROL_BOT_TOKEN|OPENAI_API_KEY|TELEGRAM_PHONE)=.+" -- ':!*.example'`  
Expected: no matches.

- [ ] **Step 6: Commit the foundation**

```powershell
git add -- pyproject.toml uv.lock .gitignore .env.example config src/job_bot tests
git commit -m "chore: scaffold Telegram job bot"
```

---

### Task 2: SQLite Schema and Idempotent Repository

**Files:**
- Create: `src/job_bot/db.py`
- Create: `tests/test_db.py`

**Interfaces:**
- Consumes: `Vacancy`, `Assessment`, `Draft`, and `VacancyStatus` from `domain.py`.
- Produces: `Database.open(path)`, `Database.close()`, `add_channel()`, `remove_channel()`, `is_allowed_channel()`, `insert_vacancy() -> bool`, `save_assessment()`, `save_draft()`, `list_queue()`, `record_decision()`, and `purge_raw_text(before)`.

- [ ] **Step 1: Write failing repository tests**

```python
# tests/test_db.py
import pytest
from job_bot.db import Database

@pytest.mark.asyncio
async def test_allowlist_and_fingerprint_are_idempotent(tmp_path, vacancy):
    db = await Database.open(tmp_path / "bot.sqlite3")
    await db.add_channel(-100123, "jobs")
    assert await db.is_allowed_channel(-100123)
    assert await db.insert_vacancy(vacancy) is True
    assert await db.insert_vacancy(vacancy) is False
    await db.close()

@pytest.mark.asyncio
async def test_raw_text_can_be_purged_without_losing_decision(tmp_path, vacancy, old_time):
    db = await Database.open(tmp_path / "bot.sqlite3")
    await db.insert_vacancy(vacancy)
    await db.record_decision(vacancy.id, "skipped")
    await db.purge_raw_text(old_time)
    stored = await db.get_vacancy(vacancy.id)
    assert stored.raw_text == ""
    assert stored.status == "skipped"
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `uv run pytest tests/test_db.py -v`  
Expected: FAIL because `Database` does not exist.

- [ ] **Step 3: Implement schema and transactional repository**

Create schema version 1 with tables `schema_version`, `channels`, `vacancies`, `assessments`, `drafts`, `approvals`, `send_attempts`, `feedback`, `exchange_rates`, and `settings`. Enforce unique constraints on `(channel_id, message_id)`, `fingerprint`, and successful `vacancy_id` sends. Enable `PRAGMA journal_mode=WAL`, `foreign_keys=ON`, and `busy_timeout=5000`.

Implement these exact asynchronous methods on `Database`: class method `open(path: Path) -> Database`; `transaction() -> AsyncContextManager[aiosqlite.Connection]`; `insert_vacancy(vacancy: Vacancy) -> bool`; `reserve_send(vacancy_id: str, approval_id: str) -> bool`; and `finish_send(vacancy_id: str, telegram_message_id: int) -> None`. Use explicit SQL and row-to-model functions; do not expose raw connections outside `Database`.

- [ ] **Step 4: Run repository tests**

Run: `uv run pytest tests/test_db.py -v`  
Expected: PASS.

- [ ] **Step 5: Commit persistence**

```powershell
git add -- src/job_bot/db.py tests/test_db.py tests/conftest.py
git commit -m "feat: add idempotent SQLite repository"
```

---

### Task 3: Deterministic Parsing, Currency Conversion, and Scoring

**Files:**
- Create: `src/job_bot/exchange_rates.py`
- Create: `src/job_bot/parsing.py`
- Create: `src/job_bot/scoring.py`
- Create: `tests/test_exchange_rates.py`
- Create: `tests/test_parsing.py`
- Create: `tests/test_scoring.py`

**Interfaces:**
- Consumes: raw Telegram post text, source metadata, canonical candidate profile, and `ExchangeRates.rub_per_unit(currency) -> Decimal`.
- Produces: `parse_vacancy(channel_id: int, message_id: int, published_at: datetime, text: str, rates: ExchangeRates) -> Vacancy`, `fingerprint(text: str, urls: Sequence[str]) -> str`, and `score_vacancy(vacancy: Vacancy, profile: CandidateProfile, policy: ScoringPolicy) -> Assessment`.

- [ ] **Step 1: Write failing parser tests for salaries and contacts**

```python
# tests/test_parsing.py
@pytest.mark.parametrize(("text", "expected"), [
    ("ЗП 120–160 тыс. ₽ net", 120_000),
    ("от $1500 gross", 150_000),
    ("зарплата не указана", None),
])
def test_salary_lower_bound(text, expected, fixed_rates):
    vacancy = parse_vacancy(-1, 7, NOW, text, fixed_rates)
    assert vacancy.salary_min_rub == expected

def test_extracts_recruiter_without_treating_channel_as_recipient(fixed_rates):
    vacancy = parse_vacancy(-1001, 8, NOW, "Пишите @hr_alex", fixed_rates)
    assert vacancy.recruiter_username == "hr_alex"
```

Also write an HTTP fixture test that parses nominal-aware Bank of Russia XML, proving that a quote of `90.0000` for nominal `1` USD yields `Decimal("90.0000")` RUB per USD and a quote of `85.0000` for nominal `100` JPY yields `Decimal("0.8500")` RUB per JPY. The official XML contract is documented at <https://www.cbr.ru/development/sxml/>.

- [ ] **Step 2: Write failing scoring tests for every hard rule**

```python
# tests/test_scoring.py
def test_explicit_salary_below_floor_is_rejected(profile, policy, vacancy_factory):
    result = score_vacancy(vacancy_factory(salary_present=True, salary_min_rub=99_999), profile, policy)
    assert result.match_class == MatchClass.REJECTED

def test_missing_salary_remains_eligible(profile, policy, vacancy_factory):
    result = score_vacancy(vacancy_factory(salary_present=False, salary_min_rub=None), profile, policy)
    assert result.hard_rejection is None

def test_english_above_b1_caps_strong_match(profile, policy, vacancy_factory):
    result = score_vacancy(vacancy_factory(english_required="C1", title="Technical Project Manager"), profile, policy)
    assert result.match_class == MatchClass.BORDERLINE
    assert any("B1" in item for item in result.warnings)
```

- [ ] **Step 3: Run focused tests and confirm failure**

Run: `uv run pytest tests/test_exchange_rates.py tests/test_parsing.py tests/test_scoring.py -v`  
Expected: FAIL because parser and scorer do not exist.

- [ ] **Step 4: Implement the exchange-rate adapter**

Fetch `https://www.cbr.ru/scripts/XML_daily.asp` once per UTC day with a 10-second timeout, parse `CharCode`, `Nominal`, and `Value` using decimal commas safely, and expose `rub_per_unit(currency: str) -> Decimal | None`. RUB always returns `Decimal("1")`. Cache the last successful dated response in SQLite. If neither a fresh response nor cached rate exists, return `None`; the parser then records the foreign salary as unknown and adds a warning instead of rejecting it.

- [ ] **Step 5: Implement deterministic extraction and scoring**

Implement normalized regex dictionaries for salary units/currencies, role aliases, remote/hybrid/office, locations, English levels, Telegram usernames, and HTTP links. Hash normalized text plus sorted canonical URLs with SHA-256.

Use these initial weights:

```python
ROLE_WEIGHT = 35
RESPONSIBILITY_WEIGHT = 25
WORK_FORMAT_WEIGHT = 20
VERIFIED_SKILL_WEIGHT = 10
COMPENSATION_WEIGHT = 10
STRONG_THRESHOLD = 75
BORDERLINE_THRESHOLD = 50
```

Return human-readable evidence for every awarded category. Reject only an explicit salary below the floor. Treat incompatible or unclear location/format as a score reduction and warning so potentially negotiable vacancies are not silently lost. Do not introduce an industry stop-list.

- [ ] **Step 6: Run exchange-rate, parser, and scorer tests**

Run: `uv run pytest tests/test_exchange_rates.py tests/test_parsing.py tests/test_scoring.py -v`  
Expected: PASS.

- [ ] **Step 7: Commit deterministic filtering**

```powershell
git add -- src/job_bot/exchange_rates.py src/job_bot/parsing.py src/job_bot/scoring.py tests/test_exchange_rates.py tests/test_parsing.py tests/test_scoring.py tests/conftest.py
git commit -m "feat: parse and score vacancies deterministically"
```

---

### Task 4: Allowlist-Aware Telegram Collection

**Files:**
- Create: `src/job_bot/collector.py`
- Create: `src/job_bot/telegram_adapters.py`
- Create: `tests/test_collector.py`

**Interfaces:**
- Consumes: `ChannelPost(channel_id, message_id, published_at, text)`, `Database.is_allowed_channel()`, and `VacancyPipeline.process_post()`.
- Produces: `Collector.handle(post) -> CollectionResult` and `TelethonUserAdapter.start(on_post)`.

- [ ] **Step 1: Write failing allowlist and duplicate tests**

```python
# tests/test_collector.py
@pytest.mark.asyncio
async def test_ignores_non_allowlisted_channel(fake_db, pipeline):
    fake_db.allowed = set()
    result = await Collector(fake_db, pipeline).handle(post(channel_id=-1009))
    assert result == CollectionResult.IGNORED
    pipeline.process_post.assert_not_awaited()

@pytest.mark.asyncio
async def test_forwards_allowlisted_post_once(fake_db, pipeline):
    fake_db.allowed = {-1009}
    collector = Collector(fake_db, pipeline)
    assert await collector.handle(post(channel_id=-1009)) == CollectionResult.PROCESSED
    pipeline.process_post.assert_awaited_once()
```

- [ ] **Step 2: Run the collector tests and confirm failure**

Run: `uv run pytest tests/test_collector.py -v`  
Expected: FAIL because `Collector` does not exist.

- [ ] **Step 3: Implement the protocol boundary and Telethon adapter**

```python
# src/job_bot/collector.py
class VacancyPipelineProtocol(Protocol):
    async def process_post(self, post: ChannelPost) -> bool:
        raise NotImplementedError

class Collector:
    async def handle(self, post: ChannelPost) -> CollectionResult:
        if not await self.db.is_allowed_channel(post.channel_id):
            return CollectionResult.IGNORED
        return CollectionResult.PROCESSED if await self.pipeline.process_post(post) else CollectionResult.DUPLICATE
```

Translate Telethon events into `ChannelPost` without exposing Telethon objects to domain code. Subscribe to new messages, not history scraping. Do not attach handlers for private chats.

- [ ] **Step 4: Run collector tests**

Run: `uv run pytest tests/test_collector.py -v`  
Expected: PASS.

- [ ] **Step 5: Commit collection**

```powershell
git add -- src/job_bot/collector.py src/job_bot/telegram_adapters.py tests/test_collector.py
git commit -m "feat: collect posts from allowlisted channels"
```

---

### Task 5: Safe External Sources and Grounded Drafting

**Files:**
- Create: `src/job_bot/sources.py`
- Create: `src/job_bot/drafting.py`
- Create: `tests/test_sources.py`
- Create: `tests/test_drafting.py`

**Interfaces:**
- Consumes: external URL, `CandidateProfile`, `Vacancy`, and optional `OPENAI_API_KEY`.
- Produces: `SourceFetcher.fetch(url) -> ExternalVacancy | None`, `TemplateDrafter.create(vacancy, assessment, profile) -> Draft`, and `OpenAIDrafter.create(external, vacancy, profile) -> Enrichment`.

- [ ] **Step 1: Write failing source-safety tests**

```python
# tests/test_sources.py
@pytest.mark.asyncio
async def test_rejects_private_and_loopback_targets(fetcher):
    for url in ("http://127.0.0.1/job", "http://169.254.169.254/latest/meta-data"):
        assert await fetcher.fetch(url) is None

@pytest.mark.asyncio
async def test_rejects_non_html_and_oversized_body(fetcher, respx_mock):
    respx_mock.get("https://jobs.example/file").mock(return_value=httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"x"))
    assert await fetcher.fetch("https://jobs.example/file") is None
```

- [ ] **Step 2: Write failing grounding tests**

```python
# tests/test_drafting.py
def test_template_uses_only_profile_evidence(vacancy, assessment, profile):
    draft = TemplateDrafter().create(vacancy, assessment, profile)
    assert draft.origin == "telegram_rules_template"
    assert "C1" not in draft.text
    assert all(evidence_id in profile.fact_ids for evidence_id in draft.evidence_ids)

@pytest.mark.asyncio
async def test_ai_receives_external_text_not_telegram_text(fake_openai, vacancy, profile):
    vacancy.raw_text = "TELEGRAM_SENTINEL"
    await OpenAIDrafter(fake_openai).create(external("EXTERNAL_SOURCE"), vacancy, profile)
    assert "EXTERNAL_SOURCE" in fake_openai.last_input
    assert "TELEGRAM_SENTINEL" not in fake_openai.last_input
```

- [ ] **Step 3: Run source and drafting tests and confirm failure**

Run: `uv run pytest tests/test_sources.py tests/test_drafting.py -v`  
Expected: FAIL because the fetcher and drafters do not exist.

- [ ] **Step 4: Implement guarded HTTP retrieval**

Allow only `https` and `http`, resolve DNS before every request and redirect, reject private/reserved/link-local IPs, limit redirects to 3, timeout to 10 seconds, response bytes to 1 MiB, and accepted content types to HTML/text. Respect an explicit deny-domain configuration and a negative robots/access decision. Extract visible main text with Beautiful Soup, cap it at 30,000 characters, and retain source URL plus retrieval timestamp.

- [ ] **Step 5: Implement template and OpenAI adapters**

Define a strict Pydantic `Enrichment` schema containing score adjustment, evidence, warnings, and draft. The OpenAI adapter calls the Responses API structured-output parser with `gpt-5.4-mini` by default. Its input contains external text and the verified subset of candidate facts, never `Vacancy.raw_text`, channel identifiers, or Telegram links. Validate every returned evidence ID against the profile; on any invalid ID, raise `UngroundedOutput` so the caller falls back to `TemplateDrafter`.

Official model capability reference: <https://developers.openai.com/api/docs/models/gpt-5.4-mini>

- [ ] **Step 6: Run source and drafting tests**

Run: `uv run pytest tests/test_sources.py tests/test_drafting.py -v`  
Expected: PASS.

- [ ] **Step 7: Commit enrichment and drafting**

```powershell
git add -- src/job_bot/sources.py src/job_bot/drafting.py tests/test_sources.py tests/test_drafting.py
git commit -m "feat: add compliant vacancy enrichment and drafting"
```

---

### Task 6: Idempotent Processing Pipeline and Vacancy Cards

**Files:**
- Create: `src/job_bot/pipeline.py`
- Create: `src/job_bot/control_bot.py`
- Create: `tests/test_pipeline.py`
- Create: `tests/test_control_bot.py`

**Interfaces:**
- Consumes: parser, scorer, repository, source fetcher, drafters, and `Notifier.send_card(card)`.
- Produces: `VacancyPipeline.process_post(post) -> bool`, `VacancyCard`, command handlers for `/channels`, `/settings`, `/queue`, `/history`, and `/status`.

- [ ] **Step 1: Write failing end-to-end classification tests**

```python
# tests/test_pipeline.py
@pytest.mark.asyncio
async def test_strong_match_notifies_immediately(pipeline, notifier, strong_post):
    assert await pipeline.process_post(strong_post) is True
    notifier.send_card.assert_awaited_once()

@pytest.mark.asyncio
async def test_borderline_match_waits_for_digest(pipeline, notifier, c1_post):
    await pipeline.process_post(c1_post)
    notifier.send_card.assert_not_awaited()
    assert await pipeline.db.count_digest_pending() == 1

@pytest.mark.asyncio
async def test_same_post_is_not_processed_twice(pipeline, strong_post):
    assert await pipeline.process_post(strong_post) is True
    assert await pipeline.process_post(strong_post) is False
```

- [ ] **Step 2: Write failing administrator authorization and channel-command tests**

```python
# tests/test_control_bot.py
@pytest.mark.asyncio
async def test_non_admin_callback_is_rejected(control_bot):
    result = await control_bot.dispatch(callback(user_id=999, data="approve:v1"))
    assert result.alert == "Доступ запрещён"

@pytest.mark.asyncio
async def test_channels_add_persists_numeric_channel_id(control_bot, db):
    await control_bot.dispatch(message(user_id=ADMIN_ID, text="/channels add -100123 jobs"))
    assert await db.is_allowed_channel(-100123)
```

- [ ] **Step 3: Run pipeline and control tests and confirm failure**

Run: `uv run pytest tests/test_pipeline.py tests/test_control_bot.py -v`  
Expected: FAIL because pipeline and control bot do not exist.

- [ ] **Step 4: Implement orchestration and cards**

Persist the vacancy before enrichment, save the deterministic assessment, attempt at most one external source, validate/fallback drafting, and transactionally mark `strong`, `borderline`, `weak`, or `rejected`. Render cards with source type, score, reasons, warnings, contact, links, and draft. Callback data contains opaque record IDs only, never draft text or contact details.

- [ ] **Step 5: Implement administrator-only commands**

Register one outer authorization middleware checking `from_user.id == settings.admin_telegram_id`. Implement exact command grammar:

```text
/channels
/channels add <numeric_channel_id> <label>
/channels remove <numeric_channel_id>
/settings
/settings timezone <IANA_name>
/settings digest <HH:MM> <HH:MM>
/queue
/history
/status
```

Reject malformed commands without changing state.

- [ ] **Step 6: Run pipeline and control tests**

Run: `uv run pytest tests/test_pipeline.py tests/test_control_bot.py -v`  
Expected: PASS.

- [ ] **Step 7: Commit pipeline and interface**

```powershell
git add -- src/job_bot/pipeline.py src/job_bot/control_bot.py tests/test_pipeline.py tests/test_control_bot.py
git commit -m "feat: add vacancy pipeline and control bot"
```

---

### Task 7: Approval Tokens and Safe Recruiter Sending

**Files:**
- Create: `src/job_bot/approvals.py`
- Create: `src/job_bot/sender.py`
- Create: `tests/test_approvals_sender.py`

**Interfaces:**
- Consumes: stored vacancy/draft, administrator action, clock, repository, and `TelegramSender.send_private(username, text) -> int`.
- Produces: `ApprovalService.issue() -> Approval`, `ApprovalService.consume() -> ApprovedSend`, `SafeSender.send(approval_id) -> SendResult`.

- [ ] **Step 1: Write failing approval safety tests**

```python
# tests/test_approvals_sender.py
@pytest.mark.asyncio
async def test_edit_invalidates_previous_approval(approval_service, draft):
    approval = await approval_service.issue("v1", "hr_alex", draft)
    await approval_service.replace_draft("v1", Draft(text="edited", origin="user", evidence_ids=[]))
    with pytest.raises(ApprovalInvalid):
        await approval_service.consume(approval.id)

@pytest.mark.asyncio
async def test_approval_is_single_use(sender, approval):
    assert (await sender.send(approval.id)).status == "sent"
    assert (await sender.send(approval.id)).status == "already_consumed"

@pytest.mark.asyncio
async def test_ambiguous_timeout_is_not_retried(sender, telegram_sender, approval):
    telegram_sender.send_private.side_effect = TimeoutError
    assert (await sender.send(approval.id)).status == "unknown"
    telegram_sender.send_private.assert_awaited_once()
```

- [ ] **Step 2: Write failing rate-limit and recipient tests**

```python
@pytest.mark.asyncio
async def test_hourly_limit_blocks_sixth_send(sender, approvals, clock):
    for approval in approvals[:5]:
        assert (await sender.send(approval.id)).status == "sent"
    assert (await sender.send(approvals[5].id)).status == "rate_limited"

@pytest.mark.parametrize("recipient", ("-100123", "https://t.me/jobs_channel"))
def test_non_private_recipient_is_rejected(recipient):
    with pytest.raises(InvalidRecipient):
        normalize_recruiter_recipient(recipient)

@pytest.mark.asyncio
async def test_resolved_bot_recipient_is_rejected(sender, telegram_sender, approval):
    telegram_sender.resolve_user.return_value.is_bot = True
    assert (await sender.send(approval.id)).status == "invalid_recipient"
    telegram_sender.send_private.assert_not_awaited()
```

- [ ] **Step 3: Run the approval/sender tests and confirm failure**

Run: `uv run pytest tests/test_approvals_sender.py -v`  
Expected: FAIL because approval and sender services do not exist.

- [ ] **Step 4: Implement cryptographic draft binding and transactional sending**

Use SHA-256 over canonical UTF-8 draft text, recipient, and vacancy ID. Store a random 128-bit approval ID, issue time, seven-day expiry, and draft hash. In one database transaction, consume the approval and reserve the vacancy send before the network call. Mark success with the Telegram message ID. Mark timeout as `unknown`, alert the user, and require manual reconciliation; never release it for automatic retry.

- [ ] **Step 5: Implement rolling rate limits and recipient validation**

Count completed and unknown send attempts in the preceding hour and calendar day in the configured timezone. Reject bot usernames by resolving the entity before send; accept only a Telegram user entity. Apply 5/hour and 15/day defaults.

- [ ] **Step 6: Run approval and sender tests**

Run: `uv run pytest tests/test_approvals_sender.py -v`  
Expected: PASS.

- [ ] **Step 7: Commit safe sending**

```powershell
git add -- src/job_bot/approvals.py src/job_bot/sender.py tests/test_approvals_sender.py
git commit -m "feat: require single-use approval for recruiter messages"
```

---

### Task 8: Digests, Retention, Health, and Redacted Logging

**Files:**
- Create: `src/job_bot/scheduler.py`
- Create: `src/job_bot/observability.py`
- Create: `tests/test_scheduler.py`
- Create: `tests/test_observability.py`
- Create: `scripts/backup.sh`

**Interfaces:**
- Consumes: repository, notifier, configured timezone/times, clock, and backup command.
- Produces: `Scheduler.tick(now)`, `HealthService.snapshot() -> HealthSnapshot`, `configure_logging()`, and exit-code-based encrypted backup script.

- [ ] **Step 1: Write failing digest and retention tests**

```python
# tests/test_scheduler.py
@pytest.mark.asyncio
async def test_digest_runs_once_per_slot(scheduler, notifier, digest_time):
    await scheduler.tick(digest_time)
    await scheduler.tick(digest_time)
    notifier.send_digest.assert_awaited_once()

@pytest.mark.asyncio
async def test_retention_removes_only_raw_text(scheduler, db, old_vacancy):
    await scheduler.run_retention()
    stored = await db.get_vacancy(old_vacancy.id)
    assert stored.raw_text == ""
    assert stored.fingerprint
```

- [ ] **Step 2: Write failing redaction and health tests**

```python
# tests/test_observability.py
def test_log_filter_redacts_credentials_and_contacts(caplog, redacted_logger):
    redacted_logger.info("token=123:ABC phone=+79991234567 email=a@example.com")
    assert "123:ABC" not in caplog.text
    assert "+79991234567" not in caplog.text
    assert "a@example.com" not in caplog.text

@pytest.mark.asyncio
async def test_revoked_session_marks_sending_unhealthy(health_service):
    health_service.telegram.session_valid = False
    snapshot = await health_service.snapshot()
    assert snapshot.can_send is False
```

- [ ] **Step 3: Run scheduler and observability tests and confirm failure**

Run: `uv run pytest tests/test_scheduler.py tests/test_observability.py -v`  
Expected: FAIL because scheduler and observability modules do not exist.

- [ ] **Step 4: Implement timezone-safe scheduling and cleanup**

Store the last successful digest slot in SQLite. On each minute tick, calculate due slots with `zoneinfo.ZoneInfo`; a restart must not duplicate a sent digest. Run raw-text retention daily and group repeated alerts by `(component, error_code)` for 30 minutes.

- [ ] **Step 5: Implement health snapshot and log redaction**

Expose health through `/status`, not HTTP. Include database availability, Telegram user session, Bot API polling, last collected post, pending queue count, last digest, last backup, and AI availability. Redact bot-token shapes, API keys, phone numbers, emails, session paths, and environment values before handlers emit logs.

- [ ] **Step 6: Implement encrypted backup script**

`scripts/backup.sh` must run SQLite's online backup command, encrypt the result with `age` using `BACKUP_AGE_RECIPIENT`, atomically move it into the backup volume, delete encrypted backups older than seven days, and return nonzero on any failure. It must never print the encryption identity or environment file.

- [ ] **Step 7: Run scheduler and observability tests**

Run: `uv run pytest tests/test_scheduler.py tests/test_observability.py -v`  
Expected: PASS.

- [ ] **Step 8: Commit operations behavior**

```powershell
git add -- src/job_bot/scheduler.py src/job_bot/observability.py tests/test_scheduler.py tests/test_observability.py scripts/backup.sh
git commit -m "feat: add digests retention and health monitoring"
```

---

### Task 9: Application Assembly, Containers, Documentation, and Staging Gate

**Files:**
- Create: `src/job_bot/app.py`
- Create: `Dockerfile`
- Create: `compose.yaml`
- Create: `scripts/smoke_test.py`
- Create: `README.md`
- Modify: `.env.example`
- Test: all files under `tests/`

**Interfaces:**
- Consumes: all services from Tasks 1–8.
- Produces: `python -m job_bot.app`, a restart-safe Docker service, a read-only smoke test, and operator instructions.

- [ ] **Step 1: Write failing lifecycle test**

```python
# tests/test_app.py
@pytest.mark.asyncio
async def test_shutdown_stops_ingress_before_closing_database(app_factory):
    app, events = app_factory()
    await app.start()
    await app.stop()
    assert events == ["collector.start", "bot.start", "collector.stop", "bot.stop", "db.close"]
```

- [ ] **Step 2: Run lifecycle test and confirm failure**

Run: `uv run pytest tests/test_app.py -v`  
Expected: FAIL because application assembly does not exist.

- [ ] **Step 3: Assemble startup and graceful shutdown**

Load settings and profile, open/migrate SQLite, build adapters and services, start the user collector and control-bot polling concurrently, and start the scheduler loop. On SIGTERM, stop ingress first, wait up to 20 seconds for in-flight non-send processing, stop polling, then close SQLite. An in-flight send reservation remains durable and is reconciled after restart rather than retried.

- [ ] **Step 4: Create hardened container configuration**

Use a multi-stage Python 3.12 slim image, install locked dependencies with `uv sync --frozen --no-dev`, and install the runtime `sqlite3`, CA certificates, and `age` packages. Create UID/GID 10001, run as that user, set a read-only root filesystem, mount `/data`, `/backups`, and `/run/job-bot`, drop all Linux capabilities, enable `no-new-privileges`, and set `restart: unless-stopped`. Do not publish ports.

- [ ] **Step 5: Write operator documentation and smoke test**

Document:

1. rotating the exposed VPS password before first access;
2. creating a dedicated SSH key and disabling root password login only after verifying key login;
3. obtaining the deployment's own Telegram `api_id`/`api_hash` and BotFather token;
4. creating the Telegram user session interactively without logging codes or 2FA secrets;
5. configuring the administrator ID, timezone, digest times, OpenAI key, and backup recipient;
6. adding the private test channel before production channels;
7. backup restore, session reauthorization, secret rotation, update, rollback, and log inspection.

`scripts/smoke_test.py` opens the database read-only and reports schema version, channel count, queue count, last collection time, last digest time, last backup time, and whether required secret names are present without displaying their values.

- [ ] **Step 6: Run the complete local verification suite**

Run: `uv run pytest -q && uv run python -m compileall -q src tests scripts && docker compose config --quiet`  
Expected: all tests pass, compilation succeeds, and Compose configuration is valid.

- [ ] **Step 7: Run secret and compliance scans**

Run: `git grep -n -I -E "(api_hash|bot_token|openai_api_key|telegram_phone)\s*=\s*['\"][^'\"]+|raw_text.*openai|OpenAI.*raw_text" -- ':!*.example'`  
Expected: no credential or Telegram-to-AI flow matches.

- [ ] **Step 8: Commit the runnable application**

```powershell
git add -- src/job_bot/app.py tests/test_app.py Dockerfile compose.yaml scripts/smoke_test.py README.md .env.example
git commit -m "feat: assemble and package Telegram job bot"
```

- [ ] **Step 9: Perform the staging gate on the Czech VPS**

After the user rotates the disclosed password and SSH-key access is established:

1. deploy to a non-production directory owned by the unprivileged app user;
2. authenticate the dedicated Telegram account interactively;
3. add one private test channel and one non-recruiter test user;
4. post fixtures that exercise `strong`, `borderline`, `weak`, rejected salary, missing salary, and C1 English;
5. verify both digest slots with temporary schedule values;
6. verify editing invalidates approval and one approval sends once;
7. induce a container restart and verify no duplicate card or send;
8. revoke the Telegram session and verify collection/sending pause with an administrator alert;
9. restore an encrypted backup into a fresh volume and run the smoke test;
10. inspect logs and volumes for credentials and private recruiter conversation content.

Expected: every acceptance criterion in `docs/superpowers/specs/2026-08-14-telegram-job-bot-design.md` passes before any real vacancy channel is added.

- [ ] **Step 10: Commit staging documentation updates**

Record only non-secret operational corrections discovered during staging in `README.md`, then run the full verification suite again and commit:

```powershell
git add -- README.md
git commit -m "docs: record verified deployment procedure"
```
