import json
import os
import discord
from datetime import datetime, timedelta, timezone
from discord.ext import commands, tasks

import json_store

CONFIG_PATH = "status_config.json"
TEMP_STATUS_FILE = os.path.join("data", "temp_status.json")

ACTIVITY_TYPES = {
    "playing": discord.ActivityType.playing,
    "watching": discord.ActivityType.watching,
    "listening": discord.ActivityType.listening,
    "competing": discord.ActivityType.competing,
}


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


class RotatingStatus(commands.Cog):
    """Cycles the bot's activity status through a list defined in status_config.json."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.index = 0
        self.rotate.start()

    def cog_unload(self):
        self.rotate.cancel()

    def _get_active_temp_status(self):
        """Returns the temp status entry if one is set and not yet expired, clearing it if expired."""
        temp = json_store.load_json(TEMP_STATUS_FILE, None)
        if not temp:
            return None
        expires_at = datetime.fromisoformat(temp["expires_at"])
        if datetime.now(timezone.utc) >= expires_at:
            json_store.save_json(TEMP_STATUS_FILE, None)
            return None
        return temp

    @tasks.loop(seconds=30)
    async def rotate(self):
        temp = self._get_active_temp_status()
        if temp:
            entry = temp
        else:
            try:
                config = load_config()
            except (FileNotFoundError, json.JSONDecodeError) as e:
                print(f"⚠️ Could not load status_config.json: {e}")
                return

            statuses = config.get("statuses", [])
            if not statuses:
                return

            # Keep the loop interval in sync with the config in case it was edited
            desired_interval = config.get("rotate_seconds", 30)
            if self.rotate.seconds != desired_interval:
                self.rotate.change_interval(seconds=desired_interval)

            entry = statuses[self.index % len(statuses)]
            self.index += 1

        activity_type = ACTIVITY_TYPES.get(entry.get("type", "playing").lower(), discord.ActivityType.playing)
        activity = discord.Activity(type=activity_type, name=entry.get("text", ""))

        try:
            await self.bot.change_presence(activity=activity)
        except Exception as e:
            print(f"❌ Failed to update status: {e}")

    @rotate.before_loop
    async def before_rotate(self):
        await self.bot.wait_until_ready()

    @discord.app_commands.command(name="set-temp-status", description="Set a temporary bot status for a set number of minutes (admin only)")
    @discord.app_commands.describe(text="The status text to show", minutes="How many minutes it should last", type="Activity type (defaults to Playing)")
    @discord.app_commands.choices(type=[
        discord.app_commands.Choice(name="Playing", value="playing"),
        discord.app_commands.Choice(name="Watching", value="watching"),
        discord.app_commands.Choice(name="Listening", value="listening"),
        discord.app_commands.Choice(name="Competing", value="competing"),
    ])
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def set_temp_status(
        self, interaction: discord.Interaction, text: str, minutes: int,
        type: discord.app_commands.Choice[str] = None
    ):
        if minutes < 1:
            await interaction.response.send_message("Minutes must be a positive number.", ephemeral=True)
            return

        expires_at = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        json_store.save_json(TEMP_STATUS_FILE, {
            "text": text,
            "type": type.value if type else "playing",
            "expires_at": expires_at.isoformat(),
        })
        await interaction.response.send_message(
            f"✅ Temporary status set to **{text}** for {minutes} minute(s).", ephemeral=True
        )

    @set_temp_status.error
    async def set_temp_status_error(self, interaction: discord.Interaction, error):
        if isinstance(error, discord.app_commands.MissingPermissions):
            await interaction.response.send_message("You need Manage Server permission to use this.", ephemeral=True)
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(RotatingStatus(bot))
