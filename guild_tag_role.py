import logging
import os

import discord
from discord.ext import commands, tasks

import log_utils

log = logging.getLogger(__name__)

ROLE_NAME = os.getenv("TAG_ROLE_NAME") or "Server Tag"
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

    async def _sync_member(self, member: discord.Member, role: discord.Role):
        equipped = has_tag_equipped(member)
        try:
            if equipped and role not in member.roles:
                await member.add_roles(role, reason="Equipped server tag")
                await log_utils.send_log(
                    self.bot, member.guild, f"🏷️ {member.mention} equipped the server tag, gave **{role.name}**"
                )
            elif not equipped and role in member.roles:
                await member.remove_roles(role, reason="Removed server tag")
                await log_utils.send_log(
                    self.bot, member.guild, f"🏷️ {member.mention} removed the server tag, took away **{role.name}**"
                )
        except discord.Forbidden:
            log.warning("Missing permissions to sync the tag role for %s", member)

    @commands.Cog.listener()
    async def on_user_update(self, before: discord.User, after: discord.User):
        for guild in self.bot.guilds:
            member = guild.get_member(after.id)
            role = discord.utils.get(guild.roles, name=ROLE_NAME)
            if member is not None and role is not None:
                await self._sync_member(member, role)

    @tasks.loop(hours=RESYNC_INTERVAL_HOURS)
    async def periodic_resync(self):
        # The first run happens at startup and catches anyone who changed their tag while the
        # bot was offline. After that it's a safety net for missed gateway events.
        for guild in self.bot.guilds:
            role = discord.utils.get(guild.roles, name=ROLE_NAME)
            if role is None:
                log.warning("No '%s' role in %s, skipping tag sync", ROLE_NAME, guild.name)
                continue
            for member in guild.members:
                await self._sync_member(member, role)

    @periodic_resync.before_loop
    async def before_periodic_resync(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(GuildTagRole(bot))
