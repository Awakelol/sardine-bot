import os
import json
import discord
from discord.ext import commands
from google import genai
from google.genai import types

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MODEL_NAME = "gemini-3.1-flash-lite"  # fast, low-latency tier — avoid gemini-3.5-flash, it's a much slower reasoning model
STATE_FILE = os.path.join("data", "ai_chat_state.json")

SYSTEM_INSTRUCTION = (
    "You are sardine, the Discord bot for a small gaming community server. "
    "You're witty, casual, and a little playful, but never mean-spirited or sarcastic in a way that stings. "
    "Keep replies SHORT — one or two sentences, like a real chat message, never a paragraph or a list. "
    "If you're not fully sure about something, say so naturally ('pretty sure, don't quote me' or "
    "'I could be wrong here') instead of stating it as fact. "
    "You can discuss current events and general knowledge when asked. "
    "Don't use excessive emojis or roleplay asterisk actions. Talk like a person texting, not a customer service bot."
)


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"enabled": True}


def save_state(data):
    os.makedirs("data", exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)


class AIChat(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
        self.state = load_state()
        print(f"🤖 AI Chat cog loaded — using model: {MODEL_NAME}")
        print(f"🔑 API key present: {bool(GEMINI_API_KEY)}, length: {len(GEMINI_API_KEY) if GEMINI_API_KEY else 0}")
        print(f"⚙️ AI chat currently {'ENABLED' if self.state.get('enabled', True) else 'DISABLED'}")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if not self.client:
            return
        if not self.state.get("enabled", True):
            return
        if not self.bot.user.mentioned_in(message):
            return

        # Strip the mention out of the message so we don't send "<@bot_id> are you cool" to the model
        user_text = message.content
        for mention in message.mentions:
            user_text = user_text.replace(f"<@{mention.id}>", "").replace(f"<@!{mention.id}>", "")
        user_text = user_text.strip()

        if not user_text:
            user_text = "Say hi and ask what they need."

        async with message.channel.typing():
            try:
                response = self.client.models.generate_content(
                    model=MODEL_NAME,
                    contents=user_text,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_INSTRUCTION,
                        max_output_tokens=150,
                        tools=[types.Tool(google_search=types.GoogleSearch())],
                    ),
                )
                reply_text = response.text or "...not sure what to say to that, honestly."
            except Exception as e:
                print(f"❌ Gemini API error: {e}")
                reply_text = "brain's not cooperating right now, try again in a bit."

        # Discord message limit safety net
        if len(reply_text) > 1900:
            reply_text = reply_text[:1900] + "..."

        await message.reply(reply_text, mention_author=False)

    @discord.app_commands.command(name="ai-toggle", description="Turn the AI chatbot on or off (admin only)")
    @discord.app_commands.checks.has_permissions(manage_guild=True)
    async def ai_toggle(self, interaction: discord.Interaction):
        current = self.state.get("enabled", True)
        self.state["enabled"] = not current
        save_state(self.state)
        status = "enabled ✅" if self.state["enabled"] else "disabled 🛑"
        await interaction.response.send_message(f"AI chatbot is now **{status}**.", ephemeral=True)

    @ai_toggle.error
    async def ai_toggle_error(self, interaction: discord.Interaction, error):
        if isinstance(error, discord.app_commands.MissingPermissions):
            await interaction.response.send_message("You need Manage Server permission to use this.", ephemeral=True)
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(AIChat(bot))
