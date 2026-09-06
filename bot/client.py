"""Discord gateway client: on_ready, on_message, post_to_zipper, resolve_thread."""

import asyncio
import aiohttp
from aiohttp import ClientTimeout
import discord

from utils.constants import ZIPPER_URL

DISCORD_CHANNEL_ID = None  # set by __init__.py at startup

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


async def post_to_zipper(prompt: str, discord_thread_id: int) -> bool:
    """Forward a message to Zipper. Returns True if Zipper acknowledged.

    Zipper decides what happens to it: pasted into a live Claude session,
    delivered to a detached one after bringing it back up, or used as the
    opening prompt of a new conversation."""
    try:
        timeout = ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(f"{ZIPPER_URL}/discord", json={
                "prompt": prompt,
                "source": "discord",
                "discord_thread_id": discord_thread_id,
            }) as resp:
                return resp.status == 200
    except Exception as e:
        print(f"[discord] post to Zipper failed: {e}")
        return False


async def resolve_thread(thread_id: int):
    await client.wait_until_ready()
    for attempt in range(1, 6):
        thread = client.get_channel(thread_id)
        if thread is None:
            try:
                thread = await client.fetch_channel(thread_id)
            except Exception as e:
                print(f"[discord] resolve_thread: fetch failed for {thread_id} (attempt {attempt}): {e}")
        if thread is not None:
            return thread
        await asyncio.sleep(min(2 ** attempt, 10))
    return None


@client.event
async def on_ready():
    print(f"[discord] logged in as {client.user}")
    print(f"[discord] listening in channel {DISCORD_CHANNEL_ID}")


@client.event
async def on_message(message: discord.Message):
    if message.author == client.user:
        return

    # Message in a thread — relay to zipper
    if isinstance(message.channel, discord.Thread):
        ok = await post_to_zipper(message.content, message.channel.id)
        if not ok:
            await message.channel.send("⚠️ Zipper disconnected")
        return

    # A message in the main channel starts a *new* conversation, so it gets its
    # own thread and Zipper answers in there. Replying inside a thread continues
    # that conversation instead (handled above), which is what lets several run
    # at once without their contexts touching.
    if message.channel.id != DISCORD_CHANNEL_ID:
        return

    title = " ".join((message.content or "new conversation").split())[:60] or "new conversation"
    try:
        thread = await message.create_thread(name=title, auto_archive_duration=1440)
        target_id = thread.id
    except Exception as e:
        # Threads can fail for reasons that are not this message's fault --
        # missing permission, a channel type that has none. Falling back to the
        # channel keeps Zipper answerable rather than silent; it just means this
        # conversation shares the channel's context like it used to.
        print(f"[discord] thread create failed: {e}")
        target_id = message.channel.id

    ok = await post_to_zipper(message.content, target_id)
    if not ok:
        await client.get_channel(target_id).send("⚠️ Zipper disconnected")
