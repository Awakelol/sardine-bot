import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
from role_menu import RoleButtonView, RolePickerView

# Load the token from .env
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# Set up intents (permissions the bot needs to "see" certain events)
intents = discord.Intents.default()
intents.members = True          # needed for welcome messages later
intents.message_content = True  # needed if we use text-based commands later

# Create the bot
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    print("Bot is online and ready.")

    # Re-register persistent views so buttons/menus keep working after a restart
    bot.add_view(RoleButtonView())
    bot.add_view(RolePickerView())

    # Load voice persistence cog (only once, on_ready can fire more than once on reconnects)
    if "voice_persistence" not in bot.extensions:
        await bot.load_extension("voice_persistence")
    if "voice_stats" not in bot.extensions:
        await bot.load_extension("voice_stats")
    if "rotating_status" not in bot.extensions:
        await bot.load_extension("rotating_status")
    if "lfg" not in bot.extensions:
        await bot.load_extension("lfg")
    if "ai_chat" not in bot.extensions:
        await bot.load_extension("ai_chat")

    # Sync slash commands with Discord
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s).")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

# A simple test slash command to confirm everything works end-to-end
@bot.tree.command(name="ping", description="Check if the bot is responsive")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("🏓 Pong! I'm online.")

# Admin command to post the role picker button in the current channel
@bot.tree.command(name="post-role-menu", description="Post the role picker button (admin only)")
@discord.app_commands.checks.has_permissions(manage_roles=True)
async def post_role_menu(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Role Picker",
        description="Click the button below to choose your Interests, Game Update pings, and Server Pings.",
        color=discord.Color.blurple()
    )
    await interaction.channel.send(embed=embed, view=RoleButtonView())
    await interaction.response.send_message("Role menu posted.", ephemeral=True)

@post_role_menu.error
async def post_role_menu_error(interaction: discord.Interaction, error):
    if isinstance(error, discord.app_commands.MissingPermissions):
        await interaction.response.send_message("You need Manage Roles permission to use this.", ephemeral=True)
    else:
        raise error

bot.run(TOKEN)
