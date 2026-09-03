"""zipper.chat

Talking to the always-on Discord bot over its HTTP API.
"""
import os, json, uuid
import urllib.error, urllib.request

from .core import *          # noqa: F401,F403 -- the shared vocabulary
from . import core


#
# The bot is a separate always-on process holding the Discord gateway
# connection. This is the only way anything else talks to it: a few HTTP calls
# to BOT_URL, stdlib-only, so zipper keeps its no-dependency promise and an
# agent session can reach Discord by running a command rather than importing a
# library.

BOT_URL = os.environ.get('BOT_URL', 'http://127.0.0.1:4200')

def _bot(path, payload, timeout=30):
    req = urllib.request.Request(
        BOT_URL.rstrip('/') + path,
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8') or '{}')

def _bot_multipart(path, message, file_path, thread_id=None, timeout=120):
    """One small multipart encoder, so sending a file needs no requests library."""
    boundary = '----zipper%s' % uuid.uuid4().hex
    name = os.path.basename(file_path)
    with open(file_path, 'rb') as fh:
        blob = fh.read()
    parts = []
    def field(k, v):
        parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                      % (boundary, k, v)).encode('utf-8'))
    if message:
        field('message', message)
    if thread_id:
        field('thread_id', str(thread_id))
    parts.append(('--%s\r\nContent-Disposition: form-data; name="file"; filename="%s"\r\n'
                  'Content-Type: application/octet-stream\r\n\r\n' % (boundary, name)).encode('utf-8'))
    parts.append(blob)
    parts.append(('\r\n--%s--\r\n' % boundary).encode('utf-8'))
    body = b''.join(parts)
    req = urllib.request.Request(
        BOT_URL.rstrip('/') + path, data=body,
        headers={'Content-Type': 'multipart/form-data; boundary=' + boundary})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8') or '{}')

def discord_send(message, file_path=None, thread_id=None):
    if file_path:
        return _bot_multipart('/send', message, os.path.expanduser(file_path), thread_id)
    return _bot('/send', {'message': message, 'thread_id': thread_id})

def discord_history(limit=5, thread_id=None):
    return _bot('/history', {'limit': limit, 'thread_id': thread_id}).get('messages', [])

def cmd_discord(a):
    """Everything an agent session needs: say something, read what was said,
    hand over a file. Deliberately four verbs and no state."""
    try:
        if a.action == 'send':
            if not a.text and not a.file:
                print('discord: nothing to send'); return 1
            r = discord_send(a.text or '', a.file, a.thread)
            if r.get('error'):
                print('discord: %s' % r['error']); return 1
            print('discord: sent%s (id %s)' % (' with ' + os.path.basename(a.file) if a.file else '',
                                               r.get('message_id', '?')))
        elif a.action == 'read':
            msgs = discord_history(a.limit, a.thread)
            if not msgs:
                print('discord: nothing to read'); return 0
            for m in reversed(msgs):          # oldest first reads like a conversation
                # Discord stamps UTC. Slicing the raw string shows the wrong
                # hour by the offset -- the same trap that once put a Phoenix
                # evening push on the next day's date.
                when = core._utc_local(m['timestamp'])[11:16] or m['timestamp'][11:16]
                print('  %s  %-16s %s' % (when, m['author'][:16],
                                          (m['content'] or '').replace('\n', ' ')[:110]))
        elif a.action == 'status':
            try:
                discord_history(1)
                print('discord: bot reachable at %s' % BOT_URL)
            except Exception as e:
                print('discord: bot NOT reachable at %s -- %s' % (BOT_URL, e)); return 1
    except urllib.error.URLError as e:
        print('discord: cannot reach the bot at %s -- %s' % (BOT_URL, e))
        print('  is the discord service running?')
        return 1
    return 0
