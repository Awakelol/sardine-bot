import os
import json
import time
import discord
from datetime import datetime, timedelta, timezone
from discord.ext import commands
from google import genai
from google.genai import types
from tavily import TavilyClient

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
MODEL_NAME = "gemini-3.1-flash-lite"  # fast, low-latency tier — avoid gemini-3.5-flash, it's a much slower reasoning model
STATE_FILE = os.path.join("data", "ai_chat_state.json")

HISTORY_TTL_SECONDS = 600  # forget the conversation after 10 min of silence
HISTORY_MAX_TURNS = 6  # keep last 6 back-and-forths per conversation
SEARCH_RECENCY_DAYS = 21  # bias search results toward the last ~3 weeks without excluding older-but-relevant news

CLASSIFIER_INSTRUCTION = (
    "You look at a Discord conversation and decide whether the latest message needs a live web search "
    "(current events, scores, prices, dates, facts that change over time, anything that could be outdated) "
    "versus general knowledge or casual chat that doesn't need one. "
    "If no search is needed, reply with exactly: CHAT\n"
    "If a search is needed, reply with exactly: SEARCH: <query>\n"
    "The <query> must be a fully self-contained search query — pull in any names, teams, events, or topics "
    "mentioned earlier in the conversation so it makes sense with zero other context. "
    "For example if earlier the conversation was about the 'World Cup 2026' and the latest message just says "
    "'what's the score of Spain vs Belgium', the query should be 'Spain vs Belgium World Cup 2026 score'. "
    "Reply with nothing else — no explanation."
)

SYSTEM_INSTRUCTION = (
    "You are sardine, the Discord bot for a small gaming community server. "
    "You're witty, casual, and a little playful, but never mean-spirited or sarcastic in a way that stings. "
    "Default to SHORT replies — one or two sentences, like a real chat message. "
    "Only go longer, up to a small paragraph, when the question actually needs more explaining to make sense. "
    "Never ramble past that, and avoid list formatting even in longer replies. "
    "If you're not fully sure about something, say so naturally ('pretty sure, don't quote me' or "
    "'I could be wrong here') instead of stating it as fact. "
    "You can discuss current events and general knowledge when asked. "
    "Don't use excessive emojis or roleplay asterisk actions. Talk like a person texting, not a customer service bot. "
    "For anything live or ongoing (sports matches, elections, breaking news), don't assume it has concluded "
    "unless the info you have clearly says so — hedge naturally instead ('still going as of my last check', etc)."
)


def current_time_context() -> str:
    now_utc = datetime.now(timezone.utc)
    now_pht = now_utc + timedelta(hours=8)
    return (
        f"Right now it's {now_utc.strftime('%A, %B %d, %Y %H:%M')} UTC "
        f"({now_pht.strftime('%A, %B %d, %Y %H:%M')} Philippine Time, UTC+8). "
        "Treat this as the real current date/time — don't estimate 'today' from anything else."
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
        self.tavily = TavilyClient(api_key=TAVILY_API_KEY) if TAVILY_API_KEY else None
        self.state = load_state()
        self.conversations = {}  # (channel_id, user_id) -> {"turns": [...], "last_active": float}
        print(f"🤖 AI Chat cog loaded — using model: {MODEL_NAME}")
        print(f"🔑 Gemini key present: {bool(GEMINI_API_KEY)}, Tavily key present: {bool(TAVILY_API_KEY)}")
        print(f"⚙️ AI chat currently {'ENABLED' if self.state.get('enabled', True) else 'DISABLED'}")

    def _get_history(self, key) -> list:
        convo = self.conversations.get(key)
        if not convo:
            return []
        if time.time() - convo["last_active"] > HISTORY_TTL_SECONDS:
            del self.conversations[key]
            return []
        return convo["turns"]

    def _remember(self, key, user_text: str, reply_text: str):
        convo = self.conversations.setdefault(key, {"turns": [], "last_active": time.time()})
        convo["turns"].append(types.Content(role="user", parts=[types.Part(text=user_text)]))
        convo["turns"].append(types.Content(role="model", parts=[types.Part(text=reply_text)]))
        convo["turns"] = convo["turns"][-HISTORY_MAX_TURNS * 2:]
        convo["last_active"] = time.time()

    async def _is_directed_at_bot(self, message: discord.Message) -> bool:
        if self.bot.user.mentioned_in(message):
            return True
        if message.reference:
            resolved = message.reference.resolved
            if resolved is None:
                try:
                    resolved = await message.channel.fetch_message(message.reference.message_id)
                except discord.HTTPException:
                    return False
            return isinstance(resolved, discord.Message) and resolved.author.id == self.bot.user.id
        return False

    def _build_search_query(self, history: list, user_text: str) -> str | None:
        """Returns a self-contained search query if this message needs a live search, else None."""
        if not self.tavily:
            return None
        try:
            result = self.client.models.generate_content(
                model=MODEL_NAME,
                contents=history + [types.Content(role="user", parts=[types.Part(text=user_text)])],
                config=types.GenerateContentConfig(
                    system_instruction=f"{current_time_context()}\n\n{CLASSIFIER_INSTRUCTION}",
                    max_output_tokens=60,
                ),
            )
            verdict = (result.text or "").strip()
            if verdict.upper().startswith("SEARCH:"):
                query = verdict.split(":", 1)[1].strip()
                return query or user_text
            return None
        except Exception as e:
            print(f"⚠️ Classifier error, defaulting to no search: {e}")
            return None

    def _search_context(self, query: str) -> str | None:
        try:
            results = self.tavily.search(
                query=query,
                max_results=3,
                include_answer=True,
                topic="news",
                days=SEARCH_RECENCY_DAYS,
            )
        except Exception as e:
            print(f"⚠️ Tavily search error: {e}")
            return None

        parts = []
        if results.get("answer"):
            parts.append(results["answer"])
        for r in results.get("results", []):
            snippet = r.get("content", "")
            if snippet:
                published = r.get("published_date")
                date_note = f" (published {published})" if published else ""
                parts.append(f"- {r.get('title', 'source')}{date_note}: {snippet[:300]}")

        return "\n".join(parts) if parts else None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if not self.client:
            return
        if not self.state.get("enabled", True):
            return
        if not await self._is_directed_at_bot(message):
            return

        # Strip the mention out of the message so we don't send "<@bot_id> are you cool" to the model
        user_text = message.content
        for mention in message.mentions:
            user_text = user_text.replace(f"<@{mention.id}>", "").replace(f"<@!{mention.id}>", "")
        user_text = user_text.strip()

        if not user_text:
            user_text = "Say hi and ask what they need."

        history_key = (message.channel.id, message.author.id)
        history = self._get_history(history_key)

        async with message.channel.typing():
            try:
                prompt = user_text
                search_query = self._build_search_query(history, user_text)
                if search_query:
                    context = self._search_context(search_query)
                    if context:
                        prompt = (
                            f"Current web info that may help (search: \"{search_query}\"):\n{context}\n\n"
                            f"Now answer this like yourself, in your own words: {user_text}"
                        )

                response = self.client.models.generate_content(
                    model=MODEL_NAME,
                    contents=history + [types.Content(role="user", parts=[types.Part(text=prompt)])],
                    config=types.GenerateContentConfig(
                        system_instruction=f"{current_time_context()}\n\n{SYSTEM_INSTRUCTION}",
                        max_output_tokens=350,
                    ),
                )
                reply_text = response.text or "...not sure what to say to that, honestly."
            except Exception as e:
                print(f"❌ Gemini API error: {e}")
                reply_text = "brain's not cooperating right now, try again in a bit."

        # Discord message limit safety net
        if len(reply_text) > 1900:
            reply_text = reply_text[:1900] + "..."

        self._remember(history_key, user_text, reply_text)
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
