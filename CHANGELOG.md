# Changelog

## v0.2.2-alpha — 2026-07-13

- Fixed AI chat memory being scoped per-user instead of per-channel, so replying to sardine as a different user lost all context

## v0.2.1-alpha — 2026-07-13

- Added LFG auto-join: anyone who sits in the VC for 15min without clicking Join gets added to the squad automatically, including people already in the VC when the post is created

## v0.2.0-alpha — 2026-07-12

- Added live voice tracking to LFG: 10min return window for creator/members, 2hr auto-expiry with grey "old" state, new color scheme (green/blue/red/grey)
- Added creator avatar/name to the LFG embed
- Added easter egg cog for pop culture line triggers (fuzzy matched, configurable via JSON)
- Consolidated duplicate JSON load/save and duration formatting into shared `json_store.py` and `time_utils.py`
- Reorganized `bot.py` cog loading into grouped sections

## v0.1.0-alpha — 2026-07-11

- Added Unverified role on join, INTERESTS/PINGS divider roles, server tag reward role
- Added `/setup-log-channel` to log role and tag role changes
- Fixed LFG embed failing to update after 15 min, added 2hr auto-expiry
- Added AI chat conversation memory and reply-to-continue support
- Fixed AI search giving stale or wrong info with recency filtering and context-aware queries
