"""Discord gateway client: on_ready, on_message, post_to_zipper, resolve_thread."""

import asyncio
import aiohttp
from aiohttp import ClientTimeout
import discord

from utils.constants import ZIPPER_URL

DISCORD_CHANNEL_ID = None  # set by __init__.py at startup

# How long a post to /discord may take before the bot stops holding the request.
# Named because the timeout notice quotes it -- a number in the message and a
# different number in the code is how a diagnostic starts lying.
POST_TIMEOUT = 300

# The unit behind ZIPPER_URL. Knowing its name is what lets a failure say
# "inactive" instead of the catch-all "disconnected".
ZIPPER_UNIT = "zipper-web"

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
        timeout = ClientTimeout(total=POST_TIMEOUT)
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
                # A server that answered is not a server that is missing, so
                # never hand back an empty reason here -- `failure_notice`
                # reads the empty string as "could not reach it" and would
                # blame the network for an HTTP error.
                return False, str(body.get("error") or f"HTTP {resp.status}")
    except asyncio.TimeoutError:
        print("[discord] post to Zipper: timed out waiting for delivery")
        return False, "slow"
    except Exception as e:
        print(f"[discord] post to Zipper failed: {e}")
        return False, "unreachable"


async def zipper_alive():
    """Is the server actually answering? A cheap GET, served by a different
    thread than the one handling a slow delivery, so a long `/discord` cannot
    make it look dead."""
    try:
        async with aiohttp.ClientSession(timeout=ClientTimeout(total=5)) as s:
            async with s.get(f"{ZIPPER_URL}/api/state") as resp:
                return resp.status == 200
    except Exception:
        return False


async def unit_state(unit: str = None):
    """`systemctl is-active`, or `''` when systemd cannot be asked.

    A read-only query, so it needs no privilege, and it is run out of process
    so a wedged systemd cannot block the gateway's event loop. The empty string
    means *unknown* and is never reported as a state -- an unanswered question
    is not the same as a stopped service.

    The unit name is resolved at call time rather than bound as a default: a
    default argument is evaluated once, at import, so it would go on querying
    whatever `ZIPPER_UNIT` was then while the message quoted its current value
    -- a diagnostic reporting on one service and naming another.
    """
    unit = unit or ZIPPER_UNIT
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl", "is-active", unit,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        return out.decode().strip()
    except Exception:
        return ""


async def failure_notice(err: str):
    """What to say in the thread about a failed post -- or `None` for nothing.

    **Every message here names one specific failure, and may only be sent when
    that failure is the one that happened.** "Zipper disconnected" used to be
    the answer to all of them, which made it useless twice over: it was wrong
    whenever the service was fine, and even when it was right it did not say
    what to do about it. The three real ways this breaks want three different
    responses -- restart the unit, look at why a running server is refusing
    connections, wait.

    So the failure is *diagnosed* rather than assumed. Two observations
    separate them: whether systemd holds the unit active, and whether the
    server answers a cheap request.

    | what happened | unit | answers | message |
    |---|---|---|---|
    | connection refused | inactive | -- | service is down, restart it |
    | connection refused | active | -- | running but not accepting connections |
    | connection refused | unknown | no | unreachable, cause unknown |
    | no response in time | active | yes | still working, probably in flight |
    | server said no | active | yes | the delivery's own reason |

    The timeout line is deliberately not an error. `/discord` returns when the
    message reaches the pane, and the reply comes back separately through the
    Stop hook, so a slow handover that eventually lands still produces an
    answer. It says the message is probably still coming, because it probably
    is -- claiming a disconnection here is what produced an error followed
    minutes later by the reply it said would never arrive.
    """
    if err == "no conversation":
        return ("🗄️ This thread's conversation is gone — "
                "start a new one in the channel.")

    if err == "slow":
        # Held the request for POST_TIMEOUT and got nothing back. If the server
        # is answering, the handover is the slow part, not the connection.
        if await zipper_alive():
            return (f"⏳ Zipper hasn't answered in {POST_TIMEOUT}s, but the service "
                    "is up — the message is most likely still being delivered. "
                    "The reply will arrive on its own if it lands; resend if it "
                    "doesn't.")
        err = "unreachable"                # not slow, gone -- fall through

    if err in ("unreachable", ""):
        state = await unit_state()
        if state == "active":
            return (f"⚠️ Zipper is unreachable — `{ZIPPER_UNIT}` is active but not "
                    f"accepting connections on {ZIPPER_URL}. It may be wedged or "
                    "mid-restart.")
        if state in ("activating", "deactivating", "reloading"):
            return (f"🔄 Zipper is restarting — `{ZIPPER_UNIT}` is {state}. "
                    "Send that again in a moment.")
        if state:
            return (f"⚠️ Zipper's service isn't running — `{ZIPPER_UNIT}` is {state}, "
                    "so nothing can be delivered until it's started again.")
        return (f"⚠️ Can't reach Zipper at {ZIPPER_URL}, and systemd couldn't be "
                f"asked about `{ZIPPER_UNIT}`.")

    # A running server that answered with a reason of its own: the connection
    # was fine and the *delivery* failed. Saying "disconnected" would send him
    # to look at systemd for a problem that is in the pane.
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
