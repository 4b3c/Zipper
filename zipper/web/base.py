"""Shared vocabulary for the dashboard modules.

The imports every part of the server needs, the PATH repair that has to happen
before anything shells out, the STATE/LOCK pair, and how old each source is.

`HERE` is still the *package* directory (`zipper/`), not this one, because
`sys.path` wants its parent -- the extra dirname is the only thing that changed
when this moved down a level.

Split out of `zipper/serve.py` on 2026-09-07.
"""
import argparse, datetime, glob, html, json, os, shutil, subprocess, sys, tempfile, threading, time
import base64, io, re, urllib.parse

from .. import core, canvas, chat, conversations, events, gh, ics, metrics, usage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(HERE))

# systemd starts a service with a minimal PATH, and ttyd, tmux and gh are not on it.
# Every shell-out then fails *silently*: the terminal card says "ttyd not installed",
# and worse, the fetcher's `gh auth token` finds no gh, falls back to public repos, and
# rewrites note frontmatter from a partial fetch -- last_push moving BACKWARDS as the
# private repos vanish. Restore a real PATH before anything shells out.
# claude-session.sh does the same for `claude`.
for _dir in (os.path.expanduser('~/.local/bin'), '/opt/homebrew/bin', '/usr/local/bin'):
    if os.path.isdir(_dir) and _dir not in os.environ.get('PATH', '').split(os.pathsep):
        os.environ['PATH'] = os.environ.get('PATH', '') + os.pathsep + _dir


STATE = {'generation': 0, 'refreshing': False, 'last_error': '', 'last_refresh': None}
LOCK = threading.Lock()


# ---------------------------------------------------------------- freshness

def _mtime_iso(path):
    try:
        return datetime.datetime.fromtimestamp(os.path.getmtime(path)).isoformat(timespec='seconds')
    except OSError:
        return None

def _blob_fetched(path):
    try:
        blob = json.load(open(path, encoding='utf-8'))
    except Exception:
        return None
    v = blob.get('fetched')
    if v and len(v) == 10:          # calendars store a bare date; fall back to mtime
        return _mtime_iso(path)
    return v or _mtime_iso(path)

def freshness():
    cal = [_blob_fetched(p) for p in glob.glob(os.path.join(core.INBOX, 'calendar-*.json'))]
    cal = [c for c in cal if c]
    vault = max((_mtime_iso(p) for p in core.iter_notes()), default=None)
    return {
        'calendars': min(cal) if cal else None,
        'github': _blob_fetched(core.GH_JSON),
        'canvas': _blob_fetched(canvas.CANVAS_JSON),
        'vault': vault,
    }

def ago(iso):
    if not iso:
        return 'never'
    try:
        t = datetime.datetime.fromisoformat(iso)
    except ValueError:
        return iso
    s = (datetime.datetime.now() - t).total_seconds()
    if s < 0:
        s = 0
    for lim, div, unit in ((90, 1, 's'), (5400, 60, 'm'), (172800, 3600, 'h')):
        if s < lim:
            return '%d%s ago' % (round(s / div), unit)
    return '%dd ago' % round(s / 86400)
