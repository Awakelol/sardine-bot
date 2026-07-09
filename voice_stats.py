import os
import json
import discord
from datetime import datetime, timezone
from discord.ext import commands

VOICE_CHANNEL_ID = os.getenv("VOICE_CHANNEL_ID")
STATS_FILE = os.path.join("data", "voice_user_stats.json")


def load_stats():
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_stats(data):
    os.makedirs("data", exist_ok=True)
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)


def format_duration(seconds: float) -> str:
    total_minutes = int(seconds // 60)
    hours, minutes = divmod(total_minutes, 60)
    days, hours = divmod(hours, 24)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


class VoiceStats(commands.Cog):
    """Tracks how long individual members spend in the target voice channel."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.target_channel_id = int(VOICE_CHANNEL_ID) if VOICE_CHANNEL_ID else None
        self.stats = load_stats()          # {user_id: {"username": str, "total_seconds": float}}
        self.active_sessions = {}          # {user_id: start_time datetime}

    def _start_session(self, member: discord.Member):
        self.active_sessions[str(member.id)] = datetime.now(timezone.utc)

    def _end_session(self, member: discord.Member):
        user_id = str(member.id)
        start_time = self.active_sessions.pop(user_id, None)
        if start_time is None:
            return

        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
        record = self.stats.get(user_id, {"username": member.display_name, "total_seconds": 0})
        record["username"] = member.display_name
        record["total_seconds"] += elapsed
        self.stats[user_id] = record
        save_stats(self.stats)

    def _get_live_total(self, user_id: str) -> float:
        """Total time including whatever the current in-progress session has accrued so far."""
        base = self.stats.get(user_id, {}).get("total_seconds", 0)
        active_start = self.active_sessions.get(user_id)
        if active_start:
            base += (datetime.now(timezone.utc) - active_start).total_seconds()
        return base

    @commands.Cog.listener()
    async def on_ready(self):
        # If the bot restarts while people are already sitting in the channel,
        # start tracking them from now rather than missing their session entirely.
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

    @discord.app_commands.command(name="voice-leaderboard", description="See who's spent the most time in the voice channel")
    async def voice_leaderboard(self, interaction: discord.Interaction):
        if not self.stats and not self.active_sessions:
            await interaction.response.send_message("No voice activity tracked yet.", ephemeral=True)
            return

        all_user_ids = set(self.stats.keys()) | set(self.active_sessions.keys())
        ranked = sorted(
            all_user_ids,
            key=lambda uid: self._get_live_total(uid),
            reverse=True
        )[:3]

        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, user_id in enumerate(ranked):
            username = self.stats.get(user_id, {}).get("username", f"<@{user_id}>")
            total = self._get_live_total(user_id)
            lines.append(f"{medals[i]} **{username}** — {format_duration(total)}")

        embed = discord.Embed(
            title="🎙️ Voice Channel Legends",
            description="\n".join(lines),
            color=discord.Color.blurple()
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceStats(bot))
