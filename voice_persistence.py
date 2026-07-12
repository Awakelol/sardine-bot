import os
import asyncio
import discord
from datetime import datetime, timezone, timedelta
from discord.ext import commands, tasks

import json_store
from time_utils import format_duration

VOICE_CHANNEL_ID = os.getenv("VOICE_CHANNEL_ID")
VOICE_LOG_CHANNEL_ID = os.getenv("VOICE_LOG_CHANNEL_ID")
TIMER_FILE = os.path.join("data", "voice_timer.json")
MILESTONE_HOURS = 200


class VoicePersistence(commands.Cog):
    """Keeps the bot connected to a designated voice channel at all times,
    and tracks how long the current uninterrupted session has lasted."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.target_channel_id = int(VOICE_CHANNEL_ID) if VOICE_CHANNEL_ID else None
        self.log_channel_id = int(VOICE_LOG_CHANNEL_ID) if VOICE_LOG_CHANNEL_ID else None
        self.timer_data = json_store.load_json(TIMER_FILE, {"start_time": None})
        self.startup_announced = False
        self.watchdog.start()

    def cog_unload(self):
        self.watchdog.cancel()

    def _start_timer_if_needed(self) -> bool:
        """Returns True if a brand-new timer was started, False if one was already running."""
        if self.timer_data.get("start_time") is None:
            self.timer_data["start_time"] = datetime.now(timezone.utc).isoformat()
            json_store.save_json(TIMER_FILE, self.timer_data)
            return True
        return False

    def _get_elapsed_seconds(self) -> float:
        start_str = self.timer_data.get("start_time")
        if not start_str:
            return 0.0
        start = datetime.fromisoformat(start_str)
        return (datetime.now(timezone.utc) - start).total_seconds()

    def _reset_timer(self):
        self.timer_data["start_time"] = None
        json_store.save_json(TIMER_FILE, self.timer_data)

    async def announce_startup(self, started_fresh: bool):
        if self.startup_announced or not self.log_channel_id:
            return
        self.startup_announced = True

        channel = self.bot.get_channel(self.log_channel_id)
        if channel is None:
            return

        if started_fresh:
            embed = discord.Embed(
                title="🔊 Bot Online — New Session Started",
                description="Voice timer starting fresh from 0m.",
                color=discord.Color.blurple()
            )
        else:
            duration_str = format_duration(self._get_elapsed_seconds())
            embed = discord.Embed(
                title="🔊 Bot Online — Session Resumed",
                description=f"Reconnected after a restart. Timer continuing at **{duration_str}**.",
                color=discord.Color.blurple()
            )
        try:
            await channel.send(embed=embed)
        except Exception as e:
            print(f"❌ Failed to send startup log: {e}")

    async def log_disconnect(self):
        if not self.log_channel_id:
            print("⚠️ VOICE_LOG_CHANNEL_ID not set — skipping disconnect log.")
            return

        elapsed_seconds = self._get_elapsed_seconds()
        elapsed_hours = elapsed_seconds / 3600
        duration_str = format_duration(elapsed_seconds)

        channel = self.bot.get_channel(self.log_channel_id)
        if channel is None:
            print(f"⚠️ Could not find log channel with ID {self.log_channel_id}.")
            return

        if elapsed_hours >= MILESTONE_HOURS:
            embed = discord.Embed(
                title="🏆 Massive Voice Session Ended",
                description=f"The voice channel session ran for a huge **{duration_str}** before disconnecting.",
                color=discord.Color.gold()
            )
        else:
            embed = discord.Embed(
                title="🔌 Voice Session Interrupted",
                description=f"Session lasted **{duration_str}** before disconnecting. Reconnecting now.",
                color=discord.Color.orange()
            )

        try:
            await channel.send(embed=embed)
        except Exception as e:
            print(f"❌ Failed to send disconnect log: {e}")

    async def log_still_occupied(self):
        """Bot disconnected but people are still in the channel, so the timer keeps running."""
        if not self.log_channel_id:
            return

        channel = self.bot.get_channel(self.log_channel_id)
        if channel is None:
            return

        elapsed_seconds = self._get_elapsed_seconds()
        duration_str = format_duration(elapsed_seconds)
        now_str = datetime.now(timezone.utc).strftime("%b %d, %I:%M %p UTC")

        embed = discord.Embed(
            title="😅 I gotta take a break, please don't leave!",
            description=(
                f"I was counting **{duration_str}** as of {now_str}.\n"
                f"Someone's still in the channel, so the clock keeps running while I'm gone. Back soon!"
            ),
            color=discord.Color.orange()
        )
        try:
            await channel.send(embed=embed)
        except Exception as e:
            print(f"❌ Failed to send still-occupied log: {e}")

    async def join_target_channel(self):
        if not self.target_channel_id:
            print("⚠️ VOICE_CHANNEL_ID not set in .env — skipping voice persistence.")
            return

        channel = self.bot.get_channel(self.target_channel_id)
        if channel is None:
            print(f"⚠️ Could not find voice channel with ID {self.target_channel_id}. Check the ID in .env.")
            return

        guild = channel.guild
        current_vc = guild.voice_client

        if current_vc is not None:
            if current_vc.channel and current_vc.channel.id == self.target_channel_id:
                # Already connected (or discord.py is already handling its own reconnect
                # to this exact channel) — don't interfere, just make sure the timer is running.
                started_fresh = self._start_timer_if_needed()
                await self.announce_startup(started_fresh)
                return
            if current_vc.is_connected():
                await current_vc.move_to(channel)
                print(f"🔀 Moved voice connection to {channel.name}")
                started_fresh = self._start_timer_if_needed()
                await self.announce_startup(started_fresh)
            else:
                # A voice client exists but isn't fully connected yet (mid-reconnect).
                # Let discord.py finish its own reconnect instead of racing it with a new connect().
                print("⏳ Voice client is mid-reconnect, skipping manual join this cycle.")
            return

        try:
            await channel.connect(reconnect=True, self_deaf=True)
            print(f"🔊 Connected to voice channel: {channel.name}")
            started_fresh = self._start_timer_if_needed()
            await self.announce_startup(started_fresh)
        except Exception as e:
            print(f"❌ Failed to connect to voice channel: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        await self.join_target_channel()

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        # Only care about the bot's own voice state
        if member.id != self.bot.user.id:
            return
        # If the bot got disconnected (kicked, channel deleted, etc.), decide whether to reset
        if before.channel is not None and after.channel is None:
            print("⚠️ Bot was disconnected from voice. Checking if anyone is still in the channel...")

            remaining_humans = [m for m in before.channel.members if not m.bot]

            if remaining_humans:
                print(f"👥 {len(remaining_humans)} member(s) still in channel — preserving timer.")
                await self.log_still_occupied()
                # Don't reset — the elapsed-time calculation already accounts for the gap
            else:
                print("📭 Channel is empty — logging session end and resetting timer.")
                await self.log_disconnect()
                self._reset_timer()

            await asyncio.sleep(3)
            await self.join_target_channel()

    @tasks.loop(minutes=5)
    async def watchdog(self):
        """Safety net: periodically checks the bot is still in the target channel."""
        await self.join_target_channel()

    @watchdog.before_loop
    async def before_watchdog(self):
        await self.bot.wait_until_ready()

    @discord.app_commands.command(name="voice-timer", description="View or reset the voice channel session timer (admin only)")
    @discord.app_commands.describe(action="What to do with the timer")
    @discord.app_commands.choices(action=[
        discord.app_commands.Choice(name="View current time", value="view"),
        discord.app_commands.Choice(name="Reset to zero", value="reset"),
    ])
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def voice_timer(self, interaction: discord.Interaction, action: discord.app_commands.Choice[str]):
        if action.value == "view":
            duration_str = format_duration(self._get_elapsed_seconds())
            await interaction.response.send_message(f"⏱️ Current session: **{duration_str}**", ephemeral=True)
        elif action.value == "reset":
            self._reset_timer()
            self._start_timer_if_needed()
            await interaction.response.send_message("🔄 Timer reset to 0.", ephemeral=True)

    @voice_timer.error
    async def voice_timer_error(self, interaction: discord.Interaction, error):
        if isinstance(error, discord.app_commands.MissingPermissions):
            await interaction.response.send_message("You need Manage Server permission to use this.", ephemeral=True)
        else:
            raise error

    @discord.app_commands.command(name="voice-timer-add", description="Add or subtract minutes from the voice timer (admin only)")
    @discord.app_commands.describe(minutes="Minutes to add (use a negative number to subtract)")
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def voice_timer_add(self, interaction: discord.Interaction, minutes: int):
        start_str = self.timer_data.get("start_time")
        if start_str is None:
            self._start_timer_if_needed()
            start_str = self.timer_data["start_time"]

        start = datetime.fromisoformat(start_str)
        adjusted_start = start - timedelta(minutes=minutes)
        self.timer_data["start_time"] = adjusted_start.isoformat()
        json_store.save_json(TIMER_FILE, self.timer_data)

        duration_str = format_duration(self._get_elapsed_seconds())
        sign = "+" if minutes >= 0 else ""
        await interaction.response.send_message(
            f"⏱️ Adjusted timer by {sign}{minutes}m. New total: **{duration_str}**", ephemeral=True
        )

    @voice_timer_add.error
    async def voice_timer_add_error(self, interaction: discord.Interaction, error):
        if isinstance(error, discord.app_commands.MissingPermissions):
            await interaction.response.send_message("You need Manage Server permission to use this.", ephemeral=True)
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(VoicePersistence(bot))
