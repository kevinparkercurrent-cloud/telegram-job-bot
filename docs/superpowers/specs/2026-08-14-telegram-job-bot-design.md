# Telegram Job Bot — Design Specification

**Date:** 2026-08-14  
**Status:** approved in conversation; awaiting review of the written specification  
**Owner:** single-user deployment for the candidate described by `candidate-profile.full.json`

## 1. Objective

Build a single-user service that monitors up to 20 explicitly allowed Telegram vacancy channels, filters posts against the candidate profile, immediately reports strong matches, groups borderline matches into two daily digests, prepares a truthful response draft, and sends a Telegram response only after explicit user approval.

The MVP sends responses only to Telegram contacts. Email addresses and external application forms are shown to the user but are never submitted automatically.

## 2. Candidate and Search Policy

The canonical candidate facts come from the supplied `candidate-profile.full.json`. The bot must not infer or invent experience beyond that profile.

Target tracks:

1. Product Manager / Technical Project Manager, with emphasis on mobile and web delivery.
2. Affiliate / iGaming Project Manager.

Search preferences:

- remote work available from Vietnam;
- relocation to Southeast Asia;
- as a fallback, office or hybrid work in Saint Petersburg;
- no industry exclusions;
- salary floor: 100,000 RUB per month, whether gross or net;
- a missing salary does not reject or penalize a vacancy;
- a stated lower bound below 100,000 RUB is a hard rejection;
- salary in another currency is converted using a documented daily exchange-rate source;
- a requirement for English above confirmed B1 caps the vacancy at borderline status and adds a warning.

## 3. Compliance Boundary

The Telegram user account is accessed through the official Telegram API protocol using the deployment's own `api_id` and `api_hash`. Only channels on the allowlist are processed.

Telegram content is not supplied to an AI or ML system. Telegram-only vacancies are parsed, scored, and drafted with deterministic rules and verified templates.

When a Telegram post contains a link to an original vacancy outside Telegram, the source adapter may retrieve that page only when the source's access rules permit it. AI may analyze the content retrieved directly from that external source. If access is forbidden, blocked, ambiguous, or technically unavailable, the bot falls back to deterministic processing and does not attempt to bypass the restriction.

No automatic action is taken on the user's behalf without their knowledge. Every outgoing response requires an explicit, vacancy-specific confirmation.

## 4. Deployment Architecture

The service runs continuously on the existing VPS in the Czech Republic. A Russian-hosted VPS is not used because direct Telegram connectivity and the selected AI provider must remain available without a VPN or proxy.

The MVP is one Dockerized Python application with focused internal modules:

- **Telegram collector:** authenticated as the dedicated user account; reads allowlisted channels and sends approved recruiter messages.
- **Control bot:** a separate Bot API bot that exposes cards, digests, settings, and confirmation actions only to the configured administrator Telegram ID.
- **Normalizer and extractor:** canonicalizes posts and extracts title, company, salary, work format, location, language requirements, contact, and external links.
- **Rule engine:** applies hard constraints and produces a deterministic score with human-readable reasons.
- **External-source adapter:** retrieves permitted source pages with timeouts, size limits, content-type checks, and domain-specific safeguards.
- **AI adapter:** analyzes only permitted external-source content and produces structured match evidence and a draft grounded in the candidate profile.
- **Template drafter:** produces Telegram-only drafts from verified profile facts without AI.
- **Scheduler:** emits immediate notifications and daily digests.
- **Persistence layer:** SQLite in WAL mode with migrations and backups.

At this scale, a message broker and PostgreSQL would add operational complexity without a current need. Module interfaces must allow replacing SQLite or splitting workers later.

## 5. Data Flow

1. The collector receives a new channel post.
2. It rejects events from channels not present in the allowlist.
3. The normalizer calculates a stable fingerprint, extracts structured fields, and eliminates duplicates and forwarded copies already seen.
4. The rule engine applies hard constraints and calculates a score from 0 to 100.
5. If a permitted external source is available, the external-source and AI adapters enrich the assessment and prepare a grounded draft. Otherwise, the template drafter creates the response.
6. The result is classified:
   - `strong`: score 75–100, delivered immediately;
   - `borderline`: score 50–74, placed in the next digest;
   - `weak`: score 0–49, retained in history without notification;
   - `rejected`: a hard constraint failed.
7. English above B1 can never result in `strong`, regardless of the numerical score.
8. The control bot presents the vacancy card and response draft.
9. `Approve` creates a single-use send authorization tied to the vacancy, recipient, and exact draft hash.
10. Immediately before sending, the service rechecks authorization, recipient, rate limits, prior-send state, and draft hash.
11. The user account sends one private Telegram message and records the outcome transactionally.

Scoring weights and thresholds are configuration, not hard-coded business logic. Initial weights prioritize role fit, relevant responsibilities, work format/location, verified skills, and compensation evidence.

## 6. User Interface

The control bot supports:

- `/channels` — list, add, and remove allowlisted channels;
- `/settings` — score thresholds, timezone, digest times, and operational limits;
- `/queue` — vacancies awaiting a decision;
- `/history` — sent, skipped, rejected, and failed vacancies;
- `/status` — user-session, collector, scheduler, source adapter, and AI health.

A vacancy card contains:

- title, company, salary, location, and format when known;
- match class and score;
- concise positive evidence and warnings;
- source-channel link and external-source link when present;
- extracted recruiter contact and response draft;
- an explicit label showing whether the assessment/draft is rule-based or externally AI-enriched.

Actions:

- `Approve` — send the exact displayed draft once;
- `Edit` — replace the draft and require a new confirmation;
- `Skip` — leave the vacancy without marking it irrelevant;
- `Not relevant` — record negative feedback for later tuning;
- `Open source` — open the source without changing state.

Default digests run at 12:00 and 19:00 in a configurable timezone.

## 7. Send Safety and Rate Limits

- Only the configured administrator Telegram ID can use control actions.
- One confirmation authorizes one recipient, one vacancy, and one exact message.
- Editing invalidates all earlier authorizations.
- Confirmations expire after seven days.
- The initial limits are 5 recruiter messages per hour and 15 per day.
- The service never sends recruiter responses to channels, groups, or bots.
- Missing or ambiguous contacts prevent sending and leave the vacancy available for manual handling.
- An ambiguous timeout never triggers an automatic resend; the user is shown a reconciliation warning.
- A successfully sent vacancy can never be sent again unless a future, explicit product feature introduces a separately audited follow-up workflow.

## 8. Persistence and Data Minimization

SQLite stores:

- allowlisted channel IDs and display names;
- vacancy fingerprints, structured facts, links, classification, and processing status;
- draft versions and their hashes;
- approval and send audit records;
- user feedback and configuration;
- external-source retrieval metadata and AI result metadata.

Raw Telegram post text is retained only as a short-lived processing cache and removed after 30 days. Long-term history retains the fingerprint, source link, extracted facts, decision, and send outcome. Private conversations with recruiters are not ingested or mirrored into the database.

The database and Telegram session use a persistent server volume. Daily encrypted backups are retained for seven days. Logs redact message content, credentials, session material, phone numbers, email addresses, and AI keys.

## 9. Secrets and Server Access

The password disclosed during design must be rotated before any connection is made. It must not be copied into the repository, specification, deployment configuration, or logs.

Deployment uses a dedicated SSH key. After key access is verified:

- direct password login for `root` is disabled;
- the application runs as an unprivileged system user;
- secrets are injected through a root-readable environment file outside Git;
- the container filesystem is read-only except for explicit data and temporary volumes;
- firewall rules expose only SSH and any endpoint strictly required by the chosen update mode;
- SSH, Telegram-session, Bot API, and AI credentials can be rotated independently.

Webhook exposure is unnecessary for the MVP; long polling avoids a public application port.

## 10. Error Handling and Recovery

- **Telegram session revoked or 2FA required:** pause collection and sending, retain the queue, and alert the administrator.
- **Telegram flood/rate error:** honor the server-provided wait period and pause outgoing sends; never loop aggressively.
- **Network loss:** reconnect with bounded exponential backoff and jitter.
- **Unavailable external source:** fall back to deterministic processing and mark the card accordingly.
- **Malformed or ungrounded AI result:** reject the enrichment, use the template draft, and record a validation error.
- **Database busy or unavailable:** stop message processing rather than acknowledge work that cannot be recorded.
- **Control-bot outage:** continue collecting into the queue, but do not send recruiter messages.
- **Process restart:** resume idempotently from durable state; already processed events and sent responses remain deduplicated.

Operational alerts are rate-limited and grouped to avoid flooding the administrator.

## 11. Testing Strategy

### Unit tests

- allowlist enforcement;
- salary parsing across ranges, currencies, gross/net labels, and missing values;
- role, location, format, and English-level rules;
- scoring boundaries and the B1 cap;
- deterministic deduplication;
- template grounding against the candidate profile;
- approval hashing, expiration, invalidation, and single use;
- hourly and daily send limits;
- secret and personal-data log redaction.

### Integration tests

- synthetic Telegram events through a fake client adapter;
- control-bot callbacks with authorized and unauthorized user IDs;
- external-source success, redirect, timeout, oversized body, forbidden domain, and invalid content;
- structured AI response validation and template fallback;
- SQLite restart recovery and concurrent event handling;
- ambiguous send timeout without automatic retry.

### Staging verification

Before production channel monitoring:

1. Use a private test channel and a non-recruiter test recipient.
2. Verify strong, borderline, weak, and rejected examples.
3. Verify both digest schedules.
4. Verify that no response can be sent without a fresh confirmation.
5. Revoke and restore the Telegram session to verify safe pausing.
6. Restart the container during processing and verify idempotent recovery.
7. Inspect the repository, container environment, database, backups, and logs for leaked secrets.

## 12. MVP Acceptance Criteria

The MVP is complete when:

- only the allowlisted channels are processed;
- duplicates produce one vacancy record and at most one card;
- strong matches arrive immediately and borderline matches appear in scheduled digests;
- explicit salaries below 100,000 RUB are rejected, while missing salaries remain eligible;
- English requirements above B1 produce a warning and cannot become strong matches;
- Telegram-only posts never reach an AI system;
- AI enrichment occurs only from a permitted external source and only uses verified candidate facts;
- every outgoing recruiter message has a recorded, vacancy-specific user confirmation;
- duplicate or unauthorized sends are prevented across retries and restarts;
- session, network, source, AI, and database failures degrade safely and notify the administrator;
- no production credential or disclosed password exists in Git, logs, database fields, or backups.

## 13. Explicitly Deferred Scope

- automatic email sending;
- automatic external-form completion;
- responses without user confirmation;
- multi-user accounts and billing;
- autonomous follow-up messages;
- AI analysis of Telegram content;
- dashboard website;
- adaptive ML training from user feedback.

Feedback is retained only for manual rule tuning in the MVP.
