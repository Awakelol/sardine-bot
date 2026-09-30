import logging
import os
import time
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands
from google import genai
from google.genai import types
from tavily import AsyncTavilyClient

import json_store
import mention_utils

log = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
# flash-lite on purpose, gemini-3.5-flash is a reasoning model and way too slow for chat
MODEL_NAME = "gemini-3.1-flash-lite"
STATE_FILE = os.path.join("data", "ai_chat_state.json")

HISTORY_TTL_SECONDS = 600  # forget the conversation after 10 min of silence
HISTORY_MAX_TURNS = 6
SEARCH_RECENCY_DAYS = 21  # leans toward recent news without cutting out older relevant stuff

# optional local time shown to the model next to UTC, e.g. TIMEZONE_LABEL="Central European Time", UTC_OFFSET_HOURS=1
TIMEZONE_LABEL = os.getenv("TIMEZONE_LABEL")
UTC_OFFSET_HOURS = float(os.getenv("UTC_OFFSET_HOURS")) if os.getenv("UTC_OFFSET_HOURS") else None

CLASSIFIER_INSTRUCTION = (
    "You look at a Discord conversation and decide whether the latest message needs a live web search "
    "(current events, scores, prices, dates, facts that change over time, anything that could be outdated) "
    "or whether it's general knowledge or casual chat that doesn't need one.\n"
    "If no search is needed, reply with exactly: CHAT\n"
    "If a search is needed, reply with exactly: SEARCH: <query>\n"
    "The query has to make sense on its own, so pull in any names, teams, events or topics mentioned earlier "
    "in the conversation. For example, if the conversation was about the 'World Cup 2026' and the latest "
    "message just says 'what's the score of Spain vs Belgium', the query should be "
    "'Spain vs Belgium World Cup 2026 score'. "
    "Reply with nothing else, no explanation."
)

SYSTEM_INSTRUCTION = (
    "You are sardine, the Discord bot for a small gaming community server. "
    "You're witty, casual and a little playful, but never mean or sarcastic in a way that stings. "
    "Keep replies SHORT by default, one or two sentences like a normal chat message. "
    "Only go longer, up to a small paragraph, when the question actually needs more explaining. "
    "Never ramble past that, and don't use lists even in longer replies. "
    "If you're not fully sure about something, say so naturally ('pretty sure, don't quote me' or "
    "'I could be wrong here') instead of stating it as fact. "
    "You can talk about current events and general knowledge when asked. "
    "Don't overuse emojis or do roleplay asterisk actions. Talk like a person texting, not a customer service bot. "
    "For anything live or ongoing (sports matches, elections, breaking news), don't assume it's over "
    "unless the info you have clearly says so. Hedge naturally instead, like 'still going as of my last check'. "
    "This is a shared channel, so messages are prefixed with 'Name: message' to show who's talking, "
    "since more than one person can reply to you in the same thread. Never put that 'Name:' prefix in your own "
    "replies, and answer whoever spoke last unless they're clearly still on an earlier point someone else made."
)


def current_time_context() -> str:
    now = datetime.now(timezone.utc)
    fmt = "%A, %B %d, %Y %H:%M"
    local = ""
    if UTC_OFFSET_HOURS is not None:
        local_now = now.astimezone(timezone(timedelta(hours=UTC_OFFSET_HOURS)))
        label = f"{TIMEZONE_LABEL}, " if TIMEZONE_LABEL else ""
        local = f" ({local_now.strftime(fmt)} {label}UTC{UTC_OFFSET_HOURS:+g})"
    return (
        f"Right now it's {now.strftime(fmt)} UTC{local}. "
        "Treat this as the real current date and time, don't work out 'today' from anything else."
    )


def user_turn(text: str) -> types.Content:
    return types.Content(role="user", parts=[types.Part(text=text)])


class AIChat(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
        self.tavily = AsyncTavilyClient(api_key=TAVILY_API_KEY) if TAVILY_API_KEY else None
        self.state = json_store.load_json(STATE_FILE, {"enabled": True})
        # keyed by channel, not user, so anyone replying in the channel shares the same context
        self.conversations = {}
        log.info(
            "AI chat using %s (gemini key: %s, tavily key: %s, enabled: %s)",
            MODEL_NAME,
            bool(GEMINI_API_KEY),
            bool(TAVILY_API_KEY),
            self.state.get("enabled", True),
        )

    def _get_history(self, channel_id: int) -> list:
        convo = self.conversations.get(channel_id)
        if not convo:
            return []
        if time.time() - convo["last_active"] > HISTORY_TTL_SECONDS:
            del self.conversations[channel_id]
            return []
        return convo["turns"]

    def _remember(self, channel_id: int, author_name: str, user_text: str, reply_text: str):
        convo = self.conversations.setdefault(channel_id, {"turns": [], "last_active": time.time()})
        # keep the name on the user turn, otherwise the model can't tell who said what
        convo["turns"].append(user_turn(f"{author_name}: {user_text}"))
        convo["turns"].append(types.Content(role="model", parts=[types.Part(text=reply_text)]))
        convo["turns"] = convo["turns"][-HISTORY_MAX_TURNS * 2 :]
        convo["last_active"] = time.time()

    async def _build_search_query(self, history: list, user_text: str) -> str | None:
        """Ask the model whether this message needs a web search, and if so for what."""
        if not self.tavily:
            return None
        try:
            result = await self.client.aio.models.generate_content(
                model=MODEL_NAME,
                contents=history + [user_turn(user_text)],
                config=types.GenerateContentConfig(
                    system_instruction=f"{current_time_context()}\n\n{CLASSIFIER_INSTRUCTION}",
                    max_output_tokens=60,
                ),
            )
        except Exception as e:
            log.warning("Search classifier failed, skipping search: %s", e)
            return None

        verdict = (result.text or "").strip()
        if not verdict.upper().startswith("SEARCH:"):
            return None
        return verdict.split(":", 1)[1].strip() or user_text

    async def _search_context(self, query: str) -> str | None:
        try:
            results = await self.tavily.search(
                query=query,
                max_results=3,
                include_answer=True,
                topic="news",
                days=SEARCH_RECENCY_DAYS,
            )
        except Exception as e:
            log.warning("Tavily search failed: %s", e)
            return None

        parts = [results["answer"]] if results.get("answer") else []
        for r in results.get("results", []):
            snippet = r.get("content")
            if not snippet:
                continue
            published = r.get("published_date")
            date_note = f" (published {published})" if published else ""
            parts.append(f"- {r.get('title', 'source')}{date_note}: {snippet[:300]}")
        return "\n".join(parts) or None

    def _clean_text(self, message: discord.Message) -> str:
        # drop the bot's own mention and turn other mentions into readable names
        text = message.content
        for user in message.mentions:
            replacement = "" if user.id == self.bot.user.id else f"@{user.display_name}"
            text = text.replace(f"<@{user.id}>", replacement).replace(f"<@!{user.id}>", replacement)
        return text.strip()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not self.client or not self.state.get("enabled", True):
            return

        eggs = self.bot.get_cog("EasterEggs")
        if eggs and eggs.find_response(message.content):
            return  # easter_eggs is handling this one

        if not await mention_utils.is_directed_at_bot(self.bot, message):
            return

        user_text = self._clean_text(message) or "Say hi and ask what they need."
        channel_id = message.channel.id
        history = self._get_history(channel_id)
        author_name = message.author.display_name

        async with message.channel.typing():
            try:
                prompt = f"{author_name}: {user_text}"
                query = await self._build_search_query(history, user_text)
                context = await self._search_context(query) if query else None
                if context:
                    prompt = (
                        f'Current web info that may help (search: "{query}"):\n{context}\n\n'
                        f"Now answer this like yourself, in your own words. The question was from "
                        f"{author_name}: {user_text}"
                    )

                response = await self.client.aio.models.generate_content(
                    model=MODEL_NAME,
                    contents=history + [user_turn(prompt)],
                    config=types.GenerateContentConfig(
                        system_instruction=f"{current_time_context()}\n\n{SYSTEM_INSTRUCTION}",
                        max_output_tokens=350,
                    ),
                )
                reply_text = response.text or "...not sure what to say to that, honestly."
            except Exception as e:
                log.error("Gemini request failed: %s", e)
                reply_text = "brain's not cooperating right now, try again in a bit."

        if len(reply_text) > 1900:  # Discord caps messages at 2000 chars
            reply_text = reply_text[:1900] + "..."

        self._remember(channel_id, author_name, user_text, reply_text)
        await message.reply(reply_text, mention_author=False)

    @app_commands.command(name="ai-toggle", description="Turn the AI chatbot on or off (admin only)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def ai_toggle(self, interaction: discord.Interaction):
        self.state["enabled"] = not self.state.get("enabled", True)
        json_store.save_json(STATE_FILE, self.state)
        status = "enabled ✅" if self.state["enabled"] else "disabled 🛑"
        await interaction.response.send_message(f"AI chatbot is now **{status}**.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AIChat(bot))
