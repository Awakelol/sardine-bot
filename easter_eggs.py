import difflib
import discord
from discord.ext import commands

import json_store

CONFIG_PATH = "easter_eggs_config.json"
FUZZY_MATCH_THRESHOLD = 0.85  # 0-1, how close a misquote can be and still trigger


def _best_match_ratio(trigger: str, content: str) -> float:
    """Slides a same-length word window across content and returns the best similarity
    score against trigger, so a trigger phrase embedded in a longer message (with a typo
    or two) still gets picked up."""
    trigger_words = trigger.split()
    content_words = content.split()
    window_size = len(trigger_words)
    if len(content_words) < window_size:
        return difflib.SequenceMatcher(None, trigger, content).ratio()

    best = 0.0
    for i in range(len(content_words) - window_size + 1):
        window = " ".join(content_words[i:i + window_size])
        best = max(best, difflib.SequenceMatcher(None, trigger, window).ratio())
    return best


class EasterEggs(commands.Cog):
    """Recognizes specific pop culture lines anywhere in chat (typo-tolerant) and replies
    in kind. Add more triggers by editing easter_eggs_config.json — no code changes needed."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.entries = json_store.load_json(CONFIG_PATH, [])
        print(f"🥚 Loaded {len(self.entries)} easter egg trigger(s).")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        content = message.content.lower()
        for entry in self.entries:
            triggers = entry.get("triggers") or [entry.get("trigger", "")]
            for trigger in triggers:
                trigger = trigger.lower()
                if not trigger:
                    continue
                if trigger in content or _best_match_ratio(trigger, content) >= FUZZY_MATCH_THRESHOLD:
                    await message.reply(entry["response"], mention_author=False)
                    return


async def setup(bot: commands.Bot):
    await bot.add_cog(EasterEggs(bot))
