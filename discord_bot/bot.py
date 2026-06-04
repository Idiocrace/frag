"""Frag premium-token Discord bot.

Runs in a context where every Discord member is already a verified Patreon
supporter (e.g. a paywalled Discord server, or a channel bridged from a
Patreon-members-only space).  Members can request their personal +50 GiB
``fragsup_…`` token via ``/token`` and manage it via ``/release``,
``/mytoken``.  Admins can revoke via ``/revoke``.

Configuration
-------------
The bot's Discord credentials live in the **obfuscated server config**
(``frag/server/out/config.py`` + ``.secrets``) under three keys:

- ``bot-token``      — the bot's Discord token (required).
- ``guild-id``       — optional.  Restrict slash commands to one guild
                       and sync there for instant availability instead
                       of waiting up to an hour for global propagation.
                       Use ``0`` to disable.
- ``admin-role-id``  — optional.  Discord role id allowed to run
                       ``/revoke``.  Without it, ``/revoke`` falls
                       back to the Administrator permission.  Use ``0``
                       to disable.

Regenerate the obfuscated config after editing
``frag/server/generate_config.py``::

    cd frag/server
    py generate_config.py

These env vars override the obfuscated config when set (useful for dev):

- ``FRAG_BOT_DISCORD_TOKEN``
- ``FRAG_BOT_GUILD_ID``
- ``FRAG_BOT_ADMIN_ROLE_ID``

Filesystem paths:

- ``FRAG_SUPPORTER_TOKENS`` — path to the supporter-token JSON store
                              the frag server reads.  Defaults to
                              ``../server/userdata/.supporter_tokens.json``.
- ``FRAG_BOT_DATA_DIR``     — where the bot keeps its own user mapping
                              file.  Defaults to ``./data``.

Run::

    cd frag/discord_bot
    pip install -r requirements.txt
    py bot.py
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands

HERE = Path(__file__).resolve().parent

# Let us import gen_supporter_token / supporter_tokens from the sibling
# server directory without packaging gymnastics.
sys.path.insert(0, str(HERE.parent / "server"))

from gen_supporter_token import _default_store_path  # noqa: E402
from supporter_tokens import SupporterTokenStore  # noqa: E402
import config  # noqa: E402

from user_tokens import UserTokenMap  # noqa: E402


log = logging.getLogger("frag.bot")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _store_path() -> Path:
    """Path to the supporter-token JSON used by the frag server."""
    env = os.environ.get("FRAG_SUPPORTER_TOKENS")
    if env:
        return Path(env)
    return _default_store_path()


def _bot_data_dir() -> Path:
    env = os.environ.get("FRAG_BOT_DATA_DIR")
    return Path(env) if env else HERE / "data"


def _guild_obj() -> Optional[discord.Object]:
    return (
        discord.Object(id=int(config.WDDM0cy3KQsGp3O3["lt4XyZSo43D2tenV"]))
        if config.WDDM0cy3KQsGp3O3["lt4XyZSo43D2tenV"]
        else None
    )


def _admin_role_id() -> Optional[int]:
    return (
        int(config.WDDM0cy3KQsGp3O3["ejnuymoWvnVLxYR3"])
        if config.WDDM0cy3KQsGp3O3["ejnuymoWvnVLxYR3"]
        else None
    )


# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------


class FragBot(discord.Client):
    def __init__(
        self,
        store: SupporterTokenStore,
        user_map: UserTokenMap,
        guild: Optional[discord.Object],
    ):
        intents = discord.Intents.default()
        # We don't need message_content or members intent for slash commands.
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.store = store
        self.user_map = user_map
        self.guild = guild

    async def setup_hook(self) -> None:
        if self.guild is not None:
            # Guild-scoped sync is instant; global takes up to an hour.
            self.tree.copy_global_to(guild=self.guild)
            await self.tree.sync(guild=self.guild)
        else:
            await self.tree.sync()


def _is_admin(interaction: discord.Interaction, admin_role_id: Optional[int]) -> bool:
    user = interaction.user
    if isinstance(user, discord.Member):
        if admin_role_id is not None and any(r.id == admin_role_id for r in user.roles):
            return True
        if user.guild_permissions.administrator:
            return True
    return False


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not config.WDDM0cy3KQsGp3O3["qbBQCFaEjaDWdPpA"]:
        print(
            "Discord token is empty. Set 'bot-token' in "
            "frag/server/generate_config.py and rerun it.",
            file=sys.stderr,
        )
        return 2

    store_path = _store_path()
    bot_data = _bot_data_dir()
    bot_data.mkdir(parents=True, exist_ok=True)

    store = SupporterTokenStore(store_path)
    user_map = UserTokenMap(bot_data / "discord_user_tokens.json")

    log.info("Supporter token store: %s", store_path)
    log.info("Discord user map     : %s", bot_data / "discord_user_tokens.json")

    bot = FragBot(store=store, user_map=user_map, guild=_guild_obj())
    admin_role_id = _admin_role_id()

    # -----------------------------------------------------------------------
    # /token
    # -----------------------------------------------------------------------
    @bot.tree.command(
        name="token",
        description="Generate (or regenerate) your Frag Premium token.",
    )
    async def token_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)

        existing = user_map.get(interaction.user.id)
        if existing is not None:
            # Revoke the old token entirely (also frees any current claim
            # the user had against it on a frag account).
            try:
                store.revoke(existing.token)
            except Exception:
                # If the old token was already gone from the server store
                # we still want to issue a fresh one for the user.
                log.warning(
                    "revoke failed for old token of discord_id=%s; continuing",
                    interaction.user.id,
                )

        # Use the bot's already-instantiated store so its in-memory view
        # stays consistent with the just-written file.  generate() from
        # the CLI script opens a *separate* store instance, which would
        # leave the bot's view stale until restart.
        new_token = store.issue(note=f"discord:{interaction.user.id}")
        user_map.set(
            interaction.user.id,
            new_token.token,
            note=str(interaction.user),
        )

        msg = (
            ("Your previous token was revoked.\n\n" if existing else "")
            + "Here's your Frag Premium token:\n"
            f"```\n{new_token.token}\n```\n"
            "Paste it into Frag → **Storage** → **Premium Token** to "
            "unlock the +50 GiB bonus.\n\n"
            "_Use `/release` to free it for use on another account, "
            "or `/mytoken` to see this token again._"
        )
        await interaction.followup.send(msg, ephemeral=True)

    # -----------------------------------------------------------------------
    # /mytoken
    # -----------------------------------------------------------------------
    @bot.tree.command(
        name="mytoken",
        description="Show the Frag Premium token already issued to you.",
    )
    async def mytoken_cmd(interaction: discord.Interaction) -> None:
        existing = user_map.get(interaction.user.id)
        if existing is None:
            await interaction.response.send_message(
                "You don't have a token yet — run `/token` to generate one.",
                ephemeral=True,
            )
            return

        # Confirm the token still exists on the server side (might have
        # been admin-revoked).  If not, prompt for a fresh /token.
        if store.get(existing.token) is None:
            user_map.remove(interaction.user.id)
            await interaction.response.send_message(
                "Your previous token was revoked.  Run `/token` to get a new one.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"Your Frag Premium token:\n```\n{existing.token}\n```",
            ephemeral=True,
        )

    # -----------------------------------------------------------------------
    # /release
    # -----------------------------------------------------------------------
    @bot.tree.command(
        name="release",
        description="Release your token so you can redeem it on another account.",
    )
    async def release_cmd(interaction: discord.Interaction) -> None:
        existing = user_map.get(interaction.user.id)
        if existing is None:
            await interaction.response.send_message(
                "You don't have a token yet — run `/token` to generate one.",
                ephemeral=True,
            )
            return

        freed = store.force_release(existing.token)
        if freed:
            msg = (
                "Released your token.  It's no longer bound to that Frag "
                "account — you can redeem it on a different one now."
            )
        else:
            msg = (
                "Your token wasn't held by any Frag account, so there was "
                "nothing to release."
            )
        await interaction.response.send_message(msg, ephemeral=True)

    # -----------------------------------------------------------------------
    # /revoke @user  (admin only)
    # -----------------------------------------------------------------------
    @bot.tree.command(
        name="revoke",
        description="Admin: invalidate a member's Frag Premium token.",
    )
    @app_commands.describe(member="The Discord user whose token to revoke.")
    async def revoke_cmd(
        interaction: discord.Interaction,
        member: discord.Member,
    ) -> None:
        if not _is_admin(interaction, admin_role_id):
            await interaction.response.send_message(
                "You don't have permission to use this command.",
                ephemeral=True,
            )
            return

        existing = user_map.get(member.id)
        if existing is None:
            await interaction.response.send_message(
                f"{member.mention} doesn't currently have a token.",
                ephemeral=True,
            )
            return

        try:
            store.revoke(existing.token)
        except Exception as e:
            log.warning(
                "store.revoke failed for discord_id=%s: %s",
                member.id,
                e,
            )
        user_map.remove(member.id)
        await interaction.response.send_message(
            f"Revoked the token previously issued to {member.mention}.",
            ephemeral=True,
        )

    # -----------------------------------------------------------------------
    # ready event
    # -----------------------------------------------------------------------
    @bot.event
    async def on_ready() -> None:
        log.info("Logged in as %s (id=%s)", bot.user, bot.user.id if bot.user else "?")

    bot.run(config.WDDM0cy3KQsGp3O3["qbBQCFaEjaDWdPpA"], log_handler=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
