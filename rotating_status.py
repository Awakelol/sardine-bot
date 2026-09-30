import logging
import os
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

import json_store

log = logging.getLogger(__name__)

CONFIG_PATH = "status_config.json"
TEMP_STATUS_FILE = os.path.join("data", "temp_status.json")

ACTIVITY_TYPES = {
    "playing": discord.ActivityType.playing,
    "watching": discord.ActivityType.watching,
    "listening": discord.ActivityType.listening,
    "competing": discord.ActivityType.competing,
}


def make_activity(entry: dict) -> discord.Activity:
    activity_type = ACTIVITY_TYPES.get(entry.get("type", "playing").lower(), discord.ActivityType.playing)
    return discord.Activity(type=activity_type, name=entry.get("text", ""))


class RotatingStatus(commands.Cog):
    """Cycles the bot's status through the list in status_config.json."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.index = 0
        self.rotate.start()

    def cog_unload(self):
        self.rotate.cancel()

    def _active_temp_status(self) -> dict | None:
        temp = json_store.load_json(TEMP_STATUS_FILE, None)
        if not temp:
            return None
        if datetime.now(timezone.utc) >= datetime.fromisoformat(temp["expires_at"]):
            json_store.save_json(TEMP_STATUS_FILE, None)
            return None
        return temp

    @tasks.loop(seconds=30)
    async def rotate(self):
        entry = self._active_temp_status()
        if entry is None:
            config = json_store.load_json(json_store.config_path(CONFIG_PATH), {})
            statuses = config.get("statuses")
            if not statuses:
                return

            # pick up rotate_seconds changes without needing a restart
            interval = config.get("rotate_seconds", 30)
            if self.rotate.seconds != interval:
                self.rotate.change_interval(seconds=interval)

            entry = statuses[self.index % len(statuses)]
            self.index += 1

        try:
            await self.bot.change_presence(activity=make_activity(entry))
        except Exception as e:
            log.warning("Failed to update status: %s", e)

    @rotate.before_loop
    async def before_rotate(self):
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="set-temp-status", description="Set a temporary bot status for a set number of minutes (admin only)"
    )
    @app_commands.describe(
        text="The status text to show",
        minutes="How many minutes it should last",
        type="Activity type (defaults to Playing)",
    )
    @app_commands.choices(
        type=[
            app_commands.Choice(name="Playing", value="playing"),
            app_commands.Choice(name="Watching", value="watching"),
            app_commands.Choice(name="Listening", value="listening"),
            app_commands.Choice(name="Competing", value="competing"),
        ]
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def set_temp_status(
        self,
        interaction: discord.Interaction,
        text: str,
        minutes: int,
        type: app_commands.Choice[str] = None,
    ):
        if minutes < 1:
            await interaction.response.send_message("Minutes must be a positive number.", ephemeral=True)
            return

        entry = {
            "text": text,
            "type": type.value if type else "playing",
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat(),
        }
        json_store.save_json(TEMP_STATUS_FILE, entry)
        await interaction.response.send_message(
            f"✅ Temporary status set to **{text}** for {minutes} minute(s).", ephemeral=True
        )
        # show it right away instead of waiting for the next rotation tick
        await self.bot.change_presence(activity=make_activity(entry))


async def setup(bot: commands.Bot):
    await bot.add_cog(RotatingStatus(bot))
