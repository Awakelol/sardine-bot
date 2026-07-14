import uuid
import discord
from datetime import datetime, timedelta, timezone
from discord.ext import commands, tasks

PENDING_TIMEOUT_MINUTES = 5
LFG_EXPIRY_HOURS = 2
AWAY_GRACE_MINUTES = 10  # window to return to VC before being dropped (or the post invalidated, for the creator)
AUTO_JOIN_MINUTES = 15  # auto-add anyone who just sits in the VC this long without clicking Join

GAME_OPTIONS = ["R6 Siege", "League of Legends", "Valorant", "Apex Legends", "Brawlhalla", "Custom"]

# Per-game max players that can be requested via the dropdown (still allow typing a custom number)
GAME_CAPS = {
    "R6 Siege": 4,
    "League of Legends": 4,
    "Valorant": 4,
    "Apex Legends": 2,
    "Brawlhalla": 3,
}

MODE_OPTIONS = ["Ranked", "Casual", "Customs"]
ELO_OPTIONS = ["Low Elo", "High Elo"]


class LFGPost:
    """Holds the live state of a posted LFG request."""

    def __init__(self, creator: discord.Member, game, mode, elo, players_needed, flavor_text):
        self.id = str(uuid.uuid4())
        self.creator = creator
        self.creator_voice_channel_id = creator.voice.channel.id
        self.creator_voice_channel_name = creator.voice.channel.name
        self.game = game
        self.mode = mode
        self.elo = elo
        self.players_needed = players_needed
        self.flavor_text = flavor_text
        self.confirmed = []          # list of discord.Member
        self.pending = {}            # {user_id: expiry datetime} — clicked Join, not yet in VC
        self.member_away = {}        # {user_id: away_until datetime} — confirmed member briefly out of VC
        self.auto_join_since = {}    # {user_id: seen_since datetime} — in VC but never clicked Join
        self.creator_away_until = None
        self.invalidated = False
        self.invalidation_reason = None
        self.old_rendered = False    # tracks whether we've already re-rendered the embed grey once "old"
        self.created_at = datetime.now(timezone.utc)
        self.message: discord.Message | None = None

    @property
    def is_full(self):
        return len(self.confirmed) >= self.players_needed

    @property
    def is_old(self):
        return datetime.now(timezone.utc) - self.created_at >= timedelta(hours=LFG_EXPIRY_HOURS)

    @staticmethod
    def _minutes_left(deadline: datetime, now: datetime) -> int:
        return max(1, int((deadline - now).total_seconds() // 60) + 1)

    def build_embed(self):
        now = datetime.now(timezone.utc)

        if self.is_old:
            color = discord.Color.light_grey()
            status_prefix = "⌛ OLD — "
        elif self.invalidated:
            color = discord.Color.red()
            status_prefix = "❌ INVALID — "
        elif self.is_full:
            color = discord.Color.blue()
            status_prefix = "✅ FULL — "
        else:
            color = discord.Color.green()
            status_prefix = ""

        description_lines = []
        if self.flavor_text:
            description_lines.append(f"*{self.flavor_text}*\n")
        if self.mode:
            description_lines.append(f"**Mode:** {self.mode}")
        if self.elo:
            description_lines.append(f"**Elo:** {self.elo}")

        creator_line = f"**Started by:** {self.creator.mention} in 🔊 {self.creator_voice_channel_name}"
        if self.creator_away_until and self.creator_away_until > now:
            creator_line += f" — ⚠️ away, back within {self._minutes_left(self.creator_away_until, now)}m or this expires"
        description_lines.append(creator_line)

        if self.confirmed:
            member_strs = []
            for m in self.confirmed:
                away_until = self.member_away.get(m.id)
                if away_until and away_until > now:
                    member_strs.append(f"{m.mention} (away, {self._minutes_left(away_until, now)}m to return)")
                else:
                    member_strs.append(m.mention)
            joined_str = ", ".join(member_strs)
        else:
            joined_str = "*None yet*"
        description_lines.append(f"**Joined:** {joined_str}")

        title = f"{status_prefix}{self.game} | Players needed: {len(self.confirmed)}/{self.players_needed}"

        if self.invalidated:
            description_lines.append(f"\n*{self.invalidation_reason or 'This request is no longer active.'}*")
        elif self.is_old:
            description_lines.append(f"\n*This request is over {LFG_EXPIRY_HOURS} hours old.*")

        embed = discord.Embed(title=title, description="\n".join(description_lines), color=color)
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


# ---------- Wizard steps ----------

class GameSelect(discord.ui.View):
    def __init__(self, cog: "LFG"):
        super().__init__(timeout=180)
        self.cog = cog
        options = [discord.SelectOption(label=g) for g in GAME_OPTIONS]
        select = discord.ui.Select(placeholder="Choose a game...", options=options)
        select.callback = self.on_select
        self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        game = interaction.data["values"][0]
        if game == "Custom":
            await interaction.response.send_modal(CustomGameModal(self.cog))
        else:
            await interaction.response.edit_message(
                content=f"**Game:** {game}\nChoose a mode:",
                view=ModeSelect(self.cog, game)
            )


class CustomGameModal(discord.ui.Modal, title="Custom Game"):
    game_name = discord.ui.TextInput(label="What game?", max_length=50)

    def __init__(self, cog: "LFG"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        # Custom games skip mode/elo entirely and go straight to players needed
        await interaction.response.send_modal(
            PlayersNeededModal(self.cog, game=str(self.game_name), mode=None, elo=None, cap=None)
        )


class ModeSelect(discord.ui.View):
    def __init__(self, cog: "LFG", game: str):
        super().__init__(timeout=180)
        self.cog = cog
        self.game = game
        options = [discord.SelectOption(label=m) for m in MODE_OPTIONS]
        select = discord.ui.Select(placeholder="Ranked, Casual, or Customs?", options=options)
        select.callback = self.on_select
        self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        mode = interaction.data["values"][0]
        if mode == "Ranked":
            await interaction.response.edit_message(
                content=f"**Game:** {self.game}\n**Mode:** {mode}\nChoose an Elo range:",
                view=EloSelect(self.cog, self.game, mode)
            )
        else:
            cap = GAME_CAPS.get(self.game, 5)
            await interaction.response.edit_message(
                content=f"**Game:** {self.game}\n**Mode:** {mode}\nHow many players do you need?",
                view=PlayersNeededSelect(self.cog, self.game, mode, elo=None, cap=cap)
            )


class EloSelect(discord.ui.View):
    def __init__(self, cog: "LFG", game: str, mode: str):
        super().__init__(timeout=180)
        self.cog = cog
        self.game = game
        self.mode = mode
        options = [discord.SelectOption(label=e) for e in ELO_OPTIONS]
        select = discord.ui.Select(placeholder="Low Elo or High Elo?", options=options)
        select.callback = self.on_select
        self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        elo = interaction.data["values"][0]
        cap = GAME_CAPS.get(self.game, 5)
        await interaction.response.edit_message(
            content=f"**Game:** {self.game}\n**Mode:** {self.mode}\n**Elo:** {elo}\nHow many players do you need?",
            view=PlayersNeededSelect(self.cog, self.game, self.mode, elo, cap)
        )


class PlayersNeededSelect(discord.ui.View):
    def __init__(self, cog: "LFG", game, mode, elo, cap: int):
        super().__init__(timeout=180)
        self.cog = cog
        self.game, self.mode, self.elo = game, mode, elo
        options = [discord.SelectOption(label=str(n)) for n in range(1, cap + 1)]
        options.append(discord.SelectOption(label="Custom number"))
        select = discord.ui.Select(placeholder=f"1 to {cap}, or custom...", options=options)
        select.callback = self.on_select
        self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        value = interaction.data["values"][0]
        if value == "Custom number":
            await interaction.response.send_modal(
                CustomNumberModal(self.cog, self.game, self.mode, self.elo)
            )
        else:
            await interaction.response.send_modal(
                FlavorTextModal(self.cog, self.game, self.mode, self.elo, int(value))
            )


class CustomNumberModal(discord.ui.Modal, title="Players Needed"):
    number = discord.ui.TextInput(label="How many players?", max_length=2)

    def __init__(self, cog, game, mode, elo):
        super().__init__()
        self.cog, self.game, self.mode, self.elo = cog, game, mode, elo

    async def on_submit(self, interaction: discord.Interaction):
        try:
            n = int(str(self.number))
            if n < 1:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("Please enter a valid positive number.", ephemeral=True)
            return
        await interaction.response.send_modal(
            FlavorTextModal(self.cog, self.game, self.mode, self.elo, n)
        )


class PlayersNeededModal(discord.ui.Modal, title="Players Needed"):
    """Used only for the Custom-game path, where there's no preset dropdown."""
    number = discord.ui.TextInput(label="How many players?", max_length=2)

    def __init__(self, cog, game, mode, elo, cap):
        super().__init__()
        self.cog, self.game, self.mode, self.elo = cog, game, mode, elo

    async def on_submit(self, interaction: discord.Interaction):
        try:
            n = int(str(self.number))
            if n < 1:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("Please enter a valid positive number.", ephemeral=True)
            return
        await interaction.response.send_modal(
            FlavorTextModal(self.cog, self.game, self.mode, self.elo, n)
        )


class FlavorTextModal(discord.ui.Modal, title="Add a message (optional)"):
    flavor = discord.ui.TextInput(
        label="Flavor text", required=False, max_length=100,
        placeholder="e.g. tara na siege time"
    )

    def __init__(self, cog: "LFG", game, mode, elo, players_needed):
        super().__init__()
        self.cog = cog
        self.game, self.mode, self.elo, self.players_needed = game, mode, elo, players_needed

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.finalize_post(
            interaction, self.game, self.mode, self.elo, self.players_needed, str(self.flavor)
        )


# ---------- Main cog ----------

class LFG(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active_posts: dict[str, LFGPost] = {}
        self.cleanup_pending.start()

    def cog_unload(self):
        self.cleanup_pending.cancel()

    @discord.app_commands.command(name="lfg", description="Post a Looking For Group request")
    async def lfg(self, interaction: discord.Interaction):
        member = interaction.user
        if member.voice is None or member.voice.channel is None:
            await interaction.response.send_message(
                "You need to be in a voice channel to use `/lfg`. Join one, then try again.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            "Choose a game:", view=GameSelect(self), ephemeral=True
        )

    async def finalize_post(self, interaction: discord.Interaction, game, mode, elo, players_needed, flavor_text):
        member = interaction.user
        if member.voice is None or member.voice.channel is None:
            await interaction.response.send_message(
                "You're no longer in a voice channel, so this LFG request can't be posted.",
                ephemeral=True
            )
            return

        post = LFGPost(member, game, mode, elo, players_needed, flavor_text)
        now = datetime.now(timezone.utc)
        # Anyone already sitting in the VC when the post goes up also starts the auto-join clock
        for existing_member in member.voice.channel.members:
            if not existing_member.bot and existing_member.id != member.id:
                post.auto_join_since[existing_member.id] = now
        self.active_posts[post.id] = post

        view = JoinView(self, post)
        await interaction.response.send_message(embed=post.build_embed(), view=view)
        original = await interaction.original_response()
        # Re-fetch as a plain channel message: interaction.original_response() is tied to the
        # interaction's webhook token, which Discord invalidates after 15 minutes — after that,
        # edits to it silently fail. A normal message edits fine for as long as the bot has access.
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
        if any(m.id == member.id for m in post.confirmed):
            await interaction.response.send_message("You've already joined this group.", ephemeral=True)
            return

        target_channel = self.bot.get_channel(post.creator_voice_channel_id)

        if member.voice is not None and member.voice.channel is not None:
            if member.voice.channel.id == post.creator_voice_channel_id:
                await self.confirm_member(post, member)
                await interaction.response.send_message("You're in! Added to the group.", ephemeral=True)
            else:
                try:
                    await member.move_to(target_channel, reason="Joined via LFG")
                    await self.confirm_member(post, member)
                    await interaction.response.send_message("Moved you to the voice channel and added you!", ephemeral=True)
                except Exception:
                    await interaction.response.send_message(
                        f"Couldn't move you automatically. Please join {target_channel.mention} manually.",
                        ephemeral=True
                    )
        else:
            post.pending[member.id] = datetime.now(timezone.utc) + timedelta(minutes=PENDING_TIMEOUT_MINUTES)
            await interaction.response.send_message(
                f"Join {target_channel.mention} within {PENDING_TIMEOUT_MINUTES} minutes to confirm your spot.",
                ephemeral=True
            )

    async def refresh_embed(self, post: LFGPost):
        if post.message:
            try:
                await post.message.edit(embed=post.build_embed())
            except discord.HTTPException as e:
                print(f"⚠️ LFG embed update failed for post {post.id}: {e}")

    async def confirm_member(self, post: LFGPost, member: discord.Member):
        post.confirmed.append(member)
        post.pending.pop(member.id, None)
        await self.refresh_embed(post)

    async def invalidate_post(self, post: LFGPost, reason: str = "The creator left the voice channel, this request is no longer active."):
        post.invalidated = True
        post.invalidation_reason = reason
        if post.is_old:
            post.old_rendered = True
        if post.message:
            try:
                await post.message.edit(embed=post.build_embed(), view=None)
            except discord.HTTPException as e:
                print(f"⚠️ LFG invalidate failed for post {post.id}: {e}")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before, after):
        now = datetime.now(timezone.utc)
        for post in list(self.active_posts.values()):
            if post.invalidated:
                continue

            left_target = (
                before.channel is not None and before.channel.id == post.creator_voice_channel_id
                and (after.channel is None or after.channel.id != post.creator_voice_channel_id)
            )
            joined_target = (
                after.channel is not None and after.channel.id == post.creator_voice_channel_id
                and (before.channel is None or before.channel.id != post.creator_voice_channel_id)
            )

            if member.id == post.creator.id:
                if left_target:
                    if not post.confirmed:
                        await self.invalidate_post(post, reason="The creator left before anyone joined.")
                    else:
                        post.creator_away_until = now + timedelta(minutes=AWAY_GRACE_MINUTES)
                        await self.refresh_embed(post)
                elif joined_target and post.creator_away_until is not None:
                    post.creator_away_until = None
                    await self.refresh_embed(post)
                continue

            if member.id in post.pending and joined_target:
                await self.confirm_member(post, member)
                continue

            is_confirmed = any(m.id == member.id for m in post.confirmed)
            if is_confirmed:
                if left_target:
                    post.member_away[member.id] = now + timedelta(minutes=AWAY_GRACE_MINUTES)
                    await self.refresh_embed(post)
                elif joined_target and member.id in post.member_away:
                    post.member_away.pop(member.id, None)
                    await self.refresh_embed(post)
                continue

            if member.bot:
                continue

            # Never clicked Join, just sitting in the VC — start/reset the auto-join clock
            if joined_target:
                post.auto_join_since[member.id] = now
            elif left_target:
                post.auto_join_since.pop(member.id, None)

    @tasks.loop(minutes=1)
    async def cleanup_pending(self):
        now = datetime.now(timezone.utc)
        for post in list(self.active_posts.values()):
            expired_pending = [uid for uid, expiry in post.pending.items() if expiry < now]
            for uid in expired_pending:
                post.pending.pop(uid, None)

            if post.invalidated:
                if post.is_old and not post.old_rendered:
                    post.old_rendered = True
                    await self.refresh_embed(post)
                continue

            if post.is_old:
                await self.invalidate_post(post, reason=f"This request expired after {LFG_EXPIRY_HOURS} hours.")
                continue

            if post.creator_away_until and post.creator_away_until < now:
                await self.invalidate_post(post, reason="The creator left and didn't return in time.")
                continue

            expired_away = [uid for uid, deadline in post.member_away.items() if deadline < now]
            if expired_away:
                for uid in expired_away:
                    post.member_away.pop(uid, None)
                post.confirmed = [m for m in post.confirmed if m.id not in expired_away]
                await self.refresh_embed(post)

            expired_auto = [
                uid for uid, since in post.auto_join_since.items()
                if now - since >= timedelta(minutes=AUTO_JOIN_MINUTES)
            ]
            if expired_auto:
                channel = self.bot.get_channel(post.creator_voice_channel_id)
                for uid in expired_auto:
                    post.auto_join_since.pop(uid, None)
                    if post.is_full or any(m.id == uid for m in post.confirmed):
                        continue
                    member = discord.utils.get(channel.members, id=uid) if channel else None
                    if member is None:
                        continue  # left without us catching the voice update, nothing to add
                    await self.confirm_member(post, member)

    @cleanup_pending.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(LFG(bot))
