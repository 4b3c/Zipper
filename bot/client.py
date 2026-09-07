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


async def post_to_zipper(prompt: str, discord_thread_id: int,
                         opening: bool = False):
    """Forward a message to Zipper. Returns `(ok, error)`.

    Zipper decides what happens to it: pasted into a live Claude session,
    delivered to a detached one after bringing it back up, or used as the
    opening prompt of a new conversation.

    `opening` says the thread was created by *this* message, so there is
    supposed to be no conversation behind it yet and starting one is correct.
    Without the flag Zipper cannot tell that from a message in some long-dead
    thread, and would answer both the same way -- by starting a stranger
    underneath a visible history it has never read.
    """
    try:
        timeout = ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(f"{ZIPPER_URL}/discord", json={
                "prompt": prompt,
                "source": "discord",
                "discord_thread_id": discord_thread_id,
                "opening": opening,
            }) as resp:
                if resp.status == 200:
                    return True, ""
                try:
                    body = await resp.json()
                except Exception:
                    body = {}
                return False, str(body.get("error") or "")
    except Exception as e:
        print(f"[discord] post to Zipper failed: {e}")
        return False, ""


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

    # Message in a thread — continue that conversation, if there is one.
    #
    # A thread whose conversation Zipper does not know is answered *here*, by
    # the bot, without waking Claude Code. Starting a fresh session instead
    # would read as continuous -- the old exchange is still on screen above the
    # reply -- while actually having no memory of any of it, which is a worse
    # failure than saying so. It also costs nothing: no session, no tokens.
    if isinstance(message.channel, discord.Thread):
        ok, err = await post_to_zipper(message.content, message.channel.id)
        if not ok:
            await message.channel.send(
                "🗄️ This thread's conversation is gone — start a new one in the channel."
                if err == "no conversation" else "⚠️ Zipper disconnected")
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
    except Exception as e:
        # No thread, no conversation -- say so rather than falling back to the
        # channel id. Every conversation is keyed on a thread and the reply
        # forwarding posts to one, so a session started without a thread is one
        # whose answers cannot get back out.
        print(f"[discord] thread create failed: {e}")
        await message.channel.send(f"⚠️ Couldn't open a thread for that: {e}")
        return

    ok, _ = await post_to_zipper(message.content, thread.id, opening=True)
    if not ok:
        await thread.send("⚠️ Zipper disconnected")
