import logging
import os

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from role_menu import RoleButtonView, RolePickerView

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
UNVERIFIED_ROLE_NAME = os.getenv("UNVERIFIED_ROLE_NAME") or "Unverified"

EXTENSIONS = [
    # voice
    "voice_persistence",
    "voice_stats",
    # community
    "rotating_status",
    "lfg",
    "ai_chat",
    "easter_eggs",
    # roles / logging
    "log_channel",
    "guild_tag_role",
]

# has_permissions reports API names, some of which differ from what the Discord UI calls them
PERMISSION_NAMES = {"manage_guild": "Manage Server"}

log = logging.getLogger("bot")


class SardineBot(commands.Bot):
    async def setup_hook(self):
        # persistent views, so the role menu buttons keep working after a restart
        self.add_view(RoleButtonView())
        self.add_view(RolePickerView())

        for ext in EXTENSIONS:
            await self.load_extension(ext)

        try:
            synced = await self.tree.sync()
            log.info("Synced %d slash command(s)", len(synced))
        except discord.HTTPException as e:
            log.error("Failed to sync commands: %s", e)


intents = discord.Intents.default()
intents.members = True  # member joins, member cache, role/user updates
intents.message_content = True  # ai_chat and easter_eggs read message text

bot = SardineBot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    log.info("Logged in as %s (%s)", bot.user, bot.user.id)


@bot.event
async def on_member_join(member: discord.Member):
    role = discord.utils.get(member.guild.roles, name=UNVERIFIED_ROLE_NAME)
    if role is None:
        log.warning("No %s role in %s, couldn't assign it to %s", UNVERIFIED_ROLE_NAME, member.guild.name, member)
        return
    try:
        await member.add_roles(role, reason="New member, pending captcha verification")
    except discord.Forbidden:
        log.warning("Missing permissions to give %s the %s role", member, UNVERIFIED_ROLE_NAME)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        perms = ", ".join(PERMISSION_NAMES.get(p, p.replace("_", " ").title()) for p in error.missing_permissions)
        await interaction.response.send_message(f"You need {perms} permission to use this.", ephemeral=True)
        return
    name = interaction.command.name if interaction.command else "unknown"
    log.error("Unhandled error in /%s", name, exc_info=error)
    if not interaction.response.is_done():
        try:
            await interaction.response.send_message("Something went wrong running that command.", ephemeral=True)
        except discord.HTTPException:
            pass  # interaction already expired


@bot.tree.command(name="ping", description="Check if the bot is responsive")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("🏓 Pong! I'm online.")


@bot.tree.command(name="post-role-menu", description="Post the role picker button (admin only)")
@app_commands.checks.has_permissions(manage_roles=True)
async def post_role_menu(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Role Manager",
        description="Click the button below to choose your roles for Genre Interests, Game Update pings, and Server Pings to access different parts of the server!",
        color=discord.Color.blurple(),
    )
    await interaction.channel.send(embed=embed, view=RoleButtonView())
    await interaction.response.send_message("Role menu posted.", ephemeral=True)


if not TOKEN:
    raise SystemExit("DISCORD_TOKEN isn't set, check your .env")

bot.run(TOKEN, root_logger=True)
