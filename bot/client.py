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

    The error half of the pair says *what* went wrong, and callers route on it
    through `failure_notice`. `slow` and `unreachable` are this function's own
    verdicts on the trip; anything else came back from the server.

    **This request waits on delivery, not on the answer.** `/discord` returns
    once the message is in the pane -- `conversations.deliver` waits up to 25s
    for the TUI to draw and presses Enter for up to 20s until the text provably
    leaves the input box -- and the reply comes back later and separately
    through the Stop hook. So the timeout bounds the handover, not the turn: a
    conversation can think for an hour without touching it. It is generous
    anyway, because its expiry is no longer evidence of anything.
    """
    try:
        timeout = ClientTimeout(total=300)
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
    except asyncio.TimeoutError:
        print("[discord] post to Zipper: timed out waiting for delivery")
        return False, "slow"
    except Exception as e:
        print(f"[discord] post to Zipper failed: {e}")
        return False, "unreachable"


async def zipper_alive():
    """Is the server actually up? A cheap GET, answered by a different thread
    than the one handling a slow delivery, so a long `/discord` does not make
    this look dead."""
    try:
        async with aiohttp.ClientSession(timeout=ClientTimeout(total=5)) as s:
            async with s.get(f"{ZIPPER_URL}/api/state") as resp:
                return resp.status == 200
    except Exception:
        return False


async def failure_notice(err: str):
    """What to say in the thread about a failed post -- or `None` for nothing.

    **"Zipper disconnected" is a claim about the service, and it may only be
    made when the service is genuinely unreachable.** It used to be the message
    for every failure, including a request that merely took longer than the
    bot's patience. That produced the worst kind of wrong: an error, followed
    minutes later by the answer it said would never come. Delivery had been
    working the entire time.

    So a timeout is not evidence any more. It is ambiguous by nature -- the
    handover may still be in flight -- and it is resolved by asking the server
    whether it is alive rather than by inferring from the clock. If it answers,
    say nothing: either the message lands and the reply arrives on its own, or
    it does not and `deliver` already reported why. A false alarm is worse than
    silence here, because the typing indicator is still running and it is
    telling the truth.
    """
    if err == "no conversation":
        return ("🗄️ This thread's conversation is gone — "
                "start a new one in the channel.")
    if err in ("slow", "unreachable", ""):
        return None if await zipper_alive() else "⚠️ Zipper disconnected"
    # A real answer from a running server: the delivery itself failed, and the
    # reason is specific ("conversation not running", "message stayed in the
    # input box"). Saying "disconnected" would send him to look at systemd for
    # a problem that is in the pane.
    return f"⚠️ Couldn't deliver that: {err}"


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
            notice = await failure_notice(err)
            if notice:
                await message.channel.send(notice)
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

    ok, err = await post_to_zipper(message.content, thread.id, opening=True)
    if not ok:
        notice = await failure_notice(err)
        if notice:
            await thread.send(notice)
