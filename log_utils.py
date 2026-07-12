import os
import discord

import json_store

CONFIG_PATH = os.path.join("data", "log_channel_config.json")


def set_log_channel(guild_id: int, channel_id: int):
    data = json_store.load_json(CONFIG_PATH, {})
    data[str(guild_id)] = channel_id
    json_store.save_json(CONFIG_PATH, data)


def get_log_channel_id(guild_id: int):
    return json_store.load_json(CONFIG_PATH, {}).get(str(guild_id))


async def send_log(bot: discord.Client, guild: discord.Guild, message: str):
    channel_id = get_log_channel_id(guild.id)
    if channel_id is None:
        return

    channel = guild.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except discord.HTTPException:
            return

    try:
        await channel.send(message)
    except discord.HTTPException as e:
        print(f"⚠️ Failed to send log message: {e}")
