# Zipper Discord Bot

A thin relay between Discord and the Zipper server. It holds the only gateway connection in
the system; it holds no conversation state at all.

## Architecture

```
bot/
├── discord_bot.py   # entry point — asyncio.run(main())
├── __init__.py      # main() — aiohttp server + Discord client startup
├── client.py        # the gateway: on_ready, on_message, post_to_zipper, resolve_thread
└── server.py        # HTTP surface: /send, /history, /edit, /react, /inject, /typing
```

**Inbound** — a message in the channel:

1. `client.py` POSTs it to the server's `/discord` endpoint
2. the server delivers it into the live Claude session — pasting into a running
   conversation, waking a detached one, or starting a new one primed with the message
3. Claude replies by running `python3 -m zipper discord send "..."`, which POSTs to this
   bot's `/send`
4. the bot posts it **in the channel**

Replies land in the channel, not in a thread. Until 2026-09-03 every message opened a thread
named after its first 50 characters, which made one conversation per sentence. Messages that
arrive in an existing thread are still relayed, so anything opened before that keeps working.

## Service

```bash
systemctl status zipper-discord
systemctl restart zipper-discord
journalctl -u zipper-discord -f
```

The unit is `deploy/zipper-discord.service`; install it with:

```bash
cp deploy/zipper-discord.service /etc/systemd/system/
systemctl daemon-reload
```

Environment file: `/opt/zipper/.env`. Listens on `127.0.0.1:4200`.

## Environment

```
DISCORD_TOKEN=          # the bot token
DISCORD_CHANNEL_ID=     # the one channel it listens in
BOT_URL=http://127.0.0.1:4200      # where zipper sends replies
ZIPPER_URL=http://127.0.0.1:8800   # where this bot forwards messages
```

This is the only part of the system with a pip dependency (`discord.py`, `aiohttp`). The
engine stays stdlib-only, which is what lets a cron job or a finished background task speak
without owning a socket.
