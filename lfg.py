import uuid
import discord
from datetime import datetime, timedelta, timezone
from discord.ext import commands, tasks

PENDING_TIMEOUT_MINUTES = 5
LFG_EXPIRY_HOURS = 2

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
        self.pending = {}            # {user_id: expiry datetime}
        self.invalidated = False
        self.invalidation_reason = None
        self.created_at = datetime.now(timezone.utc)
        self.message: discord.Message | None = None

    @property
    def is_full(self):
        return len(self.confirmed) >= self.players_needed

    def build_embed(self):
        color = discord.Color.red() if self.invalidated else (
            discord.Color.green() if self.is_full else discord.Color.blurple()
        )
        description_lines = []
        if self.flavor_text:
            description_lines.append(f"*{self.flavor_text}*\n")
        if self.mode:
            description_lines.append(f"**Mode:** {self.mode}")
        if self.elo:
            description_lines.append(f"**Elo:** {self.elo}")
        description_lines.append(f"**Started by:** {self.creator.mention} in 🔊 {self.creator_voice_channel_name}")

        joined_str = ", ".join(m.mention for m in self.confirmed) if self.confirmed else "*None yet*"
        description_lines.append(f"**Joined:** {joined_str}")

        status_prefix = "❌ EXPIRED — " if self.invalidated else ("✅ FULL — " if self.is_full else "")
        title = f"{status_prefix}{self.game} | Players needed: {len(self.confirmed)}/{self.players_needed}"

        if self.invalidated:
            description_lines.append(f"\n*{self.invalidation_reason or 'This request is no longer active.'}*")

        embed = discord.Embed(title=title, description="\n".join(description_lines), color=color)
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

    async def confirm_member(self, post: LFGPost, member: discord.Member):
        post.confirmed.append(member)
        post.pending.pop(member.id, None)
        if post.message:
            try:
                await post.message.edit(embed=post.build_embed())
            except discord.HTTPException as e:
                print(f"⚠️ LFG embed update failed for post {post.id}: {e}")

    async def invalidate_post(self, post: LFGPost, reason: str = "The creator left the voice channel, this request is no longer active."):
        post.invalidated = True
        post.invalidation_reason = reason
        if post.message:
            try:
                await post.message.edit(embed=post.build_embed(), view=None)
            except discord.HTTPException as e:
                print(f"⚠️ LFG invalidate failed for post {post.id}: {e}")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before, after):
        # Check if the creator of any active post left their voice channel
        for post in list(self.active_posts.values()):
            if post.invalidated:
                continue
            if member.id == post.creator.id:
                if before.channel and before.channel.id == post.creator_voice_channel_id:
                    if after.channel is None or after.channel.id != post.creator_voice_channel_id:
                        await self.invalidate_post(post)
                continue

            # Check if a pending member just joined the right channel
            if member.id in post.pending and after.channel and after.channel.id == post.creator_voice_channel_id:
                await self.confirm_member(post, member)

    @tasks.loop(minutes=1)
    async def cleanup_pending(self):
        now = datetime.now(timezone.utc)
        for post in list(self.active_posts.values()):
            expired = [uid for uid, expiry in post.pending.items() if expiry < now]
            for uid in expired:
                post.pending.pop(uid, None)

            if not post.invalidated and now - post.created_at >= timedelta(hours=LFG_EXPIRY_HOURS):
                await self.invalidate_post(
                    post,
                    reason=f"This request expired after {LFG_EXPIRY_HOURS} hours."
                )

    @cleanup_pending.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(LFG(bot))
