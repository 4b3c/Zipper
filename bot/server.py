"""aiohttp HTTP server — all handle_* route handlers."""

import asyncio
import io
import discord
from aiohttp import web

from utils.text import smart_split
from bot.client import client, post_to_zipper, resolve_thread, DISCORD_CHANNEL_ID

# Active typing tasks keyed by thread_id
_typing_tasks: dict[int, asyncio.Task] = {}


async def _typing_loop(channel):
    """Send a typing indicator every 8 seconds until cancelled."""
    while True:
        try:
            await channel._state.http.send_typing(channel.id)
        except Exception as e:
            print(f"[discord] typing error: {e}")
        await asyncio.sleep(8)


async def handle_inject(request: web.Request) -> web.Response:
    """Forward a synthetic prompt to zipper for a given thread."""
    try:
        body = await request.json()
        prompt = body.get("prompt", "").strip()
        thread_id = body.get("thread_id")
        if not prompt:
            return web.json_response({"error": "prompt required"}, status=400)
        if thread_id is None:
            return web.json_response({"error": "thread_id required"}, status=400)
        try:
            thread_id = int(thread_id)
        except (TypeError, ValueError):
            return web.json_response({"error": "thread_id must be an integer"}, status=400)

        async def _inject():
            ok = await post_to_zipper(prompt, thread_id)
            if not ok:
                thread = await resolve_thread(thread_id)
                if thread:
                    await thread.send("⚠️ Zipper disconnected")

        asyncio.create_task(_inject())
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def handle_send(request: web.Request) -> web.Response:
    """Send a message and/or file synchronously and return its ID."""
    try:
        # Handle both JSON and multipart requests
        if request.content_type and 'multipart' in request.content_type:
            reader = await request.multipart()
            fields = {}
            file_data = None
            file_name = "file"
            async for field in reader:
                if field.name == "file":
                    file_name = field.filename or "file"
                    file_data = await field.read()
                else:
                    fields[field.name] = await field.text()
            message = fields.get("message", "").strip()
            thread_id = fields.get("thread_id")
        else:
            body = await request.json()
            message = body.get("message", "").strip()
            thread_id = body.get("thread_id")
            file_data = None
            file_name = "file"

        if not message and not file_data:
            return web.json_response({"error": "message or file required"}, status=400)
        if not client.is_ready():
            return web.json_response({"error": "discord client not ready"}, status=503)

        # Import at call time to pick up the module-level DISCORD_CHANNEL_ID set by __init__
        import bot.client as _client_mod
        channel_id = _client_mod.DISCORD_CHANNEL_ID
        target = client.get_channel(int(thread_id)) if thread_id else client.get_channel(channel_id)
        if target is None:
            return web.json_response({"error": "channel not found"}, status=404)

        # Prepare file attachment if provided
        file_obj = None
        if file_data:
            if len(file_data) > 8 * 1024 * 1024:  # 8MB Discord limit for free servers
                return web.json_response({
                    "error": f"file too large: {len(file_data) / 1024 / 1024:.1f}MB (Discord limit: 8MB)"
                }, status=400)
            file_obj = discord.File(io.BytesIO(file_data), filename=file_name)

        try:
            last_msg = None
            if message:
                chunks = smart_split(message)
                for i, chunk in enumerate(chunks):
                    # attach file to the last chunk only
                    f = file_obj if (i == len(chunks) - 1 and file_obj) else None
                    last_msg = await asyncio.wait_for(target.send(chunk, file=f), timeout=60)
            else:
                last_msg = await asyncio.wait_for(target.send(file=file_obj), timeout=60)
            return web.json_response({"ok": True, "message_id": str(last_msg.id)})
        except asyncio.TimeoutError:
            return web.json_response({"error": "send timed out"}, status=504)
        except Exception as e:
            return web.json_response({"error": f"send failed: {type(e).__name__}: {str(e)}"}, status=500)
    except Exception as e:
        return web.json_response({"error": f"send error: {str(e)}"}, status=500)


async def handle_history(request: web.Request) -> web.Response:
    """Return recent messages from a channel or thread."""
    try:
        body = await request.json()
        if not client.is_ready():
            return web.json_response({"error": "discord client not ready"}, status=503)
        thread_id = body.get("thread_id")
        limit = min(int(body.get("limit", 20)), 100)
        import bot.client as _client_mod
        channel_id = _client_mod.DISCORD_CHANNEL_ID
        target = client.get_channel(int(thread_id)) if thread_id else client.get_channel(channel_id)
        if target is None:
            return web.json_response({"error": "channel not found"}, status=404)
        messages = []
        async for msg in target.history(limit=limit):
            entry = {
                "id": str(msg.id),
                "author": msg.author.display_name,
                "content": msg.content,
                "timestamp": msg.created_at.isoformat(),
            }
            if msg.thread:
                entry["thread_id"] = str(msg.thread.id)
            messages.append(entry)
        return web.json_response({"messages": messages})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def handle_edit(request: web.Request) -> web.Response:
    """Edit a message by ID."""
    try:
        body = await request.json()
        if not client.is_ready():
            return web.json_response({"error": "discord client not ready"}, status=503)
        message_id = body.get("message_id")
        content = body.get("content", "").strip()
        if not message_id or not content:
            return web.json_response({"error": "message_id and content required"}, status=400)
        thread_id = body.get("thread_id")
        import bot.client as _client_mod
        channel_id = _client_mod.DISCORD_CHANNEL_ID
        target = client.get_channel(int(thread_id)) if thread_id else client.get_channel(channel_id)
        if target is None:
            return web.json_response({"error": "channel not found"}, status=404)
        msg = await target.fetch_message(int(message_id))
        await msg.edit(content=content)
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def handle_react(request: web.Request) -> web.Response:
    """Add a reaction to a message."""
    try:
        body = await request.json()
        if not client.is_ready():
            return web.json_response({"error": "discord client not ready"}, status=503)
        message_id = body.get("message_id")
        emoji = body.get("emoji", "").strip()
        if not message_id or not emoji:
            return web.json_response({"error": "message_id and emoji required"}, status=400)
        if emoji.startswith("<:") or emoji.startswith("<a:"):
            return web.json_response({"error": "custom server emoji not supported, use standard unicode emoji only"}, status=400)
        thread_id = body.get("thread_id")
        import bot.client as _client_mod
        channel_id = _client_mod.DISCORD_CHANNEL_ID
        target = client.get_channel(int(thread_id)) if thread_id else client.get_channel(channel_id)
        if target is None:
            return web.json_response({"error": "channel not found"}, status=404)
        msg = await target.fetch_message(int(message_id))
        await msg.add_reaction(emoji)
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def handle_typing(request: web.Request) -> web.Response:
    """Start or stop the typing indicator for a thread."""
    try:
        body = await request.json()
        thread_id = body.get("thread_id")
        active = body.get("active", False)
        if thread_id is None:
            return web.json_response({"error": "thread_id required"}, status=400)
        thread_id = int(thread_id)

        existing = _typing_tasks.pop(thread_id, None)
        if existing:
            existing.cancel()

        if active:
            if not client.is_ready():
                return web.json_response({"error": "discord client not ready"}, status=503)
            channel = client.get_channel(thread_id) or await client.fetch_channel(thread_id)
            await channel._state.http.send_typing(channel.id)
            _typing_tasks[thread_id] = asyncio.create_task(_typing_loop(channel))

        return web.json_response({"ok": True})
    except Exception as e:
        print(f"[discord] typing setup error: {e}")
        return web.json_response({"error": str(e)}, status=500)


async def handle_thread(request: web.Request) -> web.Response:
    """Open a thread on a new channel message and return its id.

    Zipper's own messages never come back through on_message -- the client
    ignores itself -- so a conversation that starts on this side has no thread
    unless one is made deliberately. That is what this is for: adopting a
    session that began in the terminal so it can be carried on from a phone.
    """
    try:
        body = await request.json()
        message = (body.get("message") or "").strip()
        name = (body.get("name") or message or "conversation")[:60]
        if not message:
            return web.json_response({"error": "message required"}, status=400)
        if not client.is_ready():
            return web.json_response({"error": "discord client not ready"}, status=503)
        import bot.client as _client_mod
        channel = client.get_channel(_client_mod.DISCORD_CHANNEL_ID)
        if channel is None:
            return web.json_response({"error": "channel not found"}, status=404)
        msg = await channel.send(smart_split(message)[0])
        thread = await msg.create_thread(name=name, auto_archive_duration=1440)
        for chunk in smart_split(message)[1:]:
            await thread.send(chunk)
        return web.json_response({"ok": True, "thread_id": str(thread.id),
                                  "message_id": str(msg.id)})
    except Exception as e:
        return web.json_response({"error": f"{type(e).__name__}: {e}"}, status=500)


async def handle_threadinfo(request: web.Request) -> web.Response:
    """The Discord-side name of a thread.

    The list in the dashboard should read exactly like Discord does, and Discord
    is the only thing that knows what it called a thread -- a title guessed from
    the message that started one drifts the moment anybody renames it.
    """
    try:
        body = await request.json()
        thread_id = body.get("thread_id")
        if thread_id is None:
            return web.json_response({"error": "thread_id required"}, status=400)
        thread = await resolve_thread(int(thread_id))
        if thread is None:
            return web.json_response({"error": "thread not found"}, status=404)
        return web.json_response({"ok": True, "name": getattr(thread, "name", ""),
                                  "archived": bool(getattr(thread, "archived", False))})
    except Exception as e:
        return web.json_response({"error": f"{type(e).__name__}: {e}"}, status=500)


async def handle_threadrename(request: web.Request) -> web.Response:
    """Rename a thread.

    A conversation started from the dashboard opens its thread before anyone
    knows what it is about, so the thread is born with a placeholder name. Once
    Claude has titled the conversation, the thread should say the same thing --
    otherwise the phone shows a list of timestamps and finding the right one
    means opening all of them.
    """
    try:
        body = await request.json()
        thread_id = body.get("thread_id")
        name = (body.get("name") or "").strip()[:100]
        if thread_id is None or not name:
            return web.json_response({"error": "thread_id and name required"}, status=400)
        thread = await resolve_thread(int(thread_id))
        if thread is None:
            return web.json_response({"error": "thread not found"}, status=404)
        await thread.edit(name=name)
        return web.json_response({"ok": True, "name": name})
    except Exception as e:
        return web.json_response({"error": f"{type(e).__name__}: {e}"}, status=500)


def setup_routes(app: web.Application):
    app.router.add_post("/send", handle_send)
    app.router.add_post("/history", handle_history)
    app.router.add_post("/edit", handle_edit)
    app.router.add_post("/react", handle_react)
    app.router.add_post("/inject", handle_inject)
    app.router.add_post("/typing", handle_typing)
    app.router.add_post("/thread", handle_thread)
    app.router.add_post("/threadinfo", handle_threadinfo)
    app.router.add_post("/threadrename", handle_threadrename)
