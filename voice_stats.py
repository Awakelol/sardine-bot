import os
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

import json_store
from time_utils import format_duration

VOICE_CHANNEL_ID = os.getenv("VOICE_CHANNEL_ID")
STATS_FILE = os.path.join("data", "voice_user_stats.json")
MEDALS = ["🥇", "🥈", "🥉"]


class VoiceStats(commands.Cog):
    """Tracks how long each member spends in the target voice channel."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.target_channel_id = int(VOICE_CHANNEL_ID) if VOICE_CHANNEL_ID else None
        self.stats = json_store.load_json(STATS_FILE, {})  # user_id -> {"username", "total_seconds"}
        self.active_sessions: dict[str, datetime] = {}

    def _start_session(self, member: discord.Member):
        self.active_sessions[str(member.id)] = datetime.now(timezone.utc)

    def _end_session(self, member: discord.Member):
        user_id = str(member.id)
        start_time = self.active_sessions.pop(user_id, None)
        if start_time is None:
            return

        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
        record = self.stats.setdefault(user_id, {"total_seconds": 0})
        record["username"] = member.display_name
        record["total_seconds"] += elapsed
        json_store.save_json(STATS_FILE, self.stats)

    def _live_total(self, user_id: str) -> float:
        """Saved total plus the current session so far."""
        total = self.stats.get(user_id, {}).get("total_seconds", 0)
        active_start = self.active_sessions.get(user_id)
        if active_start:
            total += (datetime.now(timezone.utc) - active_start).total_seconds()
        return total

    @commands.Cog.listener()
    async def on_ready(self):
        # pick up anyone who was already in the channel when the bot (re)started
        if not self.target_channel_id:
            return
        channel = self.bot.get_channel(self.target_channel_id)
        if channel is None:
            return
        for member in channel.members:
            if not member.bot and str(member.id) not in self.active_sessions:
                self._start_session(member)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before, after):
        if member.bot or not self.target_channel_id:
            return

        was_in_target = before.channel is not None and before.channel.id == self.target_channel_id
        now_in_target = after.channel is not None and after.channel.id == self.target_channel_id

        if not was_in_target and now_in_target:
            self._start_session(member)
        elif was_in_target and not now_in_target:
            self._end_session(member)

    @app_commands.command(name="voice-leaderboard", description="See who's spent the most time in the voice channel")
    async def voice_leaderboard(self, interaction: discord.Interaction):
        user_ids = set(self.stats) | set(self.active_sessions)
        if not user_ids:
            await interaction.response.send_message("No voice activity tracked yet.", ephemeral=True)
            return

        top = sorted(user_ids, key=self._live_total, reverse=True)[: len(MEDALS)]
        lines = []
        for medal, user_id in zip(MEDALS, top, strict=False):
            username = self.stats.get(user_id, {}).get("username", f"<@{user_id}>")
            lines.append(f"{medal} **{username}** - {format_duration(self._live_total(user_id))}")

        embed = discord.Embed(
            title="🎙️ Voice Channel Legends",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceStats(bot))
