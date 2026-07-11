import discord
from discord.ext import commands, tasks

import log_utils

ROLE_NAME = "super duper cool role for cool people only"
RESYNC_INTERVAL_HOURS = 6


def has_tag_equipped(member: discord.Member) -> bool:
    tag = member.primary_guild
    return bool(tag and tag.id == member.guild.id and tag.identity_enabled)


class GuildTagRole(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.periodic_resync.start()

    def cog_unload(self):
        self.periodic_resync.cancel()

    async def _sync_member(self, member: discord.Member):
        role = discord.utils.get(member.guild.roles, name=ROLE_NAME)
        if role is None:
            return

        equipped = has_tag_equipped(member)
        try:
            if equipped and role not in member.roles:
                await member.add_roles(role, reason="Equipped server tag")
                await log_utils.send_log(self.bot, member.guild, f"🏷️ {member.mention} equipped the server tag — gave **{ROLE_NAME}**")
            elif not equipped and role in member.roles:
                await member.remove_roles(role, reason="Removed server tag")
                await log_utils.send_log(self.bot, member.guild, f"🏷️ {member.mention} removed the server tag — took away **{ROLE_NAME}**")
        except discord.Forbidden:
            print(f"⚠️ Missing permissions to sync tag role for {member}.")

    async def sync_all_guilds(self):
        for guild in self.bot.guilds:
            role = discord.utils.get(guild.roles, name=ROLE_NAME)
            if role is None:
                print(f"⚠️ '{ROLE_NAME}' role not found in {guild.name}, skipping tag sync.")
                continue
            for member in guild.members:
                await self._sync_member(member)

    @commands.Cog.listener()
    async def on_user_update(self, before: discord.User, after: discord.User):
        for guild in self.bot.guilds:
            member = guild.get_member(after.id)
            if member is not None:
                await self._sync_member(member)

    @tasks.loop(hours=RESYNC_INTERVAL_HOURS)
    async def periodic_resync(self):
        # Runs once immediately on startup (catching anyone who changed tags while the bot
        # was offline), then every RESYNC_INTERVAL_HOURS as a safety net for missed events
        # (gateway hiccups, manual role edits, cache desync).
        await self.sync_all_guilds()

    @periodic_resync.before_loop
    async def before_periodic_resync(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(GuildTagRole(bot))
