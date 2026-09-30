import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

import json_store
from time_utils import format_duration

log = logging.getLogger(__name__)

VOICE_CHANNEL_ID = os.getenv("VOICE_CHANNEL_ID")
VOICE_LOG_CHANNEL_ID = os.getenv("VOICE_LOG_CHANNEL_ID")
TIMER_FILE = os.path.join("data", "voice_timer.json")
MILESTONE_HOURS = 200


class VoicePersistence(commands.Cog):
    """Keeps the bot sitting in one voice channel and times how long the session has gone on."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.target_channel_id = int(VOICE_CHANNEL_ID) if VOICE_CHANNEL_ID else None
        self.log_channel_id = int(VOICE_LOG_CHANNEL_ID) if VOICE_LOG_CHANNEL_ID else None
        self.timer_data = json_store.load_json(TIMER_FILE, {"start_time": None})
        self.startup_announced = False
        if self.target_channel_id:
            self.watchdog.start()
        else:
            log.warning("VOICE_CHANNEL_ID not set, voice persistence is off")

    def cog_unload(self):
        self.watchdog.cancel()

    def _start_timer_if_needed(self) -> bool:
        """Returns True if a new timer was started."""
        if self.timer_data.get("start_time") is not None:
            return False
        self.timer_data["start_time"] = datetime.now(timezone.utc).isoformat()
        json_store.save_json(TIMER_FILE, self.timer_data)
        return True

    def _elapsed_seconds(self) -> float:
        start = self.timer_data.get("start_time")
        if not start:
            return 0.0
        return max(0.0, (datetime.now(timezone.utc) - datetime.fromisoformat(start)).total_seconds())

    def _reset_timer(self):
        self.timer_data["start_time"] = None
        json_store.save_json(TIMER_FILE, self.timer_data)

    async def _post_log(self, embed: discord.Embed):
        if not self.log_channel_id:
            return
        channel = self.bot.get_channel(self.log_channel_id)
        if channel is None:
            log.warning("Couldn't find voice log channel %s", self.log_channel_id)
            return
        try:
            await channel.send(embed=embed)
        except discord.HTTPException as e:
            log.warning("Failed to post voice log: %s", e)

    async def _on_connected(self):
        started_fresh = self._start_timer_if_needed()
        if self.startup_announced:
            return
        self.startup_announced = True

        if started_fresh:
            embed = discord.Embed(
                title="🔊 Bot Online - New Session Started",
                description="Voice timer starting fresh from 0m.",
                color=discord.Color.blurple(),
            )
        else:
            embed = discord.Embed(
                title="🔊 Bot Online - Session Resumed",
                description=f"Reconnected after a restart. Timer continuing at **{format_duration(self._elapsed_seconds())}**.",
                color=discord.Color.blurple(),
            )
        await self._post_log(embed)

    async def _log_session_end(self):
        elapsed = self._elapsed_seconds()
        duration = format_duration(elapsed)
        if elapsed / 3600 >= MILESTONE_HOURS:
            embed = discord.Embed(
                title="🏆 Massive Voice Session Ended",
                description=f"The voice channel session ran for a huge **{duration}** before disconnecting.",
                color=discord.Color.gold(),
            )
        else:
            embed = discord.Embed(
                title="🔌 Voice Session Interrupted",
                description=f"Session lasted **{duration}** before disconnecting. Reconnecting now.",
                color=discord.Color.orange(),
            )
        await self._post_log(embed)

    async def _log_still_occupied(self):
        now = datetime.now(timezone.utc).strftime("%b %d, %I:%M %p UTC")
        embed = discord.Embed(
            title="😅 I gotta take a break, please don't leave!",
            description=(
                f"I was counting **{format_duration(self._elapsed_seconds())}** as of {now}.\n"
                "Someone's still in the channel, so the clock keeps running while I'm gone. Back soon!"
            ),
            color=discord.Color.orange(),
        )
        await self._post_log(embed)

    async def join_target_channel(self):
        channel = self.bot.get_channel(self.target_channel_id)
        if channel is None:
            log.warning("Couldn't find voice channel %s, check VOICE_CHANNEL_ID", self.target_channel_id)
            return

        vc = channel.guild.voice_client
        if vc is None:
            try:
                await channel.connect(reconnect=True, self_deaf=True)
            except Exception as e:
                log.error("Failed to connect to %s: %s", channel.name, e)
                return
            log.info("Connected to voice channel %s", channel.name)
        elif vc.channel and vc.channel.id == channel.id:
            pass  # already here, or discord.py is reconnecting to this channel on its own
        elif vc.is_connected():
            await vc.move_to(channel)
            log.info("Moved voice connection to %s", channel.name)
        else:
            # half-connected somewhere, let discord.py finish reconnecting instead of racing it
            log.info("Voice client is mid-reconnect, skipping this check")
            return

        await self._on_connected()

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.id != self.bot.user.id or before.channel is None or after.channel is not None:
            return

        # we got disconnected (kicked, channel deleted, etc.)
        remaining = [m for m in before.channel.members if not m.bot]
        if remaining:
            # session isn't over, and elapsed time is measured from start_time so the gap still counts
            log.info("Disconnected from voice, %d member(s) still in the channel so keeping the timer", len(remaining))
            await self._log_still_occupied()
        else:
            log.info("Disconnected from an empty voice channel, ending the session")
            await self._log_session_end()
            self._reset_timer()

        await asyncio.sleep(3)
        await self.join_target_channel()

    @tasks.loop(minutes=5)
    async def watchdog(self):
        # first run happens as soon as the bot is ready, which also handles the initial join
        await self.join_target_channel()

    @watchdog.before_loop
    async def before_watchdog(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="voice-timer", description="View or reset the voice channel session timer (admin only)")
    @app_commands.describe(action="What to do with the timer")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="View current time", value="view"),
            app_commands.Choice(name="Reset to zero", value="reset"),
        ]
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def voice_timer(self, interaction: discord.Interaction, action: app_commands.Choice[str]):
        if action.value == "view":
            await interaction.response.send_message(
                f"⏱️ Current session: **{format_duration(self._elapsed_seconds())}**", ephemeral=True
            )
        elif action.value == "reset":
            self._reset_timer()
            self._start_timer_if_needed()
            await interaction.response.send_message("🔄 Timer reset to 0.", ephemeral=True)

    @app_commands.command(
        name="voice-timer-add", description="Add or subtract minutes from the voice timer (admin only)"
    )
    @app_commands.describe(minutes="Minutes to add (use a negative number to subtract)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def voice_timer_add(self, interaction: discord.Interaction, minutes: int):
        self._start_timer_if_needed()
        start = datetime.fromisoformat(self.timer_data["start_time"]) - timedelta(minutes=minutes)
        self.timer_data["start_time"] = start.isoformat()
        json_store.save_json(TIMER_FILE, self.timer_data)

        await interaction.response.send_message(
            f"⏱️ Adjusted timer by {minutes:+}m. New total: **{format_duration(self._elapsed_seconds())}**",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(VoicePersistence(bot))
