# Changelog

## v0.3.0-alpha - 2026-09-30

- Server-specific settings moved out of the repo: `status_config.json`, `roles_config.json` and `easter_eggs_config.json` are now local files, with `*.example.json` defaults used when they're missing
- Added `UNVERIFIED_ROLE_NAME`, `TAG_ROLE_NAME`, `TIMEZONE_LABEL` and `UTC_OFFSET_HOURS` settings
- Slash commands that fail with an unexpected error now reply with an error message instead of timing out
- Pinned minimum dependency versions, added README, LICENSE, ruff config and `.gitattributes`

## v0.2.6-alpha - 2026-09-29

- Fixed two rotating statuses using an invalid "waiting" type (showed as Playing), changed to Watching

## v0.2.5-alpha - 2026-09-29

- Fixed custom game / custom player count in `/lfg` failing (Discord doesn't allow a modal to open another modal), now asks for everything in one form
- Fixed AI chat blocking the whole bot while waiting on Gemini/Tavily, switched to their async clients
- Fixed double reply when a message hit an easter egg and AI chat at the same time
- Fixed divider roles lagging one selection behind in the role menu
- Fixed voice stats not picking up people already in the VC on startup (cogs now load in `setup_hook`)
- Old LFG posts are now dropped from memory once they expire
- `/set-temp-status` applies immediately instead of on the next rotation
- Moved the duplicated "missing permissions" handlers into one global handler
- Switched `print` to `logging`, JSON saves are now atomic
- Added `data/` to `.gitignore` and a `.env.example`

## v0.2.4-alpha - 2026-07-25

- Added two rotating statuses
- Added `/set-temp-status` admin command to show a temporary status for a set number of minutes, then resume normal rotation automatically

## v0.2.3-alpha - 2026-07-16

- Fixed easter eggs firing on any message containing a trigger word (e.g. "mama") instead of only when sardine is mentioned or replied to; extracted shared mention/reply-detection into mention_utils.py

## v0.2.2-alpha - 2026-07-14

- Fixed AI chat memory being scoped per-user instead of per-channel, so replying to sardine as a different user lost all context

## v0.2.1-alpha - 2026-07-14

- Added LFG auto-join: anyone who sits in the VC for 15min without clicking Join gets added to the squad automatically, including people already in the VC when the post is created

## v0.2.0-alpha - 2026-07-12

- Added live voice tracking to LFG: 10min return window for creator/members, 2hr auto-expiry with grey "old" state, new color scheme (green/blue/red/grey)
- Added creator avatar/name to the LFG embed
- Added easter egg cog for pop culture line triggers (fuzzy matched, configurable via JSON)
- Consolidated duplicate JSON load/save and duration formatting into shared `json_store.py` and `time_utils.py`
- Reorganized `bot.py` cog loading into grouped sections

## v0.1.0-alpha - 2026-07-11

- Added Unverified role on join, INTERESTS/PINGS divider roles, server tag reward role
- Added `/setup-log-channel` to log role and tag role changes
- Fixed LFG embed failing to update after 15 min, added 2hr auto-expiry
- Added AI chat conversation memory and reply-to-continue support
- Fixed AI search giving stale or wrong info with recency filtering and context-aware queries
