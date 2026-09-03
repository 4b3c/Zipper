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

    # Message in the main channel — create a thread, then relay to zipper
    if message.channel.id != DISCORD_CHANNEL_ID:
        return

    thread = await message.create_thread(name=message.content[:50])
    ok = await post_to_zipper(message.content, thread.id)
    if not ok:
        await thread.send("⚠️ Zipper disconnected")
