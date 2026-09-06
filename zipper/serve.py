#!/usr/bin/env python3
"""Dashboard server for the vault.

Design notes, 2026-09-01:
  * every page load serves the LAST CACHED state instantly -- never a spinner
  * a refresh of the external sources is kicked off in the background
  * the page shows, per source, how old its data is, so staleness is visible
    rather than silent. Every bug this vault has produced was stale data
    presented as current.

No framework: stdlib only, so the VPS needs nothing but python3.
    python3 -m zipper.serve --port 8800
"""
import argparse, datetime, glob, html, json, os, shutil, subprocess, sys, tempfile, threading, time
import base64, io, re, urllib.parse

from . import core, canvas, chat, conversations, events, gh, ics, metrics
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

# A desktop launcher hands a process PATH=/usr/bin:/bin:/usr/sbin:/sbin, and systemd
# gives it even less. ttyd, tmux and
# gh all live in Homebrew's bin, so under the app every shell-out failed silently:
# the terminal card said "ttyd not installed", and worse, the fetcher's `gh auth token`
# found no gh, fell back to public repos, and rewrote note frontmatter from a partial
# fetch -- last_push moving BACKWARDS as the private repos vanished. Restore a real
# PATH before anything shells out. claude-session.sh does the same for `claude`.
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


# ---------------------------------------------------------------- terminal

TERM = {'proc': None, 'port': 8801, 'url': '', 'enabled': True, 'ready': None,
        'host': '127.0.0.1', 'cred': '',
        # One tmux session per Zipper instance. Overridable so a second
        # instance -- a test, or a staging box -- cannot paste into the
        # conversation the real one is holding.
        'session': os.environ.get('ZIPPER_TMUX_SESSION', 'zipper')}

def _queue_prompt():
    """The queue as an opening instruction — only what is still outstanding."""
    real = [r for r in feed_rows()
            if not r['done'] and not r['text'].startswith('error')]
    if not real:
        return ''
    return ("Zipper just refreshed the vault. This run's queue:\n\n"
            + '\n'.join('  [%s] %s' % (r['key'], r['text']) for r in real)
            + "\n\nRead Meta/Queue.md and Inbox/queue.json, work out which notes and tasks "
              "these changes affect, and update the vault to match. Flag anything that looks "
              "contradictory rather than guessing. Then tell me what you changed.\n\n"
              "Cross each item off as you finish it, using the key in brackets:\n"
              "  python3 -m zipper.serve --mark <key>\n"
              "An open dashboard picks that up within a second, so the card shows what is "
              "actually left rather than what arrived.")

def _wait_port(host, port, timeout=6.0):
    """Block until ttyd is actually accepting.

    Popen returns the instant the process is forked, but ttyd needs a moment to
    bind. Returning before then makes the browser mount the iframe against a
    dead port and show "refused to connect".
    """
    import socket
    end = time.time() + timeout
    while time.time() < end:
        c = socket.socket()
        c.settimeout(0.3)
        try:
            c.connect((host, port))
            return True
        except OSError:
            time.sleep(0.1)
        finally:
            c.close()
    return False


def _port_open(host, port, timeout=0.3):
    """Is something already accepting on this port right now?"""
    import socket
    c = socket.socket()
    c.settimeout(timeout)
    try:
        c.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        c.close()


def session_exists():
    tmux = shutil.which('tmux')
    if not tmux:
        return False
    return subprocess.run([tmux, 'has-session', '-t', TERM['session']],
                          stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0


def terminal_up():
    """Is ttyd actually serving? This, not a page-local flag, is what decides
    whether the card shows start buttons.

    `window.__mounted` only ever lived in one tab, and a launcher opens a fresh
    one every time — so after a reload the page offered to *resume* a
    conversation that was already on screen. The server knows the truth: if ttyd
    is up the terminal is viewable right now, and there is nothing to resume.
    """
    if TERM['proc'] is not None and TERM['proc'].poll() is None:
        return True
    # A ttyd from a previous serve.py can outlive it (the service was restarted
    # under an open dashboard). It is still serving the same tmux session, so it
    # is still the terminal -- adopt it rather than spawning a second one that
    # cannot bind the port and dies silently.
    return _port_open(TERM['host'], TERM['port'])


def new_session():
    """Drop the tmux session so the next attach starts a fresh conversation.

    The ready-file is cleared too: it still holds the last prompt written to it,
    and `new conversation` means blank. Priming a new one is what the queue
    buttons are for.
    """
    tmux = shutil.which('tmux')
    if not tmux:
        return {'ok': False, 'error': 'tmux not installed'}
    subprocess.run([tmux, 'kill-session', '-t', TERM['session']],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if TERM['ready']:
        try:
            open(TERM['ready'], 'w').write('')
        except OSError:
            pass
    publish('diff', 'terminal    conversation closed')
    return {'ok': True}


def paste_to_session(text, label='text'):
    """Type a block into the running pane.

    Everything that reaches a live conversation from outside goes through here:
    the run queue, and now anything arriving over Discord. See inject_queue for
    why this is a bracketed paste and not send-keys.
    """
    tmux = shutil.which('tmux')
    if not tmux:
        return {'ok': False, 'error': 'tmux not installed'}
    if not session_exists():
        return {'ok': False, 'error': 'no conversation to hand %s to' % label}
    try:
        subprocess.run([tmux, 'load-buffer', '-b', 'zipperq', '-'],
                       input=text.encode('utf-8'), check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run([tmux, 'paste-buffer', '-b', 'zipperq', '-t', TERM['session'],
                        '-p', '-d'], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.25)                   # let the TUI settle before submitting
        subprocess.run([tmux, 'send-keys', '-t', TERM['session'], 'Enter'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        return {'ok': False, 'error': str(e)}
    return {'ok': True}


def inject_queue(prompt):
    """Hand this run's queue to a conversation that is already running.

    The ready-file only works at launch — claude-session.sh reads it once, before
    exec'ing claude — so a live session needs the text typed into its pane. It goes
    through tmux's paste buffer with `-p` (bracketed paste) rather than send-keys:
    the prompt is multi-line, and as keystrokes every newline would submit a
    fragment. Bracketed paste arrives as one block, then a separate Enter sends it.
    Load-bearing: `-p` only wraps the text in the bracketed-paste escapes, so it
    depends on the receiving TUI having the mode enabled. Claude Code does — it is
    what makes a multi-line paste one message there. A plain shell does not, and
    would run each line; that is the shape of it if this ever misbehaves.
    """
    res = paste_to_session(prompt, 'the queue')
    if not res['ok']:
        publish('diff', 'terminal    could not hand over the queue: %s' % res['error'])
        return res
    publish('diff', 'terminal    queue handed to the running conversation')
    return res


def start_terminal(mode='blank', prompt=None):
    """Spawn ttyd running Claude Code in the vault. Only ever called from the
    page, never at launch: opening the dashboard must not start a Claude
    session, because merely looking at the day should not cost tokens.

    Modes, and what a live session does to each:
      blank   start a conversation, nothing injected
      queue   start a conversation primed with this run's queue
      resume  reattach to the conversation that is already running
      catchup reattach AND hand it the queue

    `queue` is the only one that means a NEW conversation, so it kills the
    session first: tmux attaches instead of running claude-session.sh, so against
    a live session the prompt file was written and never read, and the button
    silently did nothing at all. `catchup` is the live-session counterpart —
    same intent, but the text is pasted into the running pane.

    Bound to loopback: ttyd -W hands out a live shell, so it must never listen
    on anything reachable from outside this machine.
    """
    if not TERM['enabled']:
        return {'ok': False, 'error': 'terminal disabled'}
    if mode == 'queue' and session_exists():
        new_session()
    resumed = session_exists()
    opening = prompt or ''
    prompt = '' if resumed else (opening or (_queue_prompt() if mode == 'queue' else ''))
    handed = False
    if resumed and mode == 'catchup':
        res = inject_queue(_queue_prompt())
        if not res['ok']:
            return res
        handed = True
    if TERM['proc']:                       # ttyd already up; just set the prompt
        if TERM['ready'] and not resumed:
            open(TERM['ready'], 'w').write(prompt)
        return {'ok': True, 'port': TERM['port'], 'resumed': resumed,
                'primed': bool(prompt) or handed}
    if _port_open(TERM['host'], TERM['port']):
        # A ttyd this process did not spawn is still serving the port (the
        # service was restarted under it). Spawning a second one would fail to
        # bind and die silently, so use the one that is there.
        return {'ok': True, 'port': TERM['port'], 'resumed': resumed,
                'primed': handed, 'adopted': True}
    exe = shutil.which('ttyd')
    if not exe:
        publish('diff', 'terminal    ttyd not installed')
        return {'ok': False, 'error': 'ttyd not installed'}
    fh = tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False)
    TERM['ready'] = fh.name
    fh.write(prompt)
    fh.close()
    inner = [os.path.join(HERE, 'claude-session.sh'), TERM['ready']]
    tmux = shutil.which('tmux')
    if tmux:
        # tmux owns the process, not the websocket. Closing the tab detaches;
        # reopening reattaches to the SAME live conversation. Without this,
        # ttyd spawns a fresh command per connection and the session is lost.
        args = [tmux, 'new', '-A', '-s', TERM['session']] + inner
    else:
        args = inner
        publish('diff', 'terminal    no tmux - the session dies with the tab')
    try:
        # On loopback the terminal is reached only through nginx's /t/<port>/,
        # which puts it on the dashboard's own origin -- and a credential there
        # would prompt for a sign-in the dashboard has already had. Exposed
        # directly on any other host it still must carry one.
        cred = ['-c', TERM['cred']] if (TERM['cred'] and TERM['host'] != '127.0.0.1') else []
        if TERM['host'] != '127.0.0.1' and not TERM['cred']:
            publish('diff', 'terminal    refusing to expose an unauthenticated shell')
            return {'ok': False, 'error': 'refusing to expose an unauthenticated shell'}
        TERM['proc'] = subprocess.Popen(
            [exe, '-p', str(TERM['port']), '-i', TERM['host'], '-W'] + cred + [
             # matches the nginx location, so ttyd's own asset and websocket
             # URLs carry the prefix they are served under
             '-b', '/t/%d' % TERM['port'],
             # Claude Code turns on mouse reporting, so a drag goes to the
             # application and xterm makes no selection of its own. xterm's
             # bypass is Shift everywhere except macOS, where it is Option --
             # and only when this option is on, which it is not by default.
             # Without it there is no way to select text with a mouse at all.
             '-t', 'macOptionClickForcesSelection=true',
             '-t', 'rightClickSelectsWord=true',
             '-t', 'fontSize=13', '-t', 'fontFamily=SFMono-Regular,Menlo,monospace',
             '-t', 'theme={"background":"#171614","foreground":"#ece8e1"}'] + args,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        TERM['url'] = 'http://127.0.0.1:%d/' % TERM['port']
        if not _wait_port(TERM['host'], TERM['port']):
            publish('diff', 'terminal    ttyd did not come up on port %d' % TERM['port'])
            return {'ok': False, 'error': 'terminal did not start'}
        publish('diff', 'terminal    %s session started' % mode)
        # The page may be holding an iframe against the ttyd this one replaced.
        # Without this it shows a dead terminal until someone reloads -- which
        # is what "sending from Discord disconnects the dashboard" looked like.
        publish('terminal', 'session started')
        if handed:
            pass
        elif mode == 'catchup' and session_exists() and _queue_prompt():
            # ttyd had to be spawned first; the pane exists only now.
            handed = inject_queue(_queue_prompt())['ok']
        return {'ok': True, 'port': TERM['port'], 'resumed': resumed,
                'primed': bool(prompt) or handed}
    except Exception as e:
        publish('diff', 'terminal    failed: %s' % e)
        return {'ok': False, 'error': str(e)}

# ---------------------------------------------------------------- inbound
#
# A message that arrives from outside the dashboard -- today that means Discord,
# tomorrow a cron trigger or a webhook. There is exactly one Claude session, and
# these are the three states it can be in:
#
#   live      ttyd is serving and tmux holds a conversation  -> paste into it
#   detached  tmux still holds the conversation, ttyd is not serving
#             (the tab was closed, or the server restarted)  -> bring ttyd back,
#                                                                then paste
#   cold      no tmux session at all                         -> start one, primed
#                                                                with the message
#
# The cold path deliberately does NOT paste. claude-session.sh reads the
# ready-file before exec'ing claude, so the message becomes the conversation's
# opening prompt -- no race against a TUI that has not drawn yet.

def _tagged(text, source):
    """Claude needs to know where this came from, because the reply goes back the
    same way. The instruction is part of the message rather than the system
    prompt so it survives into a conversation that started some other way."""
    return ('[via %s] %s\n\n'
            '(Reply to this by running: %s discord send "your reply".'
            ' The sender is not watching this terminal.)'
            % (source, text, 'python3 -m zipper'))


def deliver_to_claude(text, source='discord'):
    if not TERM['enabled']:
        return {'ok': False, 'error': 'terminal disabled'}
    body = _tagged(text, source)

    if session_exists():
        state = 'live' if terminal_up() else 'detached'
        if state == 'detached':
            r = start_terminal('resume')
            if not r['ok']:
                return r
            time.sleep(1.0)          # let tmux finish attaching before pasting
        res = paste_to_session(body, 'a %s message' % source)
        if res['ok']:
            publish('diff', 'terminal    %s message delivered (%s)' % (source, state))
        return dict(res, state=state)

    r = start_terminal('blank', prompt=body)
    if r['ok']:
        publish('diff', 'terminal    %s message started a new conversation' % source)
    return dict(r, state='cold')



TITLE_TTL = 900          # seconds before a Discord thread name is looked up again


def _row_title(thread_id, row):
    """What to call a conversation in the list.

    Claude's own `ai-title` first: it names the *conversation* rather than its
    delivery mechanism, it updates as the subject moves, and it exists for
    sessions started at the terminal that have no thread at all. Titles used to
    come from whichever path created the row -- the opening message, a generated
    'Dashboard · Sun 14:26', an id -- four schemes in one list.

    A Discord thread name is the fallback, for a conversation too young to have
    been titled yet; an id is the last resort.
    """
    own = conversations.title(thread_id)
    if own:
        _sync_thread_name(thread_id, row, own)
        return own
    if str(thread_id).startswith('local-'):
        return row.get('title') or 'new conversation'
    fetched = row.get('title_at') or 0
    if row.get('title_src') == 'discord' and (time.time() - fetched) < TITLE_TTL:
        return row.get('title')
    try:
        name = (chat._bot('/threadinfo', {'thread_id': thread_id}, timeout=6).get('name') or '').strip()
    except Exception:
        name = ''
    if name:
        conversations.touch(thread_id, title=name, title_src='discord',
                            title_at=int(time.time()))
        return name
    return row.get('title') or 'conversation %s' % str(thread_id)[-6:]


RENAME_EVERY = 600       # Discord rate-limits thread renames; twice per 10 min


def _sync_thread_name(thread_id, row, name):
    """Give the Discord thread the name Claude gave the conversation.

    A conversation started from the dashboard opens its thread before anyone
    knows what it is about, so it is born as "Dashboard · Sun 14:26". Leaving it
    that way means the phone shows a list of timestamps -- the whole point of
    opening the thread up front is being able to find the conversation later
    without having planned to.

    Renaming is rate-limited by Discord and the title moves as the subject does,
    so this only fires when the name actually changed and at most once every ten
    minutes per thread.
    """
    if str(thread_id).startswith('local-') or not name:
        return
    # Only ever rename a thread whose name we wrote. A thread opened from a
    # message in the channel is named by Discord from what he typed, and a
    # thread he renames himself is a deliberate act -- overwriting either with
    # a generated title would be taking something away, and the titles are not
    # always better than the words a person chose.
    if not row.get('auto_named'):
        return
    if row.get('discord_name') == name:
        return
    if time.time() - (row.get('renamed_at') or 0) < RENAME_EVERY:
        return
    try:
        r = chat._bot('/threadrename', {'thread_id': thread_id, 'name': name}, timeout=8)
    except Exception:
        return
    if r.get('ok'):
        conversations.touch(thread_id, discord_name=name, renamed_at=int(time.time()))


PASTE_DIR = os.path.join(tempfile.gettempdir(), 'zipper-pastes')
PASTE_KEEP = 40


def _prune_pastes():
    """Keep the last few pastes and no more.

    They are screenshots dropped into a conversation, not vault content -- they
    live in tmp, and the only reason to keep any is that a conversation may
    refer back to one it was shown a few minutes ago.
    """
    try:
        files = sorted((os.path.getmtime(os.path.join(PASTE_DIR, f)), f)
                       for f in os.listdir(PASTE_DIR))
    except OSError:
        return
    for _, f in files[:-PASTE_KEEP]:
        try:
            os.remove(os.path.join(PASTE_DIR, f))
        except OSError:
            pass


BUFFER_MAX = 200000


def newest_buffer(seen=''):
    """The most recent tmux paste buffer, if it is not the one already seen.

    Buffers are named bufferNN and numbered upwards, so the highest is newest.
    The name and size together are the identity -- a re-copy of the same text
    makes a new buffer, and the operator expects that to reach the clipboard
    again.
    """
    tmux = shutil.which('tmux')
    if not tmux:
        return {'ok': False, 'error': 'tmux not installed'}
    try:
        r = subprocess.run([tmux, 'list-buffers', '-F', '#{buffer_name}\t#{buffer_size}'],
                           capture_output=True, text=True, timeout=5)
    except Exception as e:
        return {'ok': False, 'error': str(e)}
    best, best_n, size = None, -1, 0
    for line in r.stdout.splitlines():
        name, _, sz = line.partition('\t')
        if not name.startswith('buffer'):
            continue          # zipper-copy is ours; it is not a new selection
        try:
            n = int(name[6:])
        except ValueError:
            continue
        if n > best_n:
            best, best_n, size = name, n, int(sz or 0)
    if not best:
        return {'ok': True, 'id': ''}
    ident = '%s:%d' % (best, size)
    if ident == seen or size > BUFFER_MAX:
        return {'ok': True, 'id': ident, 'unchanged': True}
    try:
        text = subprocess.run([tmux, 'show-buffer', '-b', best],
                              capture_output=True, text=True, timeout=5).stdout
    except Exception as e:
        return {'ok': False, 'error': str(e)}
    return {'ok': True, 'id': ident, 'text': text}


def conversation_rows():
    """The chat list: every conversation, with the state the page has to show."""
    conversations.sweep()
    rows = []
    for r in conversations.listing():
        rows.append({
            'thread_id': r['thread_id'],
            'title': _row_title(r['thread_id'], r),
            'alive': r['alive'],
            'bound': bool(r.get('bound')),
            'serving': r.get('serving'),
            'port': r.get('port'),
            'state': r.get('state'),
        })
    return rows


def open_conversation(thread_id):
    """Show a conversation in the dashboard: revive it if closed, then serve it.

    A conversation closed by the reaper is resumed rather than replaced -- the
    transcript is the conversation, and picking one out of the list must never
    mean starting a stranger with the same name.
    """
    if not thread_id:
        return {'ok': False, 'error': 'thread_id required'}
    if not conversations.alive(thread_id):
        r = conversations.start(thread_id)
        if not r.get('ok'):
            return r
    row = conversations.load().get(str(thread_id)) or {}
    if row.get('tmux') == TERM['session']:
        # The bound conversation is the dashboard's own terminal. It has its own
        # ttyd on --term-port; bring that one back if it is down rather than
        # giving this row a second one. Two ttyds on one tmux session both work,
        # but they share a cursor and argue about the size of the window.
        if terminal_up():
            return {'ok': True, 'port': TERM['port'], 'adopted': True}
        r = start_terminal('resume')
        return dict(r, port=r.get('port', TERM['port'])) if r.get('ok') else r
    res = conversations.ensure_ttyd(thread_id, host='127.0.0.1')
    if res.get('ok'):
        publish('diff', 'terminal    showing conversation %s' % thread_id)
    return res



def new_conversation():
    """Start another conversation, closing none.

    It gets a Discord thread of its own straight away, so a conversation begun
    at the keyboard can be picked up from a phone without being adopted after
    the fact -- which is the awkward path that binding exists to patch.
    """
    title = 'Dashboard \u00b7 %s' % datetime.datetime.now().strftime('%a %H:%M')
    try:
        r = chat._bot('/thread', {'name': title,
                                  'message': 'New conversation started from the dashboard.'})
        tid = str(r.get('thread_id') or '')
    except Exception as e:
        # No Discord, no thread -- but the conversation should still start. A
        # local id keeps it addressable in the list; it just cannot be reached
        # from a phone until someone opens a thread for it.
        publish('diff', 'terminal    no Discord thread for this conversation: %s' % e)
        tid = 'local-%d' % int(time.time())
    if not tid:
        return {'ok': False, 'error': 'could not open a Discord thread'}
    conversations.touch(tid, title=title, auto_named=True, discord_name=title)
    r = conversations.start(tid)
    if not r.get('ok'):
        return r
    res = conversations.ensure_ttyd(tid, host='127.0.0.1')
    return dict(res, thread_id=tid, title=title)


def stop_terminal():
    if TERM['proc']:
        try:
            TERM['proc'].terminate()
        except Exception:
            pass
        TERM['proc'] = None

# ---------------------------------------------------------------- refresh

SUBS = []            # live SSE subscriber queues
SUBS_LOCK = threading.Lock()
FEED = []            # the run queue, in arrival order
FEED_LOCK = threading.RLock()

# The queue lives in a file, not just in memory, for two reasons: it survives a
# restart of this server (it used to vanish, so a queue you were halfway through
# was gone), and the Claude session in the terminal can cross items off while the
# dashboard is open. FEED_MTIME remembers our own last write so the watcher can
# tell somebody else's edit from an echo of our own.
FEED_JSON = os.path.join(core.INBOX, 'feed.json')
FEED_MAX = 200
FEED_MTIME = [0.0]

def _feed_key(text):
    import hashlib
    return hashlib.sha1(text.encode('utf-8')).hexdigest()[:10]

def _feed_transient(text):
    """Status chatter, not work: shown once, never persisted, never crossed off."""
    return text.startswith('no changes') or text.startswith('terminal ')

def feed_rows():
    with FEED_LOCK:
        return [dict(r) for r in FEED]

def feed_load():
    global FEED
    try:
        blob = json.load(open(FEED_JSON, encoding='utf-8'))
        rows = [r for r in blob.get('rows', []) if r.get('key') and r.get('text')]
    except Exception:
        rows = []
    with FEED_LOCK:
        FEED = rows
    return rows

def feed_save():
    with FEED_LOCK:
        rows = [r for r in FEED if r.get('key')][-FEED_MAX:]
        tmp = FEED_JSON + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump({'rows': rows}, fh, indent=1)
        os.replace(tmp, FEED_JSON)          # atomic: the watcher never sees a half-file
        try:
            FEED_MTIME[0] = os.path.getmtime(FEED_JSON)
        except OSError:
            pass

def feed_mark(key, done=None):
    """Cross a queue item off (or back on). Accepts a unique key prefix so it is
    typeable from the terminal."""
    with FEED_LOCK:
        hits = [r for r in FEED if r['key'] == key] or \
               [r for r in FEED if r['key'].startswith(key)]
        if not hits:
            return {'ok': False, 'error': 'no queue item matching %r' % key}
        if len(hits) > 1:
            return {'ok': False, 'error': '%r matches %d items' % (key, len(hits))}
        r = hits[0]
        want = (not r.get('done')) if done is None else done
        r['done'] = datetime.datetime.now().isoformat(timespec='seconds') if want else None
        feed_save()
        return {'ok': True, 'key': r['key'], 'done': bool(r['done']), 'text': r['text']}

def feed_mark_all():
    """Cross off every row still open, in one write. --mark takes a single key, so
    clearing a whole run used to be a shell loop over --queue -- ten separate saves,
    ten watcher pushes. This is one save and one push. It only ever crosses off:
    nothing here restores a row, so it cannot undo a deliberate un-tick."""
    with FEED_LOCK:
        now = datetime.datetime.now().isoformat(timespec='seconds')
        hits = [r for r in FEED if not r.get('done')]
        for r in hits:
            r['done'] = now
        if hits:
            feed_save()
        return {'ok': True, 'marked': len(hits)}


def publish(kind, text='', **extra):
    ev = {'kind': kind, 'text': text, 'at': datetime.datetime.now().strftime('%H:%M:%S')}
    ev.update(extra)
    if kind == 'terminal':
        ev['port'] = TERM['port']
    if kind == 'diff' and not _feed_transient(text):
        with FEED_LOCK:
            key = _feed_key(text)
            if any(r['key'] == key for r in FEED):
                return                      # same fact twice is not two queue items
            row = {'key': key, 'at': ev['at'], 'text': text, 'done': None}
            FEED.append(row)
            feed_save()
        ev['key'] = key
        ev['done'] = None
    with SUBS_LOCK:
        for q in list(SUBS):
            q.append(ev)

def feed_watch(interval=1.0):
    """Push the queue when the file moves under us — that is how a cross-off from
    the terminal reaches an open dashboard."""
    last = None
    while True:
        time.sleep(interval)
        try:
            m = os.path.getmtime(FEED_JSON)
        except OSError:
            continue
        if last is None or m == last:
            last = m
            continue
        last = m
        if abs(m - FEED_MTIME[0]) < 1e-6:
            continue                        # our own write, already broadcast
        feed_load()
        publish('feed', rows=feed_rows())

def snapshot_data():
    """What the diff is measured against. Keys only — cheap to compare."""
    cal = set()
    for f in glob.glob(os.path.join(core.INBOX, 'calendar-*.json')):
        try:
            for e in json.load(open(f, encoding='utf-8'))['events']:
                # Carry the uid: it is the same across every occurrence of a
                # recurring series, and emit_diff groups on it. Without it a new
                # weekly class reports one row per expanded date.
                cal.add((e['start'], e['summary'], e.get('uid') or ''))
        except Exception:
            pass
    canvas = {}
    try:
        for r in json.load(open(canvas.CANVAS_JSON, encoding='utf-8'))['items']:
            canvas[r['title']] = r['submitted']
    except Exception:
        pass
    repos = {}
    try:
        for r in json.load(open(core.GH_JSON, encoding='utf-8'))['repos']:
            repos[r['name']] = r.get('pushed_at', '')
    except Exception:
        pass
    return {'cal': cal, 'canvas': canvas, 'repos': repos, 'flags': set(flags())}

CADENCE = {1: 'daily', 7: 'weekly', 14: 'fortnightly', 28: '4-weekly'}


def _shape(starts):
    """Describe a run of occurrence dates: '(weekly x58, through 2027-10-06)'.

    Only names a cadence when every gap is the same; a series with holidays cut
    out of it says 'repeats' rather than inventing a rhythm it does not have.
    """
    if len(starts) < 2:
        return ''
    days = sorted({(datetime.date.fromisoformat(b[:10])
                    - datetime.date.fromisoformat(a[:10])).days
                   for a, b in zip(starts, starts[1:])})
    word = CADENCE.get(days[0]) if len(days) == 1 else None
    return '  (%s \u00d7%d, through %s)' % (word or 'repeats', len(starts), starts[-1][:10])


def _cal_rows(keys, sign):
    """One row per series, not per occurrence.

    parse_ics expands RRULE into concrete dates, so before this the day CSE 434's
    lab appeared the feed published 58 identical-looking rows -- one calendar
    entry drowning out the two real changes in the same run. Every occurrence of
    a series shares an ICS uid, so group on it and state the shape of the run.
    Events with no uid fall back to their own start+summary and stay separate.
    """
    groups = {}
    for start, summary, uid in keys:
        groups.setdefault(uid or '%s|%s' % (start, summary), (summary, []))[1].append(start)
    rows = []
    for summary, starts in groups.values():
        starts.sort()
        rows.append((starts[0], summary,
                     '%s calendar  %s  %s%s' % (sign, starts[0], summary, _shape(starts))))
    return [r for _, _, r in sorted(rows)]


def emit_diff(before, after):
    n = 0
    for row in _cal_rows(after['cal'] - before['cal'], '+'):
        publish('diff', row); n += 1
    for row in _cal_rows(before['cal'] - after['cal'], '-'):
        publish('diff', row); n += 1
    for title, done in sorted(after['canvas'].items()):
        was = before['canvas'].get(title)
        if was is None:
            publish('diff', '+ canvas    %s%s' % (title, '  (already submitted)' if done else '')); n += 1
        elif done and not was:
            publish('diff', 'submitted   %s' % title); n += 1
    for name, ts in sorted(after['repos'].items()):
        if before['repos'].get(name, '') != ts and before['repos'].get(name) is not None:
            publish('diff', 'pushed      %s  %s' % (name, ts[:16])); n += 1
    for fl in sorted(after['flags'] - before['flags']):
        publish('diff', 'flag        %s' % fl); n += 1
    return n

def do_refresh():
    """Runs ONCE per launch, never on a page reload. Each source publishes as it
    lands, so the page fills in live instead of waiting for the slowest one."""
    with LOCK:
        if STATE['refreshing']:
            return
        STATE['refreshing'] = True
    publish('status', 'fetching\u2026')
    before = snapshot_data()
    err, total = [], 0
    try:
        core.TODAY = datetime.date.today()
        steps = [('calendars', ics.cmd_calendars, {}),
                 ('github', gh.cmd_github, {'since_days': 30, 'full': False})]
        if os.environ.get('CANVAS_TOKEN'):
            steps.append(('canvas', canvas.cmd_canvas, {'file': None, 'days': 60}))
        # Run the sources concurrently: a Canvas ICS feed alone can take ~6s to
        # generate, and GitHub has no reason to queue behind it.
        dlock = threading.Lock()

        def run(label, fn, kw):
            nonlocal before, total
            try:
                fn(argparse.Namespace(**kw))
            except Exception as e:
                err.append('%s: %s' % (label, e))
                publish('diff', 'error       %s: %s' % (label, e))
            with dlock:                 # snapshot/diff is shared state
                after = snapshot_data()
                total += emit_diff(before, after)
                before = after
            publish('source', label)

        threads = [threading.Thread(target=run, args=st, daemon=True) for st in steps]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        with LOCK:
            STATE['refreshing'] = False
            STATE['generation'] += 1
            STATE['last_error'] = '; '.join(err)
            STATE['last_refresh'] = datetime.datetime.now().isoformat(timespec='seconds')
        if not total:
            publish('diff', 'no changes  everything was already current')
        publish('done', '')

# ---------------------------------------------------------------- data

def _d(iso):
    try:
        return datetime.date(*map(int, iso[:10].split('-')))
    except Exception:
        return None

def upcoming(days=10):
    horizon = (core.TODAY + datetime.timedelta(days=days)).isoformat()
    cstat = canvas.canvas_status_map()
    rows = []
    for f in sorted(glob.glob(os.path.join(core.INBOX, 'calendar-*.json'))):
        blob = json.load(open(f, encoding='utf-8'))
        for e in blob['events']:
            d = e['start'][:10]
            if core.TODAY.isoformat() <= d <= horizon:
                done = cstat.get((d, core._norm_title(e['summary']))) if blob['label'] == 'canvas' else None
                rows.append({'date': d, 'time': e['start'][11:], 'label': blob['label'],
                             'summary': e['summary'], 'loc': e.get('location', ''),
                             'done': done, 'start': e['start'], 'end': e.get('end', ''),
                             'uid': e.get('uid', '')})
    rows.sort(key=lambda r: (r['date'], r['time'] or '00:00'))
    return rows


def day_events(day):
    """Every ingested event on one date. `upcoming()` starts at today, so it
    cannot look backwards; the day arrows need to."""
    cstat = canvas.canvas_status_map()
    rows = []
    for f in sorted(glob.glob(os.path.join(core.INBOX, 'calendar-*.json'))):
        blob = json.load(open(f, encoding='utf-8'))
        for e in blob['events']:
            if e['start'][:10] != day:
                continue
            done = (cstat.get((day, core._norm_title(e['summary'])))
                    if blob['label'] == 'canvas' else None)
            rows.append({'date': day, 'time': e['start'][11:], 'label': blob['label'],
                         'summary': e['summary'], 'loc': e.get('location', ''),
                         'done': done, 'start': e['start'], 'end': e.get('end', ''),
                         'uid': e.get('uid', ''), 'url': e.get('url', '')})
    rows.sort(key=lambda r: (r['time'] or '00:00', r['summary']))
    return rows


def today_split(day=None):
    """One day, split the way it reads: things with a clock on the right,
    things merely due on the left. All-day items strike through when submitted;
    timed ones strike through once the clock has passed — but only on today,
    since 'already happened' is meaningless on a day he is looking ahead to."""
    day = day or core.TODAY.isoformat()
    is_today = day == core.TODAY.isoformat()
    now = datetime.datetime.now().strftime('%H:%M')
    allday, timed = [], []
    for e in day_events(day):
        if e['time']:
            e['past'] = is_today and e['time'] < now
            timed.append(e)
        else:
            allday.append(e)
    return allday, timed


def canvas_items():
    if not os.path.exists(canvas.CANVAS_JSON):
        return []
    return json.load(open(canvas.CANVAS_JSON, encoding='utf-8')).get('items', [])


def canvas_outstanding():
    return [r for r in canvas_items()
            if not r['submitted'] and r['due'][:10] >= core.TODAY.isoformat()]


def task_text(raw):
    """Exactly the engine's normalisation, so the dashboard, ledger and queue all
    key a task the same way. A naive character class stops inside [[Note]] and
    leaves a trailing ']]' on every task that names a project."""
    t = re.sub(r'\[[a-z_]+::\s*(?:\[\[[^\]]+\]\]|[^\]]*)\]', '', raw)
    return re.sub(r'#\w+', '', t).strip()


def open_tasks():
    out = []
    for p in sorted(glob.glob(os.path.join(core.VAULT, 'Tasks', '*.md'))):
        for line in open(p, encoding='utf-8'):
            m = core.TASK_RE.match(line)
            if not m or m.group(1).lower() == 'x':
                continue
            raw = m.group(2)
            due = re.search(r'\[due::\s*(\d{4}-\d{2}-\d{2})\]', raw)
            proj = re.search(r'\[project::\s*\[\[([^\]]+)\]\]', raw)
            out.append({'text': task_text(raw),
                        'due': due.group(1) if due else '',
                        'project': proj.group(1) if proj else '',
                        'next': '#next' in raw,
                        'overdue': bool(due and due.group(1) < core.TODAY.isoformat())})
    return out


def priority(it):
    """Deliberately simple and explainable — urgency, then a little weight.

    Not a metric anyone is scored on, just a sort order. Kept legible so a
    surprising position can be argued with rather than trusted.
    """
    d = _d(it['due']) if it['due'] else None
    days = (d - core.TODAY).days if d else None
    base = ({None: 5}.get(days) if days is None else
            100 if days < 0 else 70 if days == 0 else 55 if days == 1 else
            40 if days <= 3 else 25 if days <= 7 else 10)
    bonus = 12 if it.get('next') else 0
    pts = it.get('points') or 0
    bonus += 8 if pts >= 100 else 4 if pts >= 50 else 0
    if it['source'] == 'canvas':
        bonus += 3                      # somebody else set this deadline
    return base + bonus


def ranked(limit=10):
    """Canvas work and self-reported tasks in one list, most pressing first."""
    items = []
    for r in canvas_outstanding():
        items.append({'source': 'canvas', 'title': r['title'], 'due': r['due'][:10],
                      'tag': r['course'], 'url': r['url'], 'points': r.get('points'),
                      'next': False, 'elsewhere': r.get('elsewhere', '')})
    for t in open_tasks():
        items.append({'source': 'task', 'title': t['text'], 'due': t['due'],
                      'tag': t['project'], 'url': '', 'points': 0, 'next': t['next']})
    ov = _ov_load()
    for it in items:
        it['score'] = priority(it)
        it['overdue'] = bool(it['due'] and it['due'] < core.TODAY.isoformat())
        it['key'] = override_key(it)
        it['done'] = it['key'] in ov
    # Crossed-off work sinks, whatever it scores. The partition this replaces was undone
    # by the sort on the very next line, so a struck-through row kept its place at the top
    # and spent a slot in the top ten on something already handled — which reads from the
    # browser as the cross-off not having worked.
    items.sort(key=lambda i: (i['done'], -i['score'], i['due'] or '9999', i['title']))
    return items[:limit], items


OVERRIDES = os.path.join(core.INBOX, 'overrides.json')

def _ov_load():
    try:
        return json.load(open(OVERRIDES, encoding='utf-8'))
    except Exception:
        return {}

def _ov_save(d):
    with open(OVERRIDES, 'w', encoding='utf-8') as fh:
        json.dump(d, fh, indent=1, sort_keys=True)

def override_key(it):
    return ('canvas:%s|%s' if it['source'] == 'canvas' else 'task:%s|%s') % (it['tag'], it['title'])

def toggle_done(key):
    """Cross something off by hand.

    A task is ticked in its own markdown file, so the vault stays the source of
    truth and the ledger sees the close. Canvas cannot be written to, so those
    live in overrides.json and are purely a display override.
    """
    if key.startswith('task:'):
        title = key.split('|', 1)[1]
        for p in sorted(glob.glob(os.path.join(core.VAULT, 'Tasks', '*.md'))):
            lines = open(p, encoding='utf-8').read().split('\n')
            hit = False
            for i, line in enumerate(lines):
                m = core.TASK_RE.match(line)
                if not m:
                    continue
                if task_text(m.group(2)) != title:
                    continue
                done = m.group(1).lower() == 'x'
                lines[i] = line.replace('[x]' if done else '[ ]',
                                        '[ ]' if done else '[x]', 1)
                hit = True
                break
            if hit:
                open(p, 'w', encoding='utf-8').write('\n'.join(lines))
                return {'ok': True, 'where': os.path.basename(p), 'done': not done}
        return {'ok': False, 'error': 'task not found'}
    d = _ov_load()
    if key in d:
        d.pop(key)
        state = False
    else:
        d[key] = datetime.datetime.now().isoformat(timespec='seconds')
        state = True
    _ov_save(d)
    return {'ok': True, 'where': 'overrides.json', 'done': state}

def flags():
    try:
        return json.load(open(os.path.join(core.INBOX, 'queue.json'), encoding='utf-8')).get('flags', [])
    except Exception:
        return []


def content_sig():
    """Hash of what the page shows, excluding fetch timestamps."""
    import hashlib
    h = hashlib.sha1()
    for e in upcoming():
        h.update(('%s|%s|%s|%s' % (e['date'], e['time'], e['summary'], e['done'])).encode())
    for r in canvas_outstanding():
        h.update(('%s|%s' % (r['due'], r['title'])).encode())
    for t in open_tasks():
        h.update(('%s|%s' % (t['text'], t['due'])).encode())
    for x in flags():
        h.update(x.encode())
    return h.hexdigest()[:12]


# ---------------------------------------------------------------- render

CSS = """
:root{--bg:#fbfaf8;--fg:#1c1a17;--dim:#6d675e;--line:#e2ded6;--card:#fff;--accent:#8c1d40;--warn:#b3541e;--ok:#3f6f4a}
@media(prefers-color-scheme:dark){:root{--bg:#171614;--fg:#ece8e1;--dim:#989186;--line:#2f2c28;--card:#1e1d1a;--accent:#e0708f;--warn:#e0965c;--ok:#7fb98b}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:22px 20px 60px}
h1{font-size:22px;margin:0 0 2px}
h1 .btn{margin-top:4px}
.sub{color:var(--dim);font-size:13px}
.fresh{display:flex;flex-wrap:wrap;gap:6px;margin:12px 0 20px}
.chip{border:1px solid var(--line);border-radius:99px;padding:3px 10px;font-size:12px;color:var(--dim);background:var(--card)}
.chip b{color:var(--fg);font-weight:600}
.chip.stale{border-color:var(--warn);color:var(--warn)}
.chip a{color:var(--accent);text-decoration:none;margin-left:6px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-bottom:18px}
.card h2{font-size:12px;text-transform:uppercase;letter-spacing:.07em;color:var(--dim);margin:0 0 10px;font-weight:600}
.today{display:grid;grid-template-columns:1fr 1fr;gap:0}
.today>div{padding:0 16px}
.today>div:first-child{border-right:1px solid var(--line)}
@media(max-width:800px){.today{grid-template-columns:1fr}.today>div:first-child{border-right:0;border-bottom:1px solid var(--line);padding-bottom:12px;margin-bottom:12px}}
ul{list-style:none;margin:0;padding:0}
li{padding:4px 0;font-size:14px;display:flex;gap:8px;align-items:baseline}
li.row{align-items:flex-start;gap:10px;padding:7px 0;border-bottom:1px solid var(--line)}
li.row:last-child{border-bottom:0}
.rowbody{display:flex;flex-direction:column;gap:2px;min-width:0}
.rowtitle{line-height:1.35}
.rowmeta{font-size:11px;color:var(--dim);font-variant-numeric:tabular-nums}
li.crossed .rowtitle,li.crossed .rowtitle a{text-decoration:line-through;color:var(--dim)}
.t{color:var(--dim);font-variant-numeric:tabular-nums;font-size:12px;min-width:46px}
/* the schedule grid: one column of the day, height = duration */
.grid{position:relative;margin:2px 0 4px}
.hr{position:absolute;left:0;right:0;border-top:1px solid var(--line)}
.hr span{position:absolute;top:-7px;left:0;font-size:11px;color:var(--dim);font-variant-numeric:tabular-nums;background:var(--card);padding-right:6px}
.nowline{position:absolute;left:44px;right:0;border-top:1px solid var(--accent);z-index:3}
.nowline:before{content:'';position:absolute;left:-4px;top:-3px;width:6px;height:6px;border-radius:50%;background:var(--accent)}
.blk{position:absolute;top:var(--top);height:var(--h);left:calc(var(--l) + 52px);width:calc(var(--w) - 56px);display:flex;flex-direction:column;overflow:hidden;background:var(--bg);border:1px solid var(--line);border-left:3px solid var(--dim);border-radius:5px;padding:3px 7px;z-index:2;cursor:pointer}
.blk:hover{border-color:var(--dim)}
.blk.open{height:auto;min-height:var(--h);overflow:visible;z-index:9;border-color:var(--accent);box-shadow:0 8px 28px rgba(0,0,0,.28)}
.blk.open .why{flex:none;overflow:visible;-webkit-mask-image:none;mask-image:none}
.bx{display:none;flex:none;margin-top:6px;padding-top:6px;border-top:1px solid var(--line)}
.blk.open .bx{display:block}
.acts{display:flex;flex-wrap:wrap;gap:5px;align-items:center}
.act{font:inherit;font-size:11px;border:1px solid var(--line);border-radius:5px;padding:2px 7px;color:var(--accent);text-decoration:none;background:none;cursor:pointer;white-space:nowrap}
.act:hover{border-color:var(--accent);background:var(--card)}
.act[disabled]{opacity:.5;cursor:default}
.actdim{font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.04em;margin-left:auto}
.blk.noted{border-left-color:var(--accent)}
.blk.past{opacity:.5}
.blk.past .bt{text-decoration:line-through}
.bt{font-size:13px;line-height:1.2;font-weight:500;flex:none}
.bm{font-size:11px;line-height:1.3;color:var(--dim);font-variant-numeric:tabular-nums;flex:none}
.why{font-size:11px;color:var(--dim);line-height:1.3;margin-top:2px;flex:1 1 auto;min-height:0;overflow:hidden;-webkit-mask-image:linear-gradient(to bottom,#000 calc(100% - 9px),transparent);mask-image:linear-gradient(to bottom,#000 calc(100% - 9px),transparent)}
.why p{margin:0 0 4px}
.why p:last-child{margin-bottom:0}
.bt{overflow:hidden;text-overflow:ellipsis}
.pin{font-size:10px;color:var(--accent);white-space:nowrap}
.card h2.hdr{display:flex;align-items:center;gap:8px}
.vtable{width:100%;border-collapse:collapse;font-size:13px}
.vtable th{text-align:left;font-weight:600;color:var(--dim);font-size:11px;letter-spacing:.04em;text-transform:uppercase;padding:0 10px 6px 0;border-bottom:1px solid var(--line)}
.vtable td{padding:7px 10px 7px 0;border-bottom:1px solid var(--line);vertical-align:top}
.vtable tr:last-child td{border-bottom:0}
.vwrap{overflow-x:auto}
.vlink{color:var(--accent);text-decoration:none}
.vlink:hover{text-decoration:underline}
.vnone{color:var(--dim)}
.vnote{margin:0 0 10px}
.vmore{margin:8px 0 0}
.vnavbar{margin-bottom:16px}
.vnav{color:var(--dim);text-decoration:none;margin-right:10px}
.vnav.on{color:var(--fg)}
.vnav:hover{color:var(--fg)}
.daynav{margin-left:auto;display:flex;gap:4px;align-items:center}
.daynav #daytoday{margin-right:10px}
.daynav button{background:none;border:1px solid var(--line);border-radius:5px;color:var(--dim);cursor:pointer;font:inherit;font-size:13px;line-height:1;padding:3px 8px;text-transform:none;letter-spacing:0}
.daynav button:hover{color:var(--fg);border-color:var(--dim)}
.done,.past{text-decoration:line-through;color:var(--dim)}
.tag{font-size:11px;color:var(--dim);border:1px solid var(--line);border-radius:4px;padding:0 5px;white-space:nowrap}
.od{color:var(--warn);font-weight:600}
.pri{font-variant-numeric:tabular-nums;color:var(--accent)}
.elsewhere{opacity:.75;font-style:italic}
.src{font-size:10px;text-transform:uppercase;letter-spacing:.04em;border-radius:3px;padding:0 4px;border:1px solid var(--line);color:var(--dim)}
.src.canvas{border-color:var(--accent);color:var(--accent)}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:18px}
@media(max-width:800px){.cols{grid-template-columns:1fr}}
.flag{color:var(--warn);font-size:13px;padding:3px 0}
.big{font-size:28px;font-weight:600;font-variant-numeric:tabular-nums}
.metrics{display:flex;gap:22px;flex-wrap:wrap}
.metric small{display:block;color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.05em}
footer{margin-top:24px;border-top:1px solid var(--line);padding-top:14px}
footer .fresh{margin:0}
code{background:var(--line);padding:1px 5px;border-radius:4px;font-size:12px}
.qrow{font:12px/1.7 ui-monospace,SFMono-Regular,Menlo,monospace;border-bottom:1px solid var(--line);
      padding:2px 0;display:flex;gap:8px;align-items:flex-start}
.qrow:last-child{border-bottom:0}
.qx{white-space:pre-wrap;flex:1;min-width:0}
.qt{color:var(--dim)}
.qrow.crossed .qx,.qrow.crossed .qt{text-decoration:line-through;color:var(--dim)}
.qrow.crossed .tick{border-color:var(--accent)}
.tick.ghost{border-color:transparent;cursor:default}
.qfold{display:block;width:100%;text-align:left;background:none;border:0;cursor:pointer;
  font:12px/1.9 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--dim);padding:4px 0 0}
.qfold:hover{color:var(--accent)}
.btn{float:right;margin-left:10px;font:inherit;font-size:11px;text-transform:uppercase;letter-spacing:.05em;
  background:none;border:1px solid var(--line);color:var(--dim);border-radius:5px;padding:1px 8px;cursor:pointer;text-decoration:none}
.btn:hover{border-color:var(--accent);color:var(--accent)}
#termwrap iframe{width:100%;height:600px;border:0;border-radius:8px;background:#171614;display:block}
#termbody{display:flex;gap:10px;align-items:stretch}
#toast{position:fixed;left:50%;bottom:26px;transform:translateX(-50%) translateY(8px);
       background:#171614;color:#ece8e1;padding:7px 13px;border-radius:7px;font-size:12px;
       opacity:0;pointer-events:none;transition:opacity .15s,transform .15s;z-index:200}
#toast.on{opacity:.94;transform:translateX(-50%) translateY(0)}
.termdead{height:600px;display:flex;flex-direction:column;align-items:center;justify-content:center;
          gap:10px;background:#171614;border-radius:8px;color:#ece8e1}
#termcard.full .termdead{height:100%}
#termwrap{flex:1;min-width:0}
#chatlist{width:186px;flex:0 0 186px;display:flex;flex-direction:column;gap:4px;overflow-y:auto;max-height:600px}
#termcard.full #termbody{flex:1;min-height:0}
#termcard.full #chatlist{max-height:none}
.chat{text-align:left;background:none;border:1px solid transparent;border-radius:7px;padding:6px 8px;
      cursor:pointer;color:inherit;font:inherit;line-height:1.25;display:block;width:100%}
.chat:hover{background:rgba(127,127,127,.10)}
.chat.on{border-color:var(--accent);background:rgba(127,127,127,.07)}
.chat{display:flex;align-items:center;gap:7px}
.chat .ct{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.chat .dot{flex:0 0 7px;width:7px;height:7px;border-radius:50%;background:#8a8a8a}
.chat.waiting .dot{background:#4caf72}
.chat.working .dot{background:#e0a52b;animation:chatpulse 1.4s ease-in-out infinite}
.chat.closed .dot{background:#8a8a8a}
.chat.closed .ct{opacity:.55}
@keyframes chatpulse{0%,100%{opacity:1}50%{opacity:.35}}
#termcard.full{position:fixed;inset:0;z-index:99;margin:0;border-radius:0;display:flex;flex-direction:column}
#termcard.full #termwrap{flex:1}
#termcard.full #termwrap iframe{height:100%}
.tick{flex:none;width:17px;height:17px;margin-top:1px;border:1.5px solid var(--line);border-radius:4px;
  background:none;color:var(--accent);cursor:pointer;font-size:11px;line-height:1;padding:0;
  display:flex;align-items:center;justify-content:center}
.tick:hover{border-color:var(--accent)}
li.crossed .tick{border-color:var(--accent)}
/* `el.hidden` sets an attribute, and the UA rule behind it is only [hidden]{display:none}
   -- which ANY author rule that sets display outranks. #termstart{display:flex} is an id
   selector, so hiding the start box set the attribute and changed nothing on screen: the
   buttons stayed up next to the running conversation through two rounds of "fixes" to the
   logic, which was correct the whole time. Make the attribute win everywhere. */
[hidden]{display:none!important}
#termstart{display:flex;gap:14px;justify-content:center;flex-wrap:wrap;padding:26px 0 20px}
.startbtn{font:inherit;font-size:15px;background:none;border:1px solid var(--line);color:var(--fg);
  border-radius:9px;padding:13px 26px;cursor:pointer;transition:border-color .15s,color .15s}
.startbtn:hover:not(:disabled){border-color:var(--accent);color:var(--accent)}
.startbtn:disabled{opacity:.35;cursor:not-allowed}
.more{float:right;font-size:11px;color:var(--accent);text-decoration:none;text-transform:none;letter-spacing:0}
.more:hover{text-decoration:underline}
footer .fresh{margin:0 0 10px}
a.plain{color:inherit;text-decoration:none;border-bottom:1px solid var(--line)}
a.plain:hover{border-bottom-color:var(--accent)}
"""

JS = """
function fmt(t){
  if(t==null) return 'never';
  let s=Math.max(0,Math.floor(Date.now()/1000-t));
  if(s<60) return s+'s ago';
  if(s<3600) return Math.round(s/60)+'m ago';
  if(s<172800) return Math.round(s/3600)+'h ago';
  return Math.round(s/86400)+'d ago';
}
function drawFresh(){
  const el=document.getElementById('fresh'); if(!el) return;
  el.innerHTML=['vault','calendars','github','canvas'].map(k=>{
    const x = k==='canvas' ? ' <a href="'+window.__canvashost+'" target="_blank" rel="noopener">open Canvas</a>' : '';
    return '<span class="chip"><b>'+k+'</b> '+fmt(window.__epochs[k])+x+'</span>';
  }).join('');
}
setInterval(drawFresh,1000);
function qrowHTML(r){
  const t=(r.text||'').replace(/&/g,'&amp;').replace(/</g,'&lt;');
  const tick = r.key
    ? '<button class="tick qtick" data-qkey="'+r.key+'" aria-label="cross off">'+(r.done?'\u2713':'')+'</button>'
    : '<span class="tick ghost"></span>';
  return '<div class="qrow'+(r.done?' crossed':'')+'">'+tick
        +'<span class="qt">'+r.at+'</span><span class="qx">'+t+'</span></div>';
}
// Dealt-with rows leave the list. The card answers "what is left", and a run
// of 65 items where 60 are struck through answers it badly — the crossing-off
// is the point, so the reward for doing it should be a shorter list. They are
// folded rather than deleted: an accidental tick has to be undoable, and the
// only place to un-tick is the row itself.
function drawQueue(rows){
  window.__feed=rows||[];
  const q=document.getElementById('queue'); if(!q) return;
  const open=window.__feed.filter(r=>!r.done), done=window.__feed.filter(r=>r.done);
  document.getElementById('qcount').textContent=open.length;
  if(!window.__feed.length){
    q.dataset.empty='1';
    q.innerHTML='<p class="sub">Waiting for this run\u2019s fetch\u2026</p>';
    return;
  }
  delete q.dataset.empty;
  let h = open.map(qrowHTML).join('');
  if(!open.length) h = '<p class="sub">All clear \u2014 everything this run turned up is dealt with.</p>';
  if(done.length){
    h += '<button class="qfold" id="qfold">'+(window.__showdone?'\u25be':'\u25b8')+' '
       + done.length+' crossed off</button>';
    if(window.__showdone) h += done.map(qrowHTML).join('');
  }
  q.innerHTML=h;
}
function row(e){
  const f=window.__feed||[];
  if(e.key && f.some(r=>r.key===e.key)) return;
  f.push({key:e.key||'',at:e.at,text:e.text,done:e.done||null});
  drawQueue(f);
}
// Three states, and the card shows exactly one set of choices for each:
//   mounted  the conversation is on screen — no start buttons at all, just the
//            header's `new conversation`, which is the only thing left to want
//   live     a conversation is running but this page isn't showing it (the app
//            was closed and reopened) — resume, or resume and hand it the queue.
//            Both land in the SAME conversation; one arrives with an instruction
//   cold     nothing running — start one, blank or primed with the queue
// `new conversation` lives in the header and is visible whenever there is a
// conversation to replace, so starting over never depends on finding the box.
function drawTerm(){
  const box=document.getElementById('termstart'); if(!box) return;
  // `on` means the terminal is viewable right now — either this page mounted it,
  // or ttyd is already serving one (a reload, or a freshly opened tab). Either
  // way there is nothing to resume, so no start buttons.
  const on=window.__mounted||window.__termup, live=window.__session, ready=window.__queueready;
  const qd = ready ? '' : ' disabled title="nothing in this run&#39;s queue to consume"';
  box.hidden = !!on;
  if(!on) box.innerHTML = live
    ? '<button class="startbtn" data-mode="resume">resume conversation</button>'
     +'<button class="startbtn" data-mode="catchup"'+qd+'>resume and clear queue</button>'
    : '<button class="startbtn" data-mode="blank">start blank session</button>'
     +'<button class="startbtn" data-mode="queue"'+qd+'>start session to clear queue</button>';
  document.getElementById('termnew').hidden = !(live||on);
  ['termfull','termpop'].forEach(i=>{document.getElementById(i).hidden=!on;});
  const st=document.getElementById('termstate');
  if(st && !on) st.textContent = live ? 'running — not attached here' : 'not started';
  if(st && on && !st.dataset.said) st.textContent='';
}
function shiftDay(iso,n){const d=new Date(iso+'T12:00:00');d.setDate(d.getDate()+n);
  return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');}
function drawDay(){
  const t=window.__today, d=window.__day;
  const lbl=document.getElementById('daylabel'); if(!lbl) return;
  const off=Math.round((new Date(d+'T12:00:00')-new Date(t+'T12:00:00'))/86400000);
  lbl.textContent = off===0 ? 'Today' : off===1 ? 'Tomorrow' : off===-1 ? 'Yesterday'
    : new Date(d+'T12:00:00').toLocaleDateString(undefined,{weekday:'short',day:'2-digit',month:'short'});
  // The masthead names the day being read, not the day it is: walking the arrows
  // and leaving today's date at the top of the page reads as a stuck page.
  const pd=document.getElementById('pagedate'), D=new Date(d+'T12:00:00');
  if(pd) pd.textContent = D.toLocaleDateString('en-US',{weekday:'long'})+' '
    +String(D.getDate()).padStart(2,'0')+' '+D.toLocaleDateString('en-US',{month:'long'})
    +' '+D.getFullYear();   // matches the server's '%A %d %B %Y' so the first paint doesn't shift
  const b=document.getElementById('daytoday'); if(b) b.hidden = off===0;
}
async function goDay(n){
  window.__day = n===0 ? window.__today : shiftDay(window.__day,n);
  drawDay(); await panels();
}
async function panels(){
  const p=await fetch('/api/panels?day='+encodeURIComponent(window.__day||''))
    .then(r=>r.json()).catch(()=>null); if(!p) return;
  for(const k in p.html){const el=document.getElementById(k); if(el) el.innerHTML=p.html[k];}
  window.__epochs=p.epochs; drawFresh();
  window.__session=p.session||window.__mounted; window.__queueready=p.queue_ready;
  window.__termup=p.termup; window.__termport=p.termport; drawTerm();
}
const es=new EventSource('/events');
es.addEventListener('diff',e=>row(JSON.parse(e.data)));
es.addEventListener('feed',e=>drawQueue(JSON.parse(e.data).rows));
document.addEventListener('click',async ev=>{
  const mk=ev.target.closest('.mknote');
  if(mk){
    ev.stopPropagation(); mk.disabled=true; mk.textContent='creating\u2026';
    const r=await fetch('/api/eventnote',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({summary:mk.dataset.summary,date:mk.dataset.date})})
      .then(r=>r.json()).catch(()=>({error:'failed'}));
    if(r.error){mk.disabled=false;mk.textContent=r.error;return;}
    await panels();
    return;
  }
  if(ev.target.closest('.blk a')) return;            // let the actions navigate
  const blk=ev.target.closest('.blk');
  document.querySelectorAll('.blk.open').forEach(b=>{if(b!==blk)b.classList.remove('open');});
  if(blk){ev.stopPropagation(); blk.classList.toggle('open');}
});
document.addEventListener('keydown',ev=>{
  if(ev.key==='Escape') document.querySelectorAll('.blk.open').forEach(b=>b.classList.remove('open'));
});
document.addEventListener('DOMContentLoaded',()=>{
  const pv=document.getElementById('dayprev'), nx=document.getElementById('daynext'),
        td=document.getElementById('daytoday');
  if(pv) pv.onclick=()=>goDay(-1);
  if(nx) nx.onclick=()=>goDay(1);
  if(td) td.onclick=()=>goDay(0);
  document.addEventListener('keydown',ev=>{
    if(ev.metaKey||ev.ctrlKey||ev.altKey) return;
    const tag=(ev.target.tagName||'').toLowerCase();
    if(tag==='input'||tag==='textarea'||ev.target.isContentEditable) return;
    if(ev.key==='ArrowLeft') goDay(-1); else if(ev.key==='ArrowRight') goDay(1);
  });
  drawDay();
});
es.addEventListener('terminal',e=>{
  // ttyd was (re)started under us -- point the iframe at the live one.
  const p=JSON.parse(e.data).port; if(!p) return;
  window.__termup=true; window.__termport=p; window.__mounted=true;
  mountTerm(p); drawTerm();
});
es.addEventListener('source',()=>panels());
es.addEventListener('status',e=>{document.getElementById('status').textContent=JSON.parse(e.data).text;});
es.addEventListener('done',()=>{panels();document.getElementById('status').textContent='';});
// Same origin as the dashboard, proxied by nginx to the ttyd on that port.
// Pointing the iframe at host:port directly made every conversation its own
// origin, so the terminal asked to sign in again each time one was opened.
function termURL(port){return location.origin+'/t/'+port+'/';}
// ---- the chat list: one Claude per Discord thread, switched by clicking.
// Each conversation has its own ttyd on its own port, so switching is just
// pointing the iframe somewhere else -- nothing is torn down, and the
// conversation you were reading keeps running while you read another.
window.__chat=null;
// Thread titles are whatever was typed into Discord, so they are escaped here
// rather than trusted. `esc` on the server is Python's; this is the page's own.
function chatEsc(t){return String(t==null?'':t).replace(/[&<>"]/g,
  c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function drawChats(rows){
  const el=document.getElementById('chatlist');
  if(!el) return;
  if(!rows||!rows.length){el.hidden=true;el.innerHTML='';return;}
  el.hidden=false;
  el.innerHTML=rows.map(r=>{
    const on=(String(r.thread_id)===String(window.__chat))?' on':'';
    const st=r.state||(r.alive?'waiting':'closed');
    // yellow working, green waiting, grey closed. The dot carries the state on
    // its own -- the row is a name and a light, and a line of small print under
    // every one of them made the list harder to read, not easier.
    return '<button class="chat '+st+on+'" data-tid="'+r.thread_id+'" title="'+
           chatEsc(r.title)+' \u2014 '+st+'"><span class="dot"></span>'+
           '<span class="ct">'+chatEsc(r.title)+'</span></button>';
  }).join('');
}
function loadChats(){
  return fetch('/api/conversations').then(r=>r.json())
    .then(d=>{window.__chats=d.conversations||[];drawChats(window.__chats);
              checkShown(window.__chats);return window.__chats;})
    .catch(()=>[]);
}
// A conversation can die without the page doing anything -- Ctrl-C in the pane
// ends Claude and takes the tmux session with it. The iframe then shows a
// terminal that is either frozen or, worse, a fresh shell wearing the old
// conversation's name. Swap it for a button that resumes the real one.
function checkShown(rows){
  if(!window.__chat) return;
  const row=(rows||[]).find(r=>String(r.thread_id)===String(window.__chat));
  const wrap=document.getElementById('termwrap');
  if(!wrap||!row) return;
  if(row.state==='closed'){
    if(!wrap.dataset.closed) showClosed(row);
  } else if(wrap.dataset.closed && row.serving){
    // It came back by some other route (a Discord message, say). Only then is
    // remounting free -- never resume one just because the page is looking.
    wrap.dataset.closed='';
    mountTerm(row.port);
  }
}
function openChat(tid){
  const el=document.getElementById('chatlist');
  if(el) el.querySelectorAll('.chat').forEach(b=>b.disabled=true);
  return fetch('/api/conversation',{method:'POST',headers:{'Content-Type':'application/json'},
                                    body:JSON.stringify({thread_id:tid})})
    .then(r=>r.json()).then(d=>{
      if(d.ok&&d.port){window.__chat=tid;window.__mounted=true;mountTerm(d.port);drawTerm();}
      return loadChats();
    }).catch(()=>loadChats());
}
document.addEventListener('click',ev=>{
  const b=ev.target.closest?ev.target.closest('.chat'):null;
  if(!b||!b.dataset.tid) return;
  const tid=b.dataset.tid;
  const row=(window.__chats||[]).find(r=>String(r.thread_id)===String(tid));
  // Selecting a closed conversation costs nothing; *resuming* one re-reads the
  // whole transcript at full price, because its prompt cache has expired. That
  // is a decision, not a side effect of clicking a name to see what it was.
  if(row&&row.state==='closed'){ window.__chat=tid; drawChats(window.__chats); showClosed(row); return; }
  openChat(tid);
});
function showClosed(row){
  const wrap=document.getElementById('termwrap');
  if(!wrap) return;
  wrap.dataset.closed='1';
  wrap.innerHTML='<div class="termdead"><p><b>'+chatEsc(row.title)+'</b> is closed.</p>'+
    '<button class="btn" id="termreload">reload conversation</button>'+
    '<p class="sub">Resuming re-reads the whole conversation \u2014 its prompt cache has '+
    'expired, so this one costs full price.</p></div>';
  const b=document.getElementById('termreload');
  if(b) b.onclick=()=>{b.disabled=true;b.textContent='resuming\u2026';
                       wrap.dataset.closed='';openChat(row.thread_id);};
}
function mountTerm(port){
  const u=termURL(port);
  document.getElementById('termwrap').innerHTML='<iframe src="'+u+'" allow="clipboard-read; clipboard-write"></iframe>';
  document.getElementById('termpop').href=u;
  const f=document.querySelector('#termwrap iframe');
  if(f){
    // Both, because either can be missed: a cached iframe can finish loading
    // before the listener is attached, and hookTerm is idempotent by design.
    f.addEventListener('load',()=>hookTerm(f));
    setTimeout(()=>hookTerm(f),1500);
  }
}
// Selecting in the terminal should put the text on the real machine's clipboard,
// and an image on the clipboard should reach the conversation. Both are only
// possible because ttyd is served from this origin now: the iframe is
// same-origin, so its window -- and the xterm instance ttyd leaves on it as
// `term` -- can be reached from here.
function clipReport(o){try{fetch('/api/clipdebug',{method:'POST',
  headers:{'Content-Type':'application/json'},body:JSON.stringify(o)});}catch(e){}}
function findTerm(win){
  // ttyd leaves the xterm instance on `window.term`, but do not depend on the
  // name: anything exposing getSelection + onSelectionChange is the terminal.
  if(win.term&&win.term.getSelection) return win.term;
  try{
    for(const k of Object.keys(win)){
      const v=win[k];
      if(v&&typeof v==='object'&&typeof v.getSelection==='function'
         &&typeof v.onSelectionChange==='function') return v;
    }
  }catch(e){}
  return null;
}
function hookTerm(f){
  let win;
  try{ win=f.contentWindow; }catch(e){ clipReport({stage:'cross-origin'}); return; }
  let tries=0;
  (function wait(){
    if(!win||!win.document) return;
    const term=findTerm(win);
    if(!term){ if(tries++<40){ setTimeout(wait,250); return; }
               clipReport({stage:'no-term-after-10s'}); return; }
    if(win.__zipperHooked) return;
    win.__zipperHooked=true;
    watchBuffer(win);

    // copy: xterm keeps its own selection (the canvas renderer means the page
    // has none), so read it from the terminal and write it from inside the
    // frame, where the click that just happened counts as the user gesture.
    const report=o=>{try{fetch('/api/clipdebug',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify(o)});}catch(e){}};
    // Must run *inside* the event, not in a setTimeout after it: execCommand
    // and the clipboard API both require an active user gesture, and a
    // continuation scheduled off the event no longer counts as one. That was
    // the bug -- the copy ran, silently did nothing, and left the old clipboard.
    const copySel=(why)=>{
      // Either source: xterm keeps its own selection with the canvas renderer,
      // but with the DOM renderer the selection is the document's and xterm may
      // report nothing. Whichever has text is the one the user made.
      let sel=''; try{ sel=term.getSelection()||''; }catch(e){}
      if(!sel){ try{ sel=String(win.getSelection()||'').trim(); }catch(e){} }
      if(!sel||sel===win.__lastSel) return;
      win.__lastSel=sel;
      const secure=!!win.isSecureContext, api=!!(win.navigator.clipboard&&win.navigator.clipboard.writeText);
      let how='none', ok=false;
      // execCommand first when the page is not a secure context: there the
      // async clipboard API does not exist at all, and asking for it first only
      // wastes the gesture.
      if(secure&&api){
        how='api';
        try{ win.navigator.clipboard.writeText(sel).then(()=>report({how:'api',ok:true,n:sel.length,why:why}),
                                                        e=>{const r=legacyCopy(win,sel);
                                                            report({how:'api->exec',ok:r,n:sel.length,err:String(e),why:why});});
             ok=true; }
        catch(e){ how='exec'; ok=legacyCopy(win,sel); }
      } else {
        how='exec'; ok=legacyCopy(win,sel);
      }
      if(how!=='api') report({how:how,ok:ok,n:sel.length,secure:secure,api:api,why:why,
                              proto:win.location.protocol});
      sendSel(win,sel,why);
    };
    win.document.addEventListener('mouseup',()=>copySel('mouseup'),true);

    // Belt and braces: xterm's own event. It fires without a DOM event when
    // selection is extended by keyboard or by a drag that ends outside the
    // frame, and it is the only signal if something swallows mouseup.
    try{ term.onSelectionChange(()=>{ if(!win.__selPending){ win.__selPending=1;
           setTimeout(()=>{ win.__selPending=0;
             let sel=''; try{ sel=term.getSelection(); }catch(e){}
             if(sel&&sel!==win.__lastSel){ win.__lastSel=sel; sendSel(win,sel,'selchange'); }
           },120); } }); }catch(e){}
    // Ctrl/Cmd+C on keydown, before xterm forwards it to the pty: on keyup the
    // gesture is spent and, worse, a bare Ctrl-C has already interrupted Claude.
    win.document.addEventListener('keydown',ev=>{
      if(!(ev.ctrlKey||ev.metaKey)||(ev.key!=='c'&&ev.key!=='C')) return;
      let sel=''; try{ sel=term.getSelection(); }catch(e){}
      if(!sel) return;                        // no selection: let Ctrl-C interrupt
      win.__lastSel=null; copySel('key');
      ev.preventDefault(); ev.stopPropagation();
    },true);

    // paste: text is ttyd's own business and already works. An image is not
    // text, so it is uploaded and what lands in the prompt is its path -- which
    // is what Claude Code opens.
    win.document.addEventListener('paste',ev=>{
      const items=(ev.clipboardData&&ev.clipboardData.items)||[];
      for(const it of items){
        if(it.kind!=='file'||it.type.indexOf('image/')!==0) continue;
        const blob=it.getAsFile(); if(!blob) return;
        ev.preventDefault(); ev.stopPropagation();
        fetch('/api/pasteimage',{method:'POST',headers:{'Content-Type':blob.type},body:blob})
          .then(r=>r.json())
          .then(d=>{ if(d.path){ try{ term.paste(d.path+' '); }catch(e){} }
                     else if(d.error){ alert('image paste failed: '+d.error); } })
          .catch(e=>alert('image paste failed: '+e));
        return;
      }
    },true);
  })();
}
// Claude Code selects with the mouse itself -- xterm never sees a selection --
// and copies what was highlighted into a tmux buffer on the box. So the text to
// put on the clipboard comes from there, not from the terminal widget. Poll for
// a new buffer and write it from inside the iframe, which is the focused
// document; a write from the parent is refused for exactly that reason.
function watchBuffer(win){
  if(win.__bufWatch) return;
  win.__bufWatch=1;
  let seen='', pending=null, reported=false;
  const flush=()=>{
    if(!pending) return;
    const text=pending;
    let p=null;
    try{ p=win.navigator.clipboard.writeText(text); }catch(e){ p=null; }
    if(p&&p.then){
      p.then(()=>{ pending=null; toast('copied '+text.length+' chars');
                   if(!reported){reported=true;clipReport({stage:'buffer-copy',ok:true,n:text.length});} },
             e=>{ if(legacyCopy(win,text)){ pending=null; toast('copied '+text.length+' chars'); }
                  if(!reported){reported=true;clipReport({stage:'buffer-copy',ok:false,err:String(e)});} });
    } else if(legacyCopy(win,text)){
      pending=null; toast('copied '+text.length+' chars');
    }
  };
  // Any interaction is a user gesture, which is what a refused write needs.
  ['mouseup','keydown','mousedown'].forEach(ev=>win.document.addEventListener(ev,flush,true));
  setInterval(()=>{
    fetch('/api/tmuxbuffer?seen='+encodeURIComponent(seen)).then(r=>r.json()).then(d=>{
      if(!d||!d.ok||d.unchanged||!d.text) return;
      seen=d.id; pending=d.text; flush();
    }).catch(()=>{});
  },800);
}

// The half that does not need a user gesture: tmux's buffer, and saying so.
function sendSel(win,sel,why){
  fetch('/api/copybuffer',{method:'POST',headers:{'Content-Type':'application/json'},
                           body:JSON.stringify({text:sel,thread_id:window.__chat||null,why:why})})
    .catch(()=>{});
  toast('copied '+sel.length+' chars');
}
let __toastT=null;
function toast(msg){
  let el=document.getElementById('toast');
  if(!el){ el=document.createElement('div'); el.id='toast'; document.body.appendChild(el); }
  el.textContent=msg; el.classList.add('on');
  clearTimeout(__toastT); __toastT=setTimeout(()=>el.classList.remove('on'),1600);
}
function legacyCopy(win,text){
  // returns true only if the browser says the copy actually happened
  // clipboard.writeText needs permission and a focused document, and refuses in
  // some browsers inside an iframe. This path asks for neither.
  try{
    const ta=win.document.createElement('textarea');
    ta.value=text; ta.style.position='fixed'; ta.style.opacity='0';
    win.document.body.appendChild(ta);
    ta.focus(); ta.select(); ta.setSelectionRange(0, text.length);
    const ok=win.document.execCommand('copy');
    ta.remove();
    return !!ok;
  }catch(e){ return false; }
}

document.addEventListener('DOMContentLoaded',()=>{
  drawFresh(); drawTerm(); drawQueue(window.__feed);
  // 6s, not 20: the dot is the only thing saying whether Claude is working,
  // and a light that lags twenty seconds behind is worse than none. Each poll
  // is a capture-pane per conversation, which is cheap.
  loadChats(); setInterval(loadChats, 6000);
  // ttyd already serving: attach straight to it. Before this the page offered to
  // resume a conversation it could simply have shown.
  if(window.__termup){ window.__mounted=true; mountTerm(window.__termport); drawTerm(); }
  const rb=document.getElementById('dorefresh');
  if(rb) rb.onclick=async()=>{
    document.getElementById('status').textContent='refreshing…';
    await fetch('/api/refresh',{method:'POST'});
  };
  const box=document.getElementById('termstart');
  if(box) box.addEventListener('click',async ev=>{
    const btn=ev.target.closest('.startbtn'); if(!btn||btn.disabled) return;
    const mode=btn.dataset.mode;
    // Only `queue` discards a live conversation; resume and catchup join it.
    if(window.__session && mode==='queue'
       && !confirm('Start a new Claude conversation? The current one is closed.')) return;
    btn.disabled=true;
    document.getElementById('termstate').textContent='starting…';
    const r=await fetch('/api/session',{method:'POST',headers:{'Content-Type':'application/json'},
                                        body:JSON.stringify({mode:mode})}).then(r=>r.json());
    btn.disabled=false;
    if(!r.ok){document.getElementById('termstate').textContent=r.error||'failed';return;}
    window.__session=true; window.__mounted=true; window.__termport=r.port; drawTerm();
    const st=document.getElementById('termstate');
    st.dataset.said='1';
    st.textContent = r.resumed ? (r.primed ? 'resumed, queue handed over' : 'resumed')
                               : (r.primed ? 'new conversation, queue injected' : 'new conversation');
    mountTerm(r.port);
  });
  const nb=document.getElementById('termnew');
  if(nb) nb.onclick=async()=>{
    // Adds a conversation; closes nothing. Several run at once now, so starting
    // one is no longer a decision about the one you were in -- it opens its own
    // Discord thread and joins the list beside the others.
    nb.disabled=true;
    const old=nb.textContent; nb.textContent='starting\u2026';
    try{
      const r=await fetch('/api/newconversation',{method:'POST'}).then(x=>x.json());
      if(r.ok&&r.port){
        window.__chat=r.thread_id; window.__session=true; window.__mounted=true;
        const st=document.getElementById('termstate'); st.dataset.said='1';
        st.textContent='new conversation';
        mountTerm(r.port); drawTerm();
      } else if(r.error){ alert('could not start a conversation: '+r.error); }
      await loadChats();
    } finally { nb.disabled=false; nb.textContent=old; }
  };
  const c=document.getElementById('termcard'), b=document.getElementById('termfull');
  if(b){b.onclick=()=>{c.classList.toggle('full'); b.textContent=c.classList.contains('full')?'exit':'fullscreen';};}
  document.addEventListener('keydown',ev=>{if(ev.key==='Escape'&&c&&c.classList.contains('full')){c.classList.remove('full');b.textContent='fullscreen';}});
});
"""


def esc(s):
    return html.escape(s or '')


def _lanes(timed):
    """Blocks with start/end in minutes and a lane each, so overlaps sit side
    by side instead of stacking into one ambiguous column."""
    blocks = []
    for e in timed:
        st = core._dt(e['start'])
        en = core._dt(e.get('end') or '')
        if st is None:
            continue
        s0 = st.hour * 60 + st.minute
        if en is None or en <= st:
            e0 = s0 + 30                      # no DTEND in the feed
        elif en.date() != st.date():
            e0 = 24 * 60                      # runs past midnight
        else:
            e0 = en.hour * 60 + en.minute
        blocks.append({'s': s0, 'e': max(e0, s0 + 20), 'ev': e})
    blocks.sort(key=lambda b: (b['s'], b['e']))
    free = []
    for b in blocks:
        for i, until in enumerate(free):
            if b['s'] >= until:
                free[i] = b['e']; b['lane'] = i; break
        else:
            b['lane'] = len(free); free.append(b['e'])
    return blocks, max(1, len(free))


GCAL_IDS = {}

def _gcal_ids():
    """label -> Google calendar id, read out of the remembered feed URLs.

    A Google iCal URL is .../ical/<calendar id>/private-<token>/basic.ics, so the
    id is already on disk; Canvas URLs have no such segment and get skipped.
    """
    if GCAL_IDS:
        return GCAL_IDS
    path = os.path.join(core.INBOX, 'calendars.json')
    if os.path.exists(path):
        for label, cfg in json.load(open(path, encoding='utf-8')).items():
            m = re.search(r'/ical/([^/]+)/', cfg.get('url', '') or '')
            if m and 'google.com' in cfg.get('url', ''):
                GCAL_IDS[label] = urllib.parse.unquote(m.group(1))
    return GCAL_IDS


def _gcal_link(e, recurring):
    """The Google Calendar page for one event.

    Google's `eid` is base64url(event_id + ' ' + calendar_id). The ICS UID gives
    the *series* id, so a recurring occurrence needs the instance suffix
    `_<UTC stamp>` rebuilt from its local start. Verified against three real
    htmlLinks from the Calendar API, one of them a recurring instance.
    """
    cal = _gcal_ids().get(e['label'])
    uid = e.get('uid') or ''
    if not cal or '@google.com' not in uid:
        return ''
    eid_src = uid.split('@')[0]
    if recurring:
        st = core._dt(e['start'])
        if st is None:
            return ''
        utc = st.astimezone(datetime.timezone.utc)
        eid_src += '_' + utc.strftime('%Y%m%dT%H%M%SZ')
    eid = base64.urlsafe_b64encode(('%s %s' % (eid_src, cal)).encode()).decode().rstrip('=')
    return 'https://www.google.com/calendar/event?eid=' + eid


def _join_link(e):
    """A meeting URL hiding in the location field — Zoom, Meet, Teams."""
    for cand in (e.get('loc') or '', e.get('url') or ''):
        m = re.search(r'https?://\S+', cand)
        if m and re.search(r'zoom|meet\.google|teams\.microsoft|webex', m.group(0)):
            return m.group(0)
    return ''


PX_PER_MIN = 1.0

def _schedule_html(timed, is_today=True):
    """Today as a real calendar column: height is duration, not a bullet."""
    blocks, nlanes = _lanes(timed)
    if not blocks:
        return '<div><h2>Schedule</h2><p class="sub">Nothing scheduled.</p></div>'
    notes = events.event_note_map()
    seen = {}
    for f in sorted(glob.glob(os.path.join(core.INBOX, 'calendar-*.json'))):
        for ev in json.load(open(f, encoding='utf-8')).get('events', []):
            u = ev.get('uid') or ''
            seen[u] = seen.get(u, 0) + 1
    now = datetime.datetime.now()
    nowm = (now.hour * 60 + now.minute) if is_today else -10**6
    lo = min(b['s'] for b in blocks) // 60 * 60
    hi = -(-max(b['e'] for b in blocks) // 60) * 60
    if lo - 90 <= nowm <= hi + 90:            # frame the now-line in when it is near
        lo, hi = min(lo, nowm // 60 * 60), max(hi, -(-nowm // 60) * 60)
    height = (hi - lo) * PX_PER_MIN

    out = ['<div><h2>Schedule</h2><div class="grid" style="height:%dpx">' % height]
    for m in range(lo, hi + 1, 60):
        out.append('<div class="hr" style="top:%.1fpx"><span>%02d:00</span></div>'
                   % ((m - lo) * PX_PER_MIN, (m // 60) % 24))
    if lo <= nowm <= hi:
        out.append('<div class="nowline" style="top:%.1fpx"></div>'
                   % ((nowm - lo) * PX_PER_MIN))
    for b in blocks:
        e = b['ev']
        rec = notes.get((e.get('uid', ''), core._fmt_dt(e['start'])))
        h = (b['e'] - b['s']) * PX_PER_MIN - 2
        cls = 'blk' + (' past' if b['e'] <= nowm else '') + (' noted' if rec else '')
        style = ('--top:%.1fpx;--h:%.1fpx;--l:%.4f%%;--w:%.4f%%'
                 % ((b['s'] - lo) * PX_PER_MIN, h,
                    b['lane'] * 100.0 / nlanes, 100.0 / nlanes))
        span = '%02d:%02d\u2013%02d:%02d' % (b['s'] // 60, b['s'] % 60,
                                             b['e'] // 60, b['e'] % 60)
        meta = [span, core._dur(b['e'] - b['s'])]
        if e['loc'] and 'http' not in e['loc']:
            meta.append(e['loc'][:30])
        pin = ''
        if rec:
            pin = (' <span class="pin">debrief</span>' if rec['state'] == 'due'
                   else ' <span class="pin">note</span>')

        # The description belongs in the slot, clamped to the lines the block can
        # actually hold; a footer list meant reading the grid twice to answer one
        # question. Opening the block lifts the clamp -- the height becomes a
        # floor rather than a cap.
        why = ''
        if rec and rec.get('why'):
            why = '<div class="why">%s</div>' % ''.join(
                '<p>%s</p>' % esc(x) for x in (rec.get('why_all') or [rec['why']]))

        acts = []
        if rec:
            acts.append('<a class="act" href="obsidian://open?vault=space&amp;file=%s">%s</a>'
                        % (urllib.parse.quote('Events/' + rec['title']),
                           'write the debrief' if rec['state'] == 'due' else 'open note'))
        else:
            acts.append('<button class="act mknote" data-summary="%s" data-date="%s">'
                        '+ event note</button>' % (esc(e['summary']), e['date']))
        j = _join_link(e)
        if j:
            acts.append('<a class="act" href="%s" target="_blank" rel="noopener">join</a>'
                        % esc(j))
        g = _gcal_link(e, seen.get(e.get('uid') or '', 0) > 1)
        if g:
            acts.append('<a class="act" href="%s" target="_blank" rel="noopener">calendar</a>'
                        % g)
        if e['label'] == 'canvas' and e.get('url'):
            acts.append('<a class="act" href="%s" target="_blank" rel="noopener">Canvas</a>'
                        % esc(e['url']))
        acts.append('<span class="actdim">%s</span>' % esc(e['label']))

        out.append('<div class="%s" style="%s"><div class="bt">%s%s</div>'
                   '<div class="bm">%s</div>%s'
                   '<div class="bx"><div class="acts">%s</div></div></div>'
                   % (cls, style, esc(e['summary']), pin, esc(' \u00b7 '.join(meta)),
                      why, ''.join(acts)))
    out.append('</div></div>')
    return ''.join(out)


def _today_html(day=None):
    day = day or core.TODAY.isoformat()
    is_today = day == core.TODAY.isoformat()
    allday, timed = today_split(day)
    a = ['<div><h2>%s</h2><ul>' % ('Due today' if is_today else 'Due')]
    for e in allday:
        a.append('<li><span%s>%s</span> <span class="tag">%s</span></li>'
                 % (' class="done"' if e['done'] else '', esc(e['summary']), e['label']))
    a.append('</ul>%s</div>' % ('' if allday else '<p class="sub">Nothing due.</p>'))
    return ''.join(a) + _schedule_html(timed, is_today)


def _startbtns(live, ready):
    """First paint of the start box — the server-side twin of drawTerm().

    A page load is never the `mounted` state: the iframe is only ever attached by
    a click, so the choices here are the live pair or the cold pair.
    """
    qd = '' if ready else ' disabled title="nothing in this run&#39;s queue to consume"'
    if live:
        return ('<button class="startbtn" data-mode="resume">resume conversation</button>'
                '<button class="startbtn" data-mode="catchup"%s>resume and clear queue</button>' % qd)
    return ('<button class="startbtn" data-mode="blank">start blank session</button>'
            '<button class="startbtn" data-mode="queue"%s>start session to clear queue</button>' % qd)


def _qrow_html(r):
    tick = ('<button class="tick qtick" data-qkey="%s" aria-label="cross off">%s</button>'
            % (esc(r['key']), '&#10003;' if r['done'] else ''))
    return ('<div class="qrow%s">%s<span class="qt">%s</span><span class="qx">%s</span></div>'
            % (' crossed' if r['done'] else '', tick, esc(r['at']), esc(r['text'])))


def _item_li(it, show_score=True):
    """One task row. Title leads; everything else drops to a dim second line.

    Priority leads that second line. It was a tooltip for a while, on the theory
    that a number beside the title competed with it — but the `#next` tag that
    was supposed to carry urgency is on half the open tasks, so it stopped
    discriminating. The score is the only thing that actually orders the list,
    so it says so out loud."""
    title = esc(it['title'])
    if it['url']:
        title = '<a class="plain" href="%s" target="_blank" rel="noopener">%s</a>' % (esc(it['url']), title)
    meta = []
    if show_score:
        meta.append('<span class="pri">%d</span>' % it['score'])
    if it['due']:
        meta.append('<span class="%s">%s</span>' % ('od' if it['overdue'] else '', it['due'][5:]))
    if it['tag']:
        meta.append(esc(it['tag']))
    if it.get('elsewhere'):
        # Canvas keeps only a grade column for these, so its "not submitted" is
        # silence, not a fact. Say which platform actually holds the work rather
        # than nagging about something already handed in.
        meta.append('<span class="elsewhere">on %s &mdash; Canvas can\'t tell</span>'
                    % esc(it['elsewhere']))
    return ('<li class="row %s" title="priority %d">'
            '<button class="tick" data-key="%s" aria-label="cross off">%s</button>'
            '<span class="rowbody"><span class="rowtitle">%s</span>'
            '<span class="rowmeta">%s</span></span></li>'
            % ('crossed' if it.get('done') else '', it['score'], esc(it['key']),
               '&#10003;' if it.get('done') else '', title, ' &middot; '.join(meta)))


def _side(items, empty):
    if not items:
        return '<p class="sub">%s</p>' % empty
    return '<ul>' + ''.join(_item_li(i) for i in items) + '</ul>'


def panels_html(day=None):
    _, allitems = ranked()
    cv = [i for i in allitems if i['source'] == 'canvas']
    tk = [i for i in allitems if i['source'] == 'task']
    return {'p-today': _today_html(day),
            'p-work-canvas': _side(cv[:8], 'Nothing outstanding in Canvas.'),
            'p-work-tasks': _side(tk[:8], 'No open tasks.')}


# ---------------------------------------------------------------- views
#
# `zipper views` computed these; this only draws them. One renderer for every
# view, because every view is the same shape -- which is the whole reason the
# Dataview queries were replaced with a JSON file rather than twenty panels.

VIEWS_JSON = os.path.join(core.INBOX, 'views.json')

def views_blob():
    try:
        return json.load(open(VIEWS_JSON, encoding='utf-8'))
    except Exception:
        return {'views': {}, 'pages': [], 'generated': ''}

def _cell(c):
    """A cell is a scalar, or {'link': 'Note'} when it should open in Obsidian."""
    if isinstance(c, dict) and 'link' in c:
        return ('<a class="vlink" href="obsidian://open?vault=%s&amp;file=%s">%s</a>'
                % (urllib.parse.quote(os.path.basename(core.VAULT)),
                   urllib.parse.quote(c['link']), esc(c['link'])))
    if c is None or c == '':
        return '<span class="vnone">—</span>'
    return esc(str(c))

def _view_html(v, compact=False):
    if not v:
        return ''
    head = ''.join('<th>%s</th>' % esc(c) for c in v['columns'])
    rows = v['rows'][:6] if compact else v['rows']
    body = ''.join('<tr>%s</tr>' % ''.join('<td>%s</td>' % _cell(c) for c in r)
                   for r in rows)
    more = ''
    if compact and len(v['rows']) > 6:
        more = '<p class="sub vmore">+%d more</p>' % (len(v['rows']) - 6)
    if not v['rows']:
        return ('<div class="vwrap"><p class="sub">%s</p></div>' % esc(v['empty']))
    note = ('<p class="sub vnote">%s</p>' % esc(v['note'])) if v.get('note') and not compact else ''
    return ('<div class="vwrap">%s<table class="vtable"><thead><tr>%s</tr></thead>'
            '<tbody>%s</tbody></table>%s</div>' % (note, head, body, more))

def _views_page(page_key):
    blob = views_blob()
    pages = blob.get('pages', [])
    page = next((p for p in pages if p['key'] == page_key), None)
    if not page:
        return None
    nav = ' '.join('<a class="vnav%s" href="/views/%s">%s</a>'
                   % (' on' if p['key'] == page_key else '', p['key'], esc(p['title']))
                   for p in pages)
    cards = []
    for key in page['views']:
        v = blob['views'].get(key)
        if not v:
            continue
        cards.append('<div class="card"><h2>%s <span class="sub">&middot; %d</span></h2>%s</div>'
                     % (esc(v['title']), v['count'], _view_html(v)))
    stamp = blob.get('generated', '')[:16].replace('T', ' ')
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>%s</title>
<style>%s</style></head><body><div class="wrap">
<h1>%s</h1><p class="sub">%s</p>
<p class="sub vnavbar"><a class="plain" href="/">&larr; today</a> &middot; %s</p>
%s
<footer><div class="fresh">computed %s &middot; <code>zipper views</code></div></footer>
</div></body></html>""" % (esc(page['title']), CSS, esc(page['title']),
                           esc(page['note']), nav, ''.join(cards), esc(stamp))


def _list_page(kind):
    _, allitems = ranked()
    items = [i for i in allitems if i['source'] == kind]
    label = 'Canvas' if kind == 'canvas' else 'Tasks'
    body = _side(items, 'Nothing here.')
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>%s</title>
<style>%s</style></head><body><div class="wrap">
<h1>%s <span class="sub">&middot; %d</span></h1>
<p class="sub"><a class="plain" href="/">&larr; back to today</a></p>
<div class="card">%s</div>
</div><script>%s</script></body></html>""" % (label, CSS, label, len(items), body, TICKJS)


TICKJS = """
document.addEventListener('click', async e=>{
  const b = e.target.closest('.tick'); if(!b || b.classList.contains('qtick')) return;
  const li = b.closest('li'); li.classList.toggle('crossed');
  b.innerHTML = li.classList.contains('crossed') ? '\u2713' : '\u25a1';
  await fetch('/api/done', {method:'POST', headers:{'Content-Type':'application/json'},
                            body: JSON.stringify({key: b.dataset.key})});
});
document.addEventListener('click', e=>{
  if(!e.target.closest('#qfold')) return;
  window.__showdone = !window.__showdone;
  drawQueue(window.__feed);
});
document.addEventListener('click', async e=>{
  const b = e.target.closest('.qtick'); if(!b) return;
  const r = (window.__feed||[]).find(x=>x.key===b.dataset.qkey); if(!r) return;
  r.done = r.done ? null : '1';
  drawQueue(window.__feed);
  await fetch('/api/queuedone', {method:'POST', headers:{'Content-Type':'application/json'},
                                 body: JSON.stringify({key: b.dataset.qkey})});
});
"""


def render():
    f = freshness()
    _vb = views_blob()
    p = panels_html()
    fl = flags()
    try:
        sc, det = metrics.compute_score()
        met = ('<div class="metrics">' + ''.join(
            '<div class="metric"><span class="big">%s</span><small>%s</small></div>'
            % (sc[k], k.replace('_', ' ')) for k in
            ('stall_days_max', 'projects_drifting', 'tasks_open', 'tasks_overdue'))
            + '</div><p class="sub" style="margin:10px 0 0">oldest: %s</p>'
            % esc(', '.join('%s (%dd)' % (t, n) for t, n in det.get('oldest', [])[:2])))
    except Exception as e:
        met = '<p class="sub">%s</p>' % esc(str(e))

    epochs = {}
    for k, v in f.items():
        try:
            epochs[k] = datetime.datetime.fromisoformat(v).timestamp() if v else None
        except Exception:
            epochs[k] = None

    rows = feed_rows()
    open_rows = [r for r in rows if not r['done']]
    done_rows = [r for r in rows if r['done']]
    feed = ''.join(_qrow_html(r) for r in open_rows)
    if rows and not open_rows:
        feed = '<p class="sub">All clear &mdash; everything this run turned up is dealt with.</p>'
    if done_rows:
        feed += ('<button class="qfold" id="qfold">&#9656; %d crossed off</button>'
                 % len(done_rows))
    outstanding = len(open_rows)
    live = session_exists()
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Zipper</title><style>%s</style></head><body><div class="wrap">
<h1><span id="pagedate">%s</span> <button id="dorefresh" class="btn">refresh</button></h1><div class="sub" id="status"></div>

<div class="card"><h2 class="hdr"><span id="daylabel">Today</span>
<span class="daynav"><button id="daytoday" hidden>today</button><button id="dayprev" aria-label="previous day">&lsaquo;</button><button id="daynext" aria-label="next day">&rsaquo;</button></span></h2>
<div class="today" id="p-today">%s</div></div>

<div class="card"><h2>What to work on</h2>
<div class="today">
  <div><h2>Canvas <a class="more" href="/canvas" target="_blank" rel="noopener">see all</a></h2><div id="p-work-canvas">%s</div></div>
  <div><h2>Tasks <a class="more" href="/tasks" target="_blank" rel="noopener">see all</a></h2><div id="p-work-tasks">%s</div></div>
</div></div>

<div class="card" id="termcard"><h2>Claude <span id="termstate" class="sub">%s</span>
<button id="termnew" class="btn"%s>new conversation</button>
<button id="termfull" class="btn" hidden>fullscreen</button>
<a id="termpop" class="btn" href="#" target="_blank" rel="noopener" hidden>pop out</a></h2>
<div id="termstart">%s</div>
<div id="termbody"><aside id="chatlist" hidden></aside><div id="termwrap"></div></div></div>

<div class="card"><h2>Next actions <a class="more" href="/views/now">see all</a></h2>%s</div>

<div class="card"><h2>Ventures <a class="more" href="/views/ventures">see all</a></h2>%s</div>

<div class="card"><h2>Signals</h2>
<div class="today">
  <div><h2>Flags (%d)</h2>%s</div>
  <div><h2>Execution</h2>%s</div>
</div></div>

<div class="card"><h2>This run &middot; <span id="qcount">%d</span></h2>
<div id="queue"%s>%s</div></div>

<footer><div class="fresh" id="fresh"></div>
<div class="sub vnavbar">%s</div></footer>
</div><script>window.__epochs=%s;window.__feed=%s;window.__session=%s;window.__mounted=false;window.__showdone=false;window.__queueready=%s;
window.__termup=%s;window.__termport=%s;window.__today=%s;window.__day=window.__today;window.__canvashost=%s;
%s%s</script></body></html>""" % (
        CSS, core.TODAY.strftime('%A %d %B %Y'),
        p['p-today'], p['p-work-canvas'], p['p-work-tasks'],
        'running \u2014 not attached here' if live else 'not started',
        '' if live else ' hidden',
        '' if terminal_up() else _startbtns(live, bool(_queue_prompt())),
        _view_html(_vb['views'].get('next_actions'), compact=True),
        _view_html(_vb['views'].get('scoreboard'), compact=True),
        len(fl), ''.join('<div class="flag">%s</div>' % esc(x) for x in fl) or '<p class="sub">Clean.</p>',
        met, outstanding, '' if rows else ' data-empty="1"',
        feed or '<p class="sub">Waiting for this run’s fetch…</p>',
        ' &middot; '.join('<a class="vnav" href="/views/%s">%s</a>'
                         % (pg['key'], esc(pg['title'])) for pg in _vb.get('pages', [])),
        json.dumps(epochs), json.dumps(rows), json.dumps(session_exists()),
        json.dumps(bool(_queue_prompt())), json.dumps(terminal_up()),
        json.dumps(TERM['port']), json.dumps(core.TODAY.isoformat()),
        json.dumps(canvas.CANVAS_HOST), JS, TICKJS)


# ---------------------------------------------------------------- http

SRV = {'server': None, 'clients': 0, 'quit_timer': None, 'daemon': False}

def _maybe_quit():
    """Last tab closed -> stop. A reload also drops the SSE stream, so wait a
    beat before believing it: a grace window tells a reload from a real close."""
    if SRV['clients'] > 0:
        return
    if SRV.get('daemon'):
        return          # always-on: the browser is a viewer, not the owner
    print('no clients left - shutting down')
    stop_terminal()
    threading.Thread(target=SRV['server'].shutdown, daemon=True).start()

def client_gone():
    SRV['clients'] -= 1
    if SRV['quit_timer']:
        SRV['quit_timer'].cancel()
    SRV['quit_timer'] = threading.Timer(4.0, _maybe_quit)
    SRV['quit_timer'].daemon = True
    SRV['quit_timer'].start()


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        sys.stderr.write('%s %s\n' % (self.address_string(), fmt % args))

    def _send(self, code, body, ctype='text/html; charset=utf-8'):
        raw = body.encode('utf-8') if isinstance(body, str) else body
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self._send(204, b'')

    def do_GET(self):
        core.TODAY = datetime.date.today()
        if self.path == '/':
            self._send(200, render())
        elif self.path == '/events':
            self._events()
        elif self.path.split('?')[0] == '/api/tmuxbuffer':
            # Claude Code does its own mouse selection -- that is why xterm sees
            # none -- and copies what you highlight into a *tmux* buffer, saying
            # "copied N chars to tmux buffer". That text lives on the box. This
            # hands the newest one to the page so it can put it on the operator's
            # actual clipboard, which is the thing he asked for.
            q = urllib.parse.parse_qs(self.path.partition('?')[2])
            seen = (q.get('seen') or [''])[0]
            self._send(200, json.dumps(newest_buffer(seen)), 'application/json')
        elif self.path == '/api/conversations':
            self._send(200, json.dumps({'conversations': conversation_rows()}),
                       'application/json')
        elif self.path.split('?')[0] == '/api/panels':
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            day = (q.get('day') or [''])[0]
            if not re.match(r'^\d{4}-\d{2}-\d{2}$', day or ''):
                day = None
            ep = {}
            for k, v in freshness().items():
                try:
                    ep[k] = datetime.datetime.fromisoformat(v).timestamp() if v else None
                except Exception:
                    ep[k] = None
            self._send(200, json.dumps({'epochs': ep, 'html': panels_html(day),
                                        'queue_ready': bool(_queue_prompt()),
                                        'session': session_exists(),
                                        'termup': terminal_up(),
                                        'termport': TERM['port']}), 'application/json')
        elif self.path == '/api/state':
            with LOCK:
                st = dict(STATE)
            st['ages'] = {k: ago(v) for k, v in freshness().items()}
            st['sig'] = content_sig()
            st['clients'] = SRV['clients']
            self._send(200, json.dumps(st), 'application/json')
        elif self.path == '/views' or self.path.startswith('/views/'):
            key = self.path[7:].strip('/') or (views_blob().get('pages') or [{'key': ''}])[0]['key']
            page = _views_page(key)
            if page is None:
                self._send(404, '<p>no such view page</p>')
            else:
                self._send(200, page)
        elif self.path in ('/tasks', '/canvas'):
            self._send(200, _list_page('task' if self.path == '/tasks' else 'canvas'))
        elif self.path == '/bookmarklet':
            base = 'http://%s' % self.headers.get('Host', 'localhost')
            code = BOOKMARKLET % ((core.TODAY - datetime.timedelta(days=14)).isoformat(),
                                  (core.TODAY + datetime.timedelta(days=120)).isoformat(), base)
            self._send(200, '<!doctype html><meta charset=utf-8><title>Bookmarklet</title>'
                            '<p>Drag to your bookmarks bar, then click it on your Canvas host:</p>'
                            '<p><a href="%s">Send Canvas &rarr; Zipper</a></p>' % html.escape(code))
        else:
            self._send(404, 'not found', 'text/plain; charset=utf-8')

    def _events(self):
        q = []
        with SUBS_LOCK:
            SUBS.append(q)
        SRV['clients'] += 1
        if SRV['quit_timer']:
            SRV['quit_timer'].cancel()
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'keep-alive')
        self.end_headers()
        try:
            while True:
                if q:
                    ev = q.pop(0)
                    self.wfile.write(('event: %s\ndata: %s\n\n'
                                      % (ev['kind'], json.dumps(ev))).encode())
                else:
                    self.wfile.write(b': ping\n\n')     # detects a closed tab
                    time.sleep(0.5)
                self.wfile.flush()
        except Exception:
            pass
        finally:
            with SUBS_LOCK:
                if q in SUBS:
                    SUBS.remove(q)
            client_gone()

    def do_POST(self):
        core.TODAY = datetime.date.today()
        if self.path == '/api/refresh':
            threading.Thread(target=do_refresh, daemon=True).start()
            self._send(202, json.dumps({'ok': True}), 'application/json')
        elif self.path == '/api/session':
            n = int(self.headers.get('Content-Length', 0))
            mode = 'blank'
            if n:
                try:
                    mode = json.loads(self.rfile.read(n).decode('utf-8')).get('mode', 'blank')
                except Exception:
                    pass
            self._send(200, json.dumps(start_terminal(mode) or {'ok': False}), 'application/json')
        elif self.path == '/api/clipdebug':
            # The clipboard is the one thing here that cannot be tested from
            # this box: whether it works depends on the browser, and on whether
            # the page is a secure context. So the page says what happened and
            # it lands in the journal, where it can be read instead of guessed.
            n = int(self.headers.get('Content-Length', 0))
            try:
                d = json.loads(self.rfile.read(n).decode('utf-8')) if n else {}
            except Exception:
                d = {}
            print('[clip] %s' % json.dumps(d, sort_keys=True)[:400], flush=True)
            self._send(200, json.dumps({'ok': True}), 'application/json')
        elif self.path == '/api/copybuffer':
            # A selection should land in the tmux paste buffer as well as the
            # browser's clipboard. They are different clipboards: the browser's
            # is the operator's own machine, tmux's is inside the box, and
            # pasting from one pane into another wants the second.
            n = int(self.headers.get('Content-Length', 0))
            try:
                d = json.loads(self.rfile.read(n).decode('utf-8')) if n else {}
            except Exception:
                d = {}
            text = d.get('text') or ''
            if not text:
                self._send(400, json.dumps({'error': 'text required'}), 'application/json')
                return
            sess = conversations.tmux_name(str(d['thread_id'])) if d.get('thread_id') else TERM['session']
            try:
                tmux = shutil.which('tmux')
                subprocess.run([tmux, 'load-buffer', '-b', 'zipper-copy', '-'],
                               input=text.encode('utf-8'), check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run([tmux, 'display-message', '-t', sess,
                                'copied %d chars to tmux buffer' % len(text)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self._send(200, json.dumps({'ok': True, 'chars': len(text)}),
                           'application/json')
            except Exception as e:
                self._send(500, json.dumps({'error': str(e)}), 'application/json')
        elif self.path == '/api/pasteimage':
            # An image on the clipboard is not text and cannot be typed into a
            # terminal. The browser can read it, so it posts the bytes here and
            # gets back a path -- which *is* text, and which Claude Code opens.
            n = int(self.headers.get('Content-Length', 0))
            ctype = (self.headers.get('Content-Type') or '').split(';')[0].strip()
            if n <= 0 or n > 16 * 1024 * 1024:
                self._send(400, json.dumps({'error': 'empty or too large (16MB max)'}),
                           'application/json')
                return
            ext = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/gif': '.gif',
                   'image/webp': '.webp'}.get(ctype)
            if not ext:
                self._send(400, json.dumps({'error': 'unsupported type %s' % ctype}),
                           'application/json')
                return
            try:
                os.makedirs(PASTE_DIR, exist_ok=True)
                name = datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + ext
                path = os.path.join(PASTE_DIR, name)
                with open(path, 'wb') as fh:
                    fh.write(self.rfile.read(n))
                _prune_pastes()
                self._send(200, json.dumps({'ok': True, 'path': path}), 'application/json')
            except Exception as e:
                self._send(500, json.dumps({'error': str(e)}), 'application/json')
        elif self.path == '/api/newconversation':
            self._send(200, json.dumps(new_conversation()), 'application/json')
        elif self.path == '/api/conversation':
            n = int(self.headers.get('Content-Length', 0))
            try:
                d = json.loads(self.rfile.read(n).decode('utf-8')) if n else {}
            except Exception:
                d = {}
            self._send(200, json.dumps(open_conversation(str(d.get('thread_id') or ''))),
                       'application/json')
        elif self.path == '/api/eventnote':
            n = int(self.headers.get('Content-Length', 0))
            try:
                d = json.loads(self.rfile.read(n).decode('utf-8')) if n else {}
                summary = (d.get('summary') or '').strip()
                day = (d.get('date') or '').strip()
                if not summary or not re.match(r'^\d{4}-\d{2}-\d{2}$', day):
                    raise ValueError('need summary and date')

                class _A:                      # events.cmd_event's argparse shape
                    match = summary
                    date = day
                    about = None
                    why = None
                buf = io.StringIO()
                old, sys.stdout = sys.stdout, buf
                try:
                    rc = events.cmd_event(_A())
                finally:
                    sys.stdout = old
                out = buf.getvalue().strip()
                if rc != 0:
                    raise ValueError(out.split('\n')[0] or 'no matching event')
                # the client re-fetches the panel itself; no SSE kind fits this
                self._send(200, json.dumps({'ok': True, 'msg': out}), 'application/json')
            except Exception as e:
                self._send(400, json.dumps({'error': str(e)[:80]}), 'application/json')
        elif self.path == '/api/newsession':
            self._send(200, json.dumps(new_session()), 'application/json')
        elif self.path == '/api/done':
            n = int(self.headers.get('Content-Length', 0))
            try:
                key = json.loads(self.rfile.read(n).decode('utf-8'))['key']
                self._send(200, json.dumps(toggle_done(key)), 'application/json')
            except Exception as e:
                self._send(400, json.dumps({'error': str(e)}), 'application/json')
        elif self.path == '/api/queuedone':
            n = int(self.headers.get('Content-Length', 0))
            try:
                key = json.loads(self.rfile.read(n).decode('utf-8'))['key']
                res = feed_mark(key)
                if res['ok']:
                    publish('feed', rows=feed_rows())
                self._send(200, json.dumps(res), 'application/json')
            except Exception as e:
                self._send(400, json.dumps({'error': str(e)}), 'application/json')
        elif self.path == '/discord':
            # The bot posts every message it sees here. It is loopback-only and
            # unauthenticated, exactly like the rest of this server.
            try:
                n = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(n) or b'{}')
            except Exception:
                self._send(400, json.dumps({'error': 'bad json'}), 'application/json')
                return
            text = (body.get('content') or body.get('prompt') or '').strip()
            if not text:
                self._send(400, json.dumps({'error': 'content required'}),
                           'application/json')
                return
            tid = body.get('discord_thread_id')
            if tid:
                # A thread is a conversation. Its own instance gets the message,
                # started or resumed as needed, and Discord shows the typing
                # indicator until that instance answers -- chat.discord_send
                # clears it, so every reply path ends the indicator exactly once.
                chat.discord_typing(True, tid)
                if not (conversations.load().get(str(tid)) or {}).get('title'):
                    # The thread's name in Discord is the first line of the
                    # message that opened it; the chat list should read the same
                    # rather than showing an id nobody recognises.
                    conversations.touch(str(tid), title=' '.join(text.split())[:60])
                res = conversations.deliver(str(tid), _tagged(text, body.get('source', 'discord')),
                                            body.get('source', 'discord'))
                if res.get('ok'):
                    publish('diff', 'terminal    %s -> thread %s (%s)'
                            % (body.get('source', 'discord'), tid, res.get('state')))
                else:
                    chat.discord_typing(False, tid)
            else:
                res = deliver_to_claude(text, body.get('source', 'discord'))
            self._send(200 if res.get('ok') else 503, json.dumps(res),
                       'application/json')
        elif self.path == '/api/canvas':
            n = int(self.headers.get('Content-Length', 0))
            try:
                before = snapshot_data()
                items = json.loads(self.rfile.read(n).decode('utf-8'))
                rows, skipped = canvas._canvas_parse(items)
                with open(canvas.CANVAS_JSON, 'w', encoding='utf-8') as fh:
                    json.dump({'fetched': datetime.datetime.now().isoformat(timespec='seconds'),
                               'source': 'bookmarklet', 'items': rows}, fh, indent=1)
                emit_diff(before, snapshot_data())
                publish('source', 'canvas')
                self._send(200, json.dumps({'ok': True, 'kept': len(rows)}), 'application/json')
            except Exception as e:
                self._send(400, json.dumps({'error': str(e)}), 'application/json')
        else:
            self._send(404, 'not found', 'text/plain; charset=utf-8')


BOOKMARKLET = (
    "javascript:(async()=>{let u='/api/v1/planner/items?start_date=%s&end_date=%s&per_page=100',a=[];"
    "while(u){const r=await fetch(u,{credentials:'same-origin'});let t=await r.text();"
    "if(t.startsWith('while(1);'))t=t.slice(9);a.push(...JSON.parse(t));"
    "const m=(r.headers.get('Link')||'').match(/<([^>]+)>;\\s*rel=\"next\"/);u=m?m[1]:null;}"
    "await fetch('%s/api/canvas',{method:'POST',headers:{'Content-Type':'application/json'},"
    "body:JSON.stringify(a)});alert('sent '+a.length+' items to Zipper');})()")


def conversation_reaper():
    """Close conversations once their prompt cache has gone cold.

    The message is the point, not the kill: an idle instance costs nothing, but
    the next message to a cold one is re-read from scratch at full price. The
    operator asked to know that before he types, not after.
    """
    def notify(tid, text):
        chat.discord_send(text, thread_id=tid)
    while True:
        time.sleep(120)
        try:
            for tid in conversations.reap(notify=notify):
                publish('diff', 'terminal    conversation %s closed (idle)' % tid)
        except Exception as e:
            print('[reaper] %s' % e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8800)
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--open', action='store_true', help='open a browser and exit when it closes')
    ap.add_argument('--no-terminal', action='store_true', help='skip the embedded Claude session')
    ap.add_argument('--term-port', type=int, default=8801)
    # Defaults come from the environment so a systemd EnvironmentFile can set
    # them; the flags still win when both are given.
    ap.add_argument('--term-host', default=os.environ.get('ZIPPER_TERM_HOST', '127.0.0.1'),
                    help='ttyd -W is a live shell; leave on loopback unless you mean it')
    ap.add_argument('--term-cred', default=os.environ.get('ZIPPER_TERM_CRED', ''),
                    help='user:password for the terminal; REQUIRED to bind it off loopback')
    ap.add_argument('--mark', metavar='KEY',
                    help='cross a queue item off by key (or unique prefix) and exit')
    ap.add_argument('--mark-all', action='store_true', dest='mark_all',
                    help='cross off every run-queue item still open and exit')
    ap.add_argument('--queue', action='store_true', help='print the run queue and exit')
    ap.add_argument('--daemon', action='store_true',
                    help='stay up when the last tab closes (for systemd)')
    a = ap.parse_args()
    if a.mark or a.queue or a.mark_all:
        feed_load()
        if a.queue:
            for r in feed_rows():
                print('%s %s %s' % (r['key'], '[x]' if r['done'] else '[ ]', r['text']))
            return 0
        if a.mark_all:
            n = feed_mark_all()['marked']
            print('crossed off  %d item(s)' % n if n else 'nothing open')
            return 0
        res = feed_mark(a.mark)
        print(res.get('error') or ('crossed off  %s' % res['text'] if res['done']
                                   else 'restored     %s' % res['text']))
        return 0 if res['ok'] else 1
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    TERM['enabled'] = not a.no_terminal
    TERM['port'] = a.term_port
    TERM['host'] = a.term_host
    TERM['cred'] = a.term_cred
    SRV['server'] = srv
    SRV['daemon'] = a.daemon
    feed_load()
    threading.Thread(target=feed_watch, daemon=True).start()
    threading.Thread(target=conversation_reaper, daemon=True).start()
    url = 'http://%s:%d/' % (a.host, a.port)
    print('zipper dashboard on %s%s' % (url, '  (daemon)' if a.daemon else ''))
    if a.daemon:
        print('inbound: POST %sdiscord   {"content": "..."}' % url)
    threading.Thread(target=do_refresh, daemon=True).start()
    if a.open:
        import webbrowser
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    stop_terminal()
    print('stopped')
    return 0


if __name__ == '__main__':
    sys.exit(main() or 0)
