import os
import json
import discord

CONFIG_PATH = os.path.join("data", "log_channel_config.json")


def _load() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save(data: dict):
    os.makedirs("data", exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f)


def set_log_channel(guild_id: int, channel_id: int):
    data = _load()
    data[str(guild_id)] = channel_id
    _save(data)


def get_log_channel_id(guild_id: int):
    return _load().get(str(guild_id))


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
