import os
import discord
from discord.ext import commands
from google import genai
from google.genai import types

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MODEL_NAME = "gemini-3.1-flash-lite"  # fast, low-latency tier — avoid gemini-3.5-flash, it's a much slower reasoning model

SYSTEM_INSTRUCTION = (
    "You are sardine, the Discord bot for a small gaming community server. "
    "You're witty, casual, and a little playful, but never mean-spirited or sarcastic in a way that stings. "
    "Keep replies SHORT — one or two sentences, like a real chat message, never a paragraph or a list. "
    "If you're not fully sure about something, say so naturally ('pretty sure, don't quote me' or "
    "'I could be wrong here') instead of stating it as fact. "
    "You can discuss current events and general knowledge when asked. "
    "Don't use excessive emojis or roleplay asterisk actions. Talk like a person texting, not a customer service bot."
)


class AIChat(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if not self.client:
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


async def setup(bot: commands.Bot):
    await bot.add_cog(AIChat(bot))
