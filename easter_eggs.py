import difflib
import logging

import discord
from discord.ext import commands

import json_store
import mention_utils

log = logging.getLogger(__name__)

CONFIG_PATH = "easter_eggs_config.json"
FUZZY_MATCH_THRESHOLD = 0.85  # 0-1, how close a misquote can be and still trigger


def _best_match_ratio(trigger: str, content: str) -> float:
    # compare against every run of words the same length as the trigger, so a trigger with
    # a typo or two buried in a longer message still matches
    trigger_words = trigger.split()
    content_words = content.split()
    window_size = len(trigger_words)
    if len(content_words) < window_size:
        return difflib.SequenceMatcher(None, trigger, content).ratio()

    best = 0.0
    for i in range(len(content_words) - window_size + 1):
        window = " ".join(content_words[i : i + window_size])
        best = max(best, difflib.SequenceMatcher(None, trigger, window).ratio())
    return best


class EasterEggs(commands.Cog):
    """Replies to certain pop culture lines when someone mentions or replies to the bot.
    Triggers live in easter_eggs_config.json."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.entries = json_store.load_json(json_store.config_path(CONFIG_PATH), [])
        log.info("Loaded %d easter egg trigger(s)", len(self.entries))

    def find_response(self, content: str) -> str | None:
        content = content.lower()
        for entry in self.entries:
            for trigger in entry.get("triggers") or [entry.get("trigger", "")]:
                trigger = trigger.lower()
                if trigger and (trigger in content or _best_match_ratio(trigger, content) >= FUZZY_MATCH_THRESHOLD):
                    return entry["response"]
        return None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        response = self.find_response(message.content)
        if response and await mention_utils.is_directed_at_bot(self.bot, message):
            await message.reply(response, mention_author=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(EasterEggs(bot))
