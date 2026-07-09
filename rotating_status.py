import json
import discord
from discord.ext import commands, tasks

CONFIG_PATH = "status_config.json"

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

    @tasks.loop(seconds=30)
    async def rotate(self):
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
        activity_type = ACTIVITY_TYPES.get(entry.get("type", "playing").lower(), discord.ActivityType.playing)
        activity = discord.Activity(type=activity_type, name=entry.get("text", ""))

        try:
            await self.bot.change_presence(activity=activity)
        except Exception as e:
            print(f"❌ Failed to update status: {e}")

        self.index += 1

    @rotate.before_loop
    async def before_rotate(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(RotatingStatus(bot))
