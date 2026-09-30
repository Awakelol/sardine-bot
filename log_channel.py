import discord
from discord import app_commands
from discord.ext import commands

import log_utils


class LogChannel(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="setup-log-channel", description="Set where the bot posts role/log updates (admin only)")
    @app_commands.describe(channel="Channel to use (defaults to the channel you run this in)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup_log_channel(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        target = channel or interaction.channel
        log_utils.set_log_channel(interaction.guild.id, target.id)
        await interaction.response.send_message(f"✅ Log channel set to {target.mention}.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(LogChannel(bot))
