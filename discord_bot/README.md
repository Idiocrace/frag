# Frag premium-token Discord bot

Runs in a Discord context where **every member is already a verified Patreon
supporter** (paywalled server, Patreon-bridged channel, etc.).  Members
self-service their +50 GiB Frag Premium token via slash commands.

## Commands

| Command            | Who          | What it does                                                |
| ------------------ | ------------ | ----------------------------------------------------------- |
| `/token`           | any member   | Generates a fresh token.  If you already had one, the old one is revoked first. |
| `/mytoken`         | any member   | Shows the token already issued to you (ephemeral).          |
| `/release`         | any member   | Frees your token's current Frag-account claim so you can redeem it elsewhere. |
| `/revoke @member`  | admin only   | Invalidates a member's token (use when they lose access).   |

All responses are **ephemeral** — only the user who ran the command sees
the token.  Channels stay clean.

## Setup

```bash
cd frag/discord_bot
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Then set the required env var and run:

```bash
set FRAG_BOT_DISCORD_TOKEN=...           # bot token from the Discord dev portal
set FRAG_BOT_GUILD_ID=...                # optional: instant slash-command sync to one guild
set FRAG_BOT_ADMIN_ROLE_ID=...           # optional: role allowed to /revoke
set FRAG_SUPPORTER_TOKENS=...            # optional: override default token store path
py bot.py
```

## Bot permissions

When inviting the bot to a server, grant it:

- `applications.commands` (slash commands)
- `Send Messages` (responses)

No `Members Intent` or `Message Content Intent` needed.

## How it talks to the Frag server

The bot imports directly from `../server/supporter_tokens.py` and
`../server/gen_supporter_token.py`.  It writes to the same JSON file
(`.supporter_tokens.json` under `userdata/` by default) that the frag
server reads, so generated tokens are claimable the moment they're issued.

It also keeps its **own** mapping file (`data/discord_user_tokens.json`)
recording which Discord user got which token.  That file is gitignored
and should never be shared — leaking it leaks every issued token.
