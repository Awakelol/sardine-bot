import discord


async def is_directed_at_bot(bot: discord.Client, message: discord.Message) -> bool:
    """True if the message @mentions the bot, or is a reply to one of the bot's own messages."""
    if bot.user.mentioned_in(message):
        return True
    if message.reference:
        resolved = message.reference.resolved
        if resolved is None:
            try:
                resolved = await message.channel.fetch_message(message.reference.message_id)
            except discord.HTTPException:
                return False
        return isinstance(resolved, discord.Message) and resolved.author.id == bot.user.id
    return False
