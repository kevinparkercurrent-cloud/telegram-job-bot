# Channel Management from the Control Bot

## Goal

Allow the owner to add and remove monitored Telegram channels directly from the control bot without finding internal Telegram channel IDs or using the server console.

The feature supports public channels and private channels, raises the allowlist limit from 20 to 100, and keeps channel membership on the dedicated Telegram reader account synchronized with the allowlist.

## Access and scope

- Only the configured control-bot administrator may manage channels.
- The feature accepts Telegram broadcast channels only. Groups, supergroups, users, and bots are rejected.
- Public channels may be supplied as `@username`, `t.me/username`, or `https://t.me/username`.
- Private channels may be supplied with a Telegram invite link.
- Invite links are used only to join and resolve the channel. They are never persisted or included in logs or bot responses.
- The allowlist contains at most 100 channels.

## User interface

The `/channels` command opens an inline channel-management menu. It shows:

- the current count, such as `20/100`;
- up to 10 channels per page;
- previous and next page controls when required;
- `Добавить канал` and `Удалить канал` actions.

### Adding a channel

1. The owner presses `Добавить канал`.
2. The bot asks for an `@username`, public link, or private invite link.
3. The bot waits up to 15 minutes for one text message. `/cancel` cancels the operation. Commands and non-text messages do not become channel input.
4. The dedicated Telegram reader account resolves the reference and joins the channel when necessary.
5. The service verifies that the resolved entity is a broadcast channel.
6. The database stores the channel ID, resolved display title, and public username when available. It does not store the submitted invite link.
7. The bot confirms the addition and displays the updated channel menu.

Adding an already allowlisted channel is idempotent: the stored title and public username may be refreshed, but no duplicate is created and the channel count does not increase.

### Removing a channel

1. The owner presses `Удалить канал`.
2. The bot displays a paginated channel-selection menu.
3. After the owner selects a channel, the bot asks for confirmation and warns that rejoining a private channel may require a new invite link.
4. On confirmation, the service removes the channel from the allowlist and makes the dedicated Telegram reader account leave it.
5. If leaving Telegram fails after the database change, the bot reports that monitoring has stopped but the reader account may still be subscribed. This avoids continuing to process a channel the owner intended to remove.

Cancellation leaves both membership and the allowlist unchanged.

## Compatibility commands

The interactive menu is the primary interface. Text commands remain available as a fallback:

- `/channels add <reference>` accepts the same public username, public link, or private invite link as the menu.
- `/channels remove <channel_id>` performs the same confirmed removal flow rather than deleting immediately.
- `/channels` opens the interactive menu.

The old numeric-ID add syntax is no longer the documented workflow. Existing stored channels remain valid without migration by the owner.

## Components and boundaries

### Control bot runtime

The aiogram runtime owns temporary interaction state for add and remove flows. Pending input is keyed by the administrator user ID and expires after 15 minutes. A service restart safely cancels unfinished interactions.

The runtime renders menus and translates button presses or text input into channel-management service calls. It does not call Telethon or mutate the database directly.

### Channel-management service

A dedicated service coordinates Telegram membership and allowlist persistence. It exposes operations for:

- resolving and joining a submitted channel reference;
- listing paginated stored channels;
- preparing and confirming removal;
- leaving a stored channel and removing it from the allowlist.

This keeps Telethon-specific behavior out of command parsing and makes failure behavior independently testable.

### Telegram reader adapter

The Telethon adapter gains explicit channel-membership operations:

- join or resolve a public channel;
- import a private invite;
- validate the resolved entity as a broadcast channel;
- leave a channel by stored ID.

Telegram exceptions are translated into stable application errors. Submitted invite links and hashes must not appear in logs.

### Database

The `channels` table remains the source of truth for monitoring. It stores:

- Telegram channel ID;
- display label;
- nullable public username;
- creation timestamp.

The schema migration is additive and idempotent. The database enforces the 100-channel limit while allowing an existing channel to be refreshed at capacity.

## Data flow and consistency

For addition, Telegram joining and validation happen before persistence. If Telegram rejects the reference, the database is unchanged. If persistence fails after joining, the service attempts to leave the newly joined channel and reports the failure.

For removal, the allowlist entry is deleted before the leave request so monitoring stops immediately. The subsequent leave request is best effort and its failure is reported clearly.

The collector continues to consult the database allowlist for every incoming post, so changes take effect without restarting the service.

## Error messages

The owner receives concise Russian messages for:

- invalid or unsupported reference;
- expired, revoked, or already-used invite;
- entity is not a broadcast channel;
- Telegram access or rate-limit failure;
- allowlist capacity reached;
- channel is already present;
- channel no longer exists or cannot be left;
- expired or cancelled interaction.

Internal exception details and secret invite material are not shown.

## Testing

Automated tests cover:

- accepted public reference formats;
- private invite links;
- rejection of groups, users, malformed links, and unsupported hosts;
- idempotent duplicate addition;
- the 100-channel boundary;
- invite-link non-persistence and redaction from errors;
- add and remove state transitions, cancellation, and 15-minute expiry;
- pagination at 0, 10, 11, and 100 channels;
- admin-only authorization;
- rollback attempt when persistence fails after joining;
- immediate allowlist removal and best-effort leave behavior;
- backward compatibility for existing channel rows and fallback commands.

Deployment verification checks the schema migration, channel count, control-bot health, and one owner-authorized public test-channel add/remove cycle before production use.
