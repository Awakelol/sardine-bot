import logging
import uuid
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

log = logging.getLogger(__name__)

PENDING_TIMEOUT_MINUTES = 5
LFG_EXPIRY_HOURS = 2
AWAY_GRACE_MINUTES = (
    10  # how long someone can leave the VC before losing their spot (or killing the post, for the creator)
)
AUTO_JOIN_MINUTES = 15  # anyone sitting in the VC this long without clicking Join gets added anyway

GAME_OPTIONS = ["R6 Siege", "League of Legends", "Valorant", "Apex Legends", "Brawlhalla", "Custom"]

# max players offered in the dropdown per game, a custom number can still be typed in
GAME_CAPS = {
    "R6 Siege": 4,
    "League of Legends": 4,
    "Valorant": 4,
    "Apex Legends": 2,
    "Brawlhalla": 3,
}
DEFAULT_CAP = 5

MODE_OPTIONS = ["Ranked", "Casual", "Customs"]
ELO_OPTIONS = ["Low Elo", "High Elo"]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def minutes_left(deadline: datetime, now: datetime) -> int:
    return max(1, int((deadline - now).total_seconds() // 60) + 1)


class LFGPost:
    def __init__(self, creator: discord.Member, game, mode, elo, players_needed, flavor_text):
        self.id = str(uuid.uuid4())
        self.creator = creator
        self.voice_channel_id = creator.voice.channel.id
        self.voice_channel_name = creator.voice.channel.name
        self.game = game
        self.mode = mode
        self.elo = elo
        self.players_needed = players_needed
        self.flavor_text = flavor_text
        self.confirmed: list[discord.Member] = []
        self.pending: dict[int, datetime] = {}  # clicked Join but not in the VC yet -> expiry
        self.member_away: dict[int, datetime] = {}  # confirmed but stepped out -> deadline to return
        self.auto_join_since: dict[int, datetime] = {}  # in the VC without clicking Join -> since when
        self.creator_away_until: datetime | None = None
        self.invalidated = False
        self.invalidation_reason = None
        self.created_at = now_utc()
        self.message: discord.Message | None = None

    @property
    def is_full(self):
        return len(self.confirmed) >= self.players_needed

    @property
    def is_old(self):
        return now_utc() - self.created_at >= timedelta(hours=LFG_EXPIRY_HOURS)

    def has_member(self, user_id: int) -> bool:
        return any(m.id == user_id for m in self.confirmed)

    def build_embed(self):
        now = now_utc()

        if self.is_old:
            color, prefix = discord.Color.light_grey(), "⌛ OLD - "
        elif self.invalidated:
            color, prefix = discord.Color.red(), "❌ INVALID - "
        elif self.is_full:
            color, prefix = discord.Color.blue(), "✅ FULL - "
        else:
            color, prefix = discord.Color.green(), ""

        lines = []
        if self.flavor_text:
            lines.append(f"*{self.flavor_text}*\n")
        if self.mode:
            lines.append(f"**Mode:** {self.mode}")
        if self.elo:
            lines.append(f"**Elo:** {self.elo}")

        creator_line = f"**Started by:** {self.creator.mention} in 🔊 {self.voice_channel_name}"
        if self.creator_away_until and self.creator_away_until > now:
            creator_line += f" (⚠️ away, back within {minutes_left(self.creator_away_until, now)}m or this expires)"
        lines.append(creator_line)

        members = []
        for m in self.confirmed:
            away_until = self.member_away.get(m.id)
            if away_until and away_until > now:
                members.append(f"{m.mention} (away, {minutes_left(away_until, now)}m to return)")
            else:
                members.append(m.mention)
        lines.append(f"**Joined:** {', '.join(members) or '*None yet*'}")

        if self.invalidated:
            lines.append(f"\n*{self.invalidation_reason or 'This request is no longer active.'}*")
        elif self.is_old:
            lines.append(f"\n*This request is over {LFG_EXPIRY_HOURS} hours old.*")

        title = f"{prefix}{self.game} | Players needed: {len(self.confirmed)}/{self.players_needed}"
        embed = discord.Embed(title=title, description="\n".join(lines), color=color)
        embed.set_author(name=self.creator.display_name, icon_url=self.creator.display_avatar.url)
        return embed


class JoinView(discord.ui.View):
    def __init__(self, cog: "LFG", post: LFGPost):
        super().__init__(timeout=None)
        self.cog = cog
        self.post = post

    @discord.ui.button(label="Join", style=discord.ButtonStyle.green, emoji="🙋")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_join_click(interaction, self.post)


# /lfg wizard: game -> mode -> elo (ranked only) -> players -> details modal


class GameSelect(discord.ui.View):
    def __init__(self, cog: "LFG"):
        super().__init__(timeout=180)
        self.cog = cog
        select = discord.ui.Select(
            placeholder="Choose a game...", options=[discord.SelectOption(label=g) for g in GAME_OPTIONS]
        )
        select.callback = self.on_select
        self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        game = interaction.data["values"][0]
        if game == "Custom":
            # custom games skip mode/elo and ask for everything in the modal
            await interaction.response.send_modal(DetailsModal(self.cog))
        else:
            await interaction.response.edit_message(
                content=f"**Game:** {game}\nChoose a mode:",
                view=ModeSelect(self.cog, game),
            )


class ModeSelect(discord.ui.View):
    def __init__(self, cog: "LFG", game: str):
        super().__init__(timeout=180)
        self.cog = cog
        self.game = game
        select = discord.ui.Select(
            placeholder="Ranked, Casual, or Customs?", options=[discord.SelectOption(label=m) for m in MODE_OPTIONS]
        )
        select.callback = self.on_select
        self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        mode = interaction.data["values"][0]
        if mode == "Ranked":
            await interaction.response.edit_message(
                content=f"**Game:** {self.game}\n**Mode:** {mode}\nChoose an Elo range:",
                view=EloSelect(self.cog, self.game, mode),
            )
        else:
            await interaction.response.edit_message(
                content=f"**Game:** {self.game}\n**Mode:** {mode}\nHow many players do you need?",
                view=PlayersNeededSelect(self.cog, self.game, mode, elo=None),
            )


class EloSelect(discord.ui.View):
    def __init__(self, cog: "LFG", game: str, mode: str):
        super().__init__(timeout=180)
        self.cog = cog
        self.game = game
        self.mode = mode
        select = discord.ui.Select(
            placeholder="Low Elo or High Elo?", options=[discord.SelectOption(label=e) for e in ELO_OPTIONS]
        )
        select.callback = self.on_select
        self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        elo = interaction.data["values"][0]
        await interaction.response.edit_message(
            content=f"**Game:** {self.game}\n**Mode:** {self.mode}\n**Elo:** {elo}\nHow many players do you need?",
            view=PlayersNeededSelect(self.cog, self.game, self.mode, elo),
        )


class PlayersNeededSelect(discord.ui.View):
    def __init__(self, cog: "LFG", game: str, mode: str, elo: str | None):
        super().__init__(timeout=180)
        self.cog = cog
        self.game, self.mode, self.elo = game, mode, elo
        cap = GAME_CAPS.get(game, DEFAULT_CAP)
        options = [discord.SelectOption(label=str(n)) for n in range(1, cap + 1)]
        options.append(discord.SelectOption(label="Custom number"))
        select = discord.ui.Select(placeholder=f"1 to {cap}, or custom...", options=options)
        select.callback = self.on_select
        self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        value = interaction.data["values"][0]
        players_needed = None if value == "Custom number" else int(value)
        await interaction.response.send_modal(DetailsModal(self.cog, self.game, self.mode, self.elo, players_needed))


class DetailsModal(discord.ui.Modal):
    """Last step of the wizard. Asks for whatever the dropdowns didn't cover, all in one go,
    since Discord doesn't allow opening a modal from another modal's submit."""

    def __init__(self, cog: "LFG", game=None, mode=None, elo=None, players_needed=None):
        if game is None:
            title = "Custom Game"
        elif players_needed is None:
            title = "Players Needed"
        else:
            title = "Add a message (optional)"
        super().__init__(title=title)
        self.cog = cog
        self.game, self.mode, self.elo, self.players_needed = game, mode, elo, players_needed

        self.game_input = discord.ui.TextInput(label="What game?", max_length=50)
        self.count_input = discord.ui.TextInput(label="How many players?", max_length=2)
        self.flavor_input = discord.ui.TextInput(
            label="Flavor text", required=False, max_length=100, placeholder="e.g. need one more for ranked"
        )
        if game is None:
            self.add_item(self.game_input)
        if players_needed is None:
            self.add_item(self.count_input)
        self.add_item(self.flavor_input)

    async def on_submit(self, interaction: discord.Interaction):
        players_needed = self.players_needed
        if players_needed is None:
            try:
                players_needed = int(self.count_input.value)
            except ValueError:
                players_needed = 0
            if players_needed < 1:
                await interaction.response.send_message("Please enter a valid positive number.", ephemeral=True)
                return

        game = self.game or self.game_input.value.strip()
        await self.cog.finalize_post(interaction, game, self.mode, self.elo, players_needed, self.flavor_input.value)


class LFG(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active_posts: dict[str, LFGPost] = {}
        self.cleanup_pending.start()

    def cog_unload(self):
        self.cleanup_pending.cancel()

    @app_commands.command(name="lfg", description="Post a Looking For Group request")
    async def lfg(self, interaction: discord.Interaction):
        if interaction.user.voice is None or interaction.user.voice.channel is None:
            await interaction.response.send_message(
                "You need to be in a voice channel to use `/lfg`. Join one, then try again.", ephemeral=True
            )
            return
        await interaction.response.send_message("Choose a game:", view=GameSelect(self), ephemeral=True)

    async def finalize_post(self, interaction: discord.Interaction, game, mode, elo, players_needed, flavor_text):
        member = interaction.user
        if member.voice is None or member.voice.channel is None:
            await interaction.response.send_message(
                "You're no longer in a voice channel, so this LFG request can't be posted.", ephemeral=True
            )
            return

        post = LFGPost(member, game, mode, elo, players_needed, flavor_text)
        # people already in the VC when the post goes up start on the auto-join clock too
        now = now_utc()
        for m in member.voice.channel.members:
            if not m.bot and m.id != member.id:
                post.auto_join_since[m.id] = now
        self.active_posts[post.id] = post

        await interaction.response.send_message(embed=post.build_embed(), view=JoinView(self, post))
        original = await interaction.original_response()
        # Edits through original_response() go via the interaction webhook, whose token dies after
        # 15 minutes. Fetching it as a normal channel message keeps it editable for good.
        post.message = await interaction.channel.fetch_message(original.id)

    async def handle_join_click(self, interaction: discord.Interaction, post: LFGPost):
        member = interaction.user

        if post.invalidated:
            await interaction.response.send_message("This LFG request has expired.", ephemeral=True)
            return
        if post.is_full:
            await interaction.response.send_message("This group is already full.", ephemeral=True)
            return
        if member.id == post.creator.id:
            await interaction.response.send_message("You're already the one hosting this LFG.", ephemeral=True)
            return
        if post.has_member(member.id):
            await interaction.response.send_message("You've already joined this group.", ephemeral=True)
            return

        target_channel = self.bot.get_channel(post.voice_channel_id)
        if target_channel is None:
            await interaction.response.send_message("That voice channel doesn't exist anymore.", ephemeral=True)
            return

        if member.voice is None or member.voice.channel is None:
            post.pending[member.id] = now_utc() + timedelta(minutes=PENDING_TIMEOUT_MINUTES)
            await interaction.response.send_message(
                f"Join {target_channel.mention} within {PENDING_TIMEOUT_MINUTES} minutes to confirm your spot.",
                ephemeral=True,
            )
            return

        if member.voice.channel.id == post.voice_channel_id:
            await self.confirm_member(post, member)
            await interaction.response.send_message("You're in! Added to the group.", ephemeral=True)
            return

        try:
            await member.move_to(target_channel, reason="Joined via LFG")
        except discord.HTTPException:
            await interaction.response.send_message(
                f"Couldn't move you automatically. Please join {target_channel.mention} manually.", ephemeral=True
            )
            return
        await self.confirm_member(post, member)
        await interaction.response.send_message("Moved you to the voice channel and added you!", ephemeral=True)

    async def refresh_embed(self, post: LFGPost):
        if not post.message:
            return
        try:
            await post.message.edit(embed=post.build_embed())
        except discord.HTTPException as e:
            log.warning("Couldn't update LFG post %s: %s", post.id, e)

    async def confirm_member(self, post: LFGPost, member: discord.Member):
        post.confirmed.append(member)
        post.pending.pop(member.id, None)
        await self.refresh_embed(post)

    async def invalidate_post(self, post: LFGPost, reason: str):
        post.invalidated = True
        post.invalidation_reason = reason
        if post.is_old:
            # already renders grey, nothing left to update later
            self.active_posts.pop(post.id, None)
        if post.message:
            try:
                await post.message.edit(embed=post.build_embed(), view=None)
            except discord.HTTPException as e:
                log.warning("Couldn't invalidate LFG post %s: %s", post.id, e)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before, after):
        before_id = before.channel.id if before.channel else None
        after_id = after.channel.id if after.channel else None
        if before_id == after_id:
            return  # mute/deafen/stream changes

        now = now_utc()
        for post in list(self.active_posts.values()):
            if post.invalidated:
                continue
            left = before_id == post.voice_channel_id
            joined = after_id == post.voice_channel_id

            if member.id == post.creator.id:
                if left:
                    if not post.confirmed:
                        await self.invalidate_post(post, "The creator left before anyone joined.")
                    else:
                        post.creator_away_until = now + timedelta(minutes=AWAY_GRACE_MINUTES)
                        await self.refresh_embed(post)
                elif joined and post.creator_away_until is not None:
                    post.creator_away_until = None
                    await self.refresh_embed(post)
                continue

            if member.id in post.pending and joined:
                await self.confirm_member(post, member)
                continue

            if post.has_member(member.id):
                if left:
                    post.member_away[member.id] = now + timedelta(minutes=AWAY_GRACE_MINUTES)
                    await self.refresh_embed(post)
                elif joined and post.member_away.pop(member.id, None):
                    await self.refresh_embed(post)
                continue

            if member.bot:
                continue

            # hasn't clicked Join, just hanging out in the VC
            if joined:
                post.auto_join_since[member.id] = now
            elif left:
                post.auto_join_since.pop(member.id, None)

    @tasks.loop(minutes=1)
    async def cleanup_pending(self):
        now = now_utc()
        for post in list(self.active_posts.values()):
            post.pending = {uid: expiry for uid, expiry in post.pending.items() if expiry >= now}

            if post.invalidated:
                # one last edit to grey it out once it's old, then stop tracking it
                if post.is_old:
                    await self.refresh_embed(post)
                    del self.active_posts[post.id]
                continue

            if post.is_old:
                await self.invalidate_post(post, f"This request expired after {LFG_EXPIRY_HOURS} hours.")
                continue

            if post.creator_away_until and post.creator_away_until < now:
                await self.invalidate_post(post, "The creator left and didn't return in time.")
                continue

            expired_away = {uid for uid, deadline in post.member_away.items() if deadline < now}
            if expired_away:
                for uid in expired_away:
                    del post.member_away[uid]
                post.confirmed = [m for m in post.confirmed if m.id not in expired_away]
                await self.refresh_embed(post)

            ready = [
                uid
                for uid, since in post.auto_join_since.items()
                if now - since >= timedelta(minutes=AUTO_JOIN_MINUTES)
            ]
            if ready:
                channel = self.bot.get_channel(post.voice_channel_id)
                for uid in ready:
                    del post.auto_join_since[uid]
                    if post.is_full or post.has_member(uid):
                        continue
                    member = discord.utils.get(channel.members, id=uid) if channel else None
                    if member is not None:  # None if they left and we missed the voice update
                        await self.confirm_member(post, member)

    @cleanup_pending.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(LFG(bot))
