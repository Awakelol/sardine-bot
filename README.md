# sardine-bot

A Discord bot for small gaming community servers. It sits in a voice channel
around the clock, gives out roles through a dropdown menu, helps people find a
squad with `/lfg`, and answers when you mention it, using Gemini with optional
live web search.

## Features

- **Voice presence.** Stays in one voice channel, reconnects on its own, and
  times how long the channel has been continuously occupied. Session start,
  resume and end are announced in a log channel.
- **Voice leaderboard.** Tracks how long each member spends in that channel
  and shows the top three with `/voice-leaderboard`.
- **Role menu.** A persistent button opens dropdowns per role category.
  Categories can share a "divider" role that a member keeps as long as they
  hold any role from those categories.
- **LFG.** `/lfg` walks through game, mode, rank and player count, then posts
  a card with a Join button. The card follows the voice channel live: members
  who leave get a grace period to return, anyone who idles in the channel
  long enough is added automatically, and posts expire after two hours.
- **AI chat.** Mention or reply to the bot to talk to it. It keeps a short
  per-channel memory, and it decides per message whether a Tavily web search
  would help before answering.
- **Easter eggs.** Fuzzy-matched trigger lines with canned replies, set in a
  JSON file.
- **Rotating status.** Cycles through statuses from a JSON file. Admins can
  pin a temporary one with `/set-temp-status`.
- **Server tag role.** Gives a role to members who equip the server's tag
  and removes it when they take the tag off.
- **Join role.** Gives new members a role on join, for use with a
  verification or captcha bot.

## Requirements

- Python 3.12+
- A Discord application with a bot user
- Optional: a [Gemini API key](https://aistudio.google.com/apikey) for AI
  chat and a [Tavily API key](https://tavily.com) for web search

## Setup

1. Create an application at the
   [Discord Developer Portal](https://discord.com/developers/applications).
   Under **Bot**, copy the token and enable the **Server Members** and
   **Message Content** privileged intents.
2. Invite the bot with the `bot` and `applications.commands` scopes and these
   permissions: View Channels, Send Messages, Embed Links, Read Message
   History, Manage Roles, Connect, Move Members.
3. In your server, move the bot's role above every role it should hand out.
4. Install and configure:

   ```sh
   git clone https://github.com/Awakelol/sardine-bot.git
   cd sardine-bot
   python -m venv venv
   venv\Scripts\activate          # Windows
   # source venv/bin/activate     # macOS/Linux
   pip install -r requirements.txt
   cp .env.example .env           # then fill it in
   ```

5. Optionally copy any of the `*.example.json` files to the same name without
   `.example` and edit them (see [Configuration](#configuration)).
6. Run it:

   ```sh
   python bot.py
   ```

   Slash commands are synced globally on startup. Discord can take a few
   minutes to show new ones.

## Configuration

### Environment (`.env`)

| Setting | Required | Description |
| --- | --- | --- |
| `DISCORD_TOKEN` | yes | Bot token. |
| `VOICE_CHANNEL_ID` | no | Voice channel to stay in and track. Empty turns voice presence and stats off. |
| `VOICE_LOG_CHANNEL_ID` | no | Text channel for voice session announcements. |
| `UNVERIFIED_ROLE_NAME` | no | Role given on join. Default `Unverified`. Skipped with a warning if the role doesn't exist. |
| `TAG_ROLE_NAME` | no | Role given to members wearing the server tag. Default `Server Tag`. |
| `GEMINI_API_KEY` | no | Enables AI chat. |
| `TAVILY_API_KEY` | no | Enables web search for AI chat. |
| `TIMEZONE_LABEL`, `UTC_OFFSET_HOURS` | no | Local time given to the model next to UTC, e.g. `Central European Time` and `1`. |

To copy a channel ID, turn on Developer Mode in Discord (Settings > Advanced),
then right-click the channel.

### JSON files

Each file is read from `<name>.json` if it exists, otherwise from the
committed `<name>.example.json`. The non-example files are gitignored, so your
server's roles, statuses and in-jokes stay out of the repo.

- `roles_config.json`: role menu categories. Each category has a
  `description`, `max_values`, an optional `divider_role`, and a list of
  `roles` (`label`, `emoji`, `role_name`). The roles must already exist in the
  server, with names that match exactly.
- `status_config.json`: `rotate_seconds` and a list of `statuses`, each a
  `type` (`playing`, `watching`, `listening`, `competing`) and `text`.
  Changes are picked up without a restart.
- `easter_eggs_config.json`: a list of entries with either `trigger` or
  `triggers` and a `response`. Loaded at startup.

The LFG games, per-game player caps and timings are constants at the top of
`lfg.py`.

## Usage

| Command | Who | What it does |
| --- | --- | --- |
| `/ping` | everyone | Checks the bot is alive. |
| `/lfg` | everyone (in a voice channel) | Posts a Looking For Group card. |
| `/voice-leaderboard` | everyone | Top three by time in the tracked voice channel. |
| `/post-role-menu` | Manage Roles | Posts the role menu button in the current channel. |
| `/setup-log-channel` | Manage Server | Sets where role and tag changes are logged. |
| `/voice-timer` | Manage Server | Views or resets the voice session timer. |
| `/voice-timer-add` | Manage Server | Adds or subtracts minutes from the timer. |
| `/set-temp-status` | Manage Server | Shows a status for N minutes, then resumes rotation. |
| `/ai-toggle` | Manage Server | Turns AI chat on or off. |

To chat with the bot, @mention it or reply to one of its messages.

## How it works

`bot.py` loads each feature as a discord.py cog in `setup_hook` and
registers the role menu views as persistent, so their buttons keep working
across restarts. Shared helpers live in `json_store.py` (atomic JSON
read/write, example-file fallback), `log_utils.py` (per-server log channel),
`mention_utils.py` and `time_utils.py`.

Runtime state goes to `data/`: the voice timer start time, per-member voice
totals, the AI chat on/off flag, any temporary status and the log channel for
each server. The voice timer stores its start time rather than a running
count, so time spent while the bot is restarting still counts as long as
someone stays in the channel.

For AI chat, a first short Gemini call classifies the message as `CHAT` or
`SEARCH: <query>`, using the recent conversation to make the query
self-contained. If it's a search, the top Tavily news results from the last
three weeks go into the prompt. Conversation memory is per channel, keeps the
last six exchanges, and resets after ten minutes of silence.

## Limitations

- Voice presence and voice stats track a single voice channel, so they're
  meant for one server per bot instance.
- LFG posts live in memory. After a restart, old cards stop updating and
  their Join buttons no longer respond.
- Voice stats sessions in progress when the bot stops are not saved.
- AI chat memory is in memory only and is lost on restart.
- The role menu matches roles by name, so renaming a role in Discord means
  updating `roles_config.json` too.

## License

[MIT](LICENSE)
