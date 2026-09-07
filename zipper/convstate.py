"""What a conversation is *doing*, and the list the dashboard draws from.

Liveness, the busy/idle read, titles from the transcript, when it last said
anything, closing one, and the sweep and reaper that keep the registry honest.
Everything here observes a conversation rather than defining one -- see
`zipper.convcore` for that.

Split out of `zipper/conversations.py` on 2026-09-07.
"""
import os, re, json, time, shutil, subprocess, datetime

from .core import *          # noqa: F401,F403
from . import core
from .convcore import (CLAUDE_PROJECTS, CONV_JSON, IDLE_NOTICE, _pane, _project_dir,
                       _tmux, alive, load, save, session_id, target, tmux_name,
                       touch, transcript)
from .ttyd import _port_open, stop_ttyd

def last_active(thread_id):
    """Newest of what we know: our own last delivery, and the transcript's mtime.

    The transcript is what catches a conversation that is working -- it grows
    while Claude thinks, long after the message that started it arrived.
    """
    stamps = []
    row = load().get(str(thread_id)) or {}
    if row.get('last_active'):
        try:
            stamps.append(datetime.datetime.fromisoformat(row['last_active']).timestamp())
        except ValueError:
            pass
    stamps.append(last_message_at(thread_id))
    return max(stamps) if stamps else 0.0

def close(thread_id, reason='idle', force=False):
    """Kill a conversation's session.

    A **bound** thread is refused unless forced. Its tmux session is not ours --
    it is a terminal the operator is sitting in front of, adopted by a thread so
    it could be reached from a phone. Closing it kills that conversation
    outright, and the next thing he types starts a stranger with no context.
    That happened once, 2026-09-06, from a cleanup command that meant to tidy a
    test: `--close` on the bound row ran `kill-session -t zipper` and took the
    dashboard's own pane with it. The transcript survived and could be resumed,
    but nothing warned first, so the guard lives here rather than in the callers.
    """
    row = load().get(str(thread_id)) or {}
    if (row.get('bound') or row.get('pinned')) and not force:
        return {'ok': False, 'error': 'conversation %s is bound to tmux session %r -- '
                                      'closing it would kill a live terminal. '
                                      'Pass force=True if that is really what you want.'
                                      % (thread_id, row.get('tmux'))}
    stop_ttyd(thread_id)
    try:
        subprocess.run([_tmux(), 'kill-session', '-t', target(thread_id)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except RuntimeError:
        pass
    d = load()
    if str(thread_id) in d:
        d[str(thread_id)]['closed'] = True
        d[str(thread_id)]['closed_at'] = datetime.datetime.now().isoformat(timespec='seconds')
        d[str(thread_id)]['closed_reason'] = reason
        save(d)
    return {'ok': True}




_TITLES = {}


def title(thread_id, default=''):
    """The conversation's own name, from the newest `ai-title` in its transcript.

    Cached on the transcript's size and mtime: these files reach megabytes and
    the list polls every few seconds, so only the tail is ever read -- the last
    title written wins, and it is always near the end.
    """
    p = transcript(thread_id)
    try:
        st = os.stat(p)
    except OSError:
        return default
    key = (st.st_mtime, st.st_size)
    hit = _TITLES.get(str(thread_id))
    if hit and hit[0] == key:
        return hit[1] or default
    found = ''
    try:
        with open(p, 'rb') as fh:
            if st.st_size > 262144:
                fh.seek(-262144, os.SEEK_END)
                fh.readline()          # drop the partial line the seek landed in
            for raw in fh:
                if b'"ai-title"' not in raw:
                    continue
                try:
                    d = json.loads(raw.decode('utf-8', 'replace'))
                except ValueError:
                    continue
                if d.get('type') == 'ai-title' and d.get('aiTitle'):
                    found = d['aiTitle'].strip()
    except OSError:
        return default
    _TITLES[str(thread_id)] = (key, found)
    return found or default


_MSGTIME = {}
_SEEN = {}          # thread -> when the busy marker was last seen


def last_message_at(thread_id):
    """When this conversation last exchanged a message.

    The file's mtime is not that. Resuming a conversation appends bookkeeping --
    cost-state, bridge-session, a session header -- and rewrites the mtime
    without anything having been said, which sent a conversation to the top of
    the list for being reopened. Those entries carry no timestamp; user and
    assistant messages do, so the newest of those is the honest answer.
    """
    p = transcript(thread_id)
    try:
        st = os.stat(p)
    except OSError:
        return 0.0
    key = (st.st_mtime, st.st_size)
    hit = _MSGTIME.get(str(thread_id))
    if hit and hit[0] == key:
        return hit[1]
    newest = ''
    try:
        with open(p, 'rb') as fh:
            if st.st_size > 262144:
                fh.seek(-262144, os.SEEK_END)
                fh.readline()
            for raw in fh:
                if b'"timestamp"' not in raw:
                    continue
                if b'"type":"user"' not in raw and b'"type":"assistant"' not in raw:
                    continue
                try:
                    d = json.loads(raw.decode('utf-8', 'replace'))
                except ValueError:
                    continue
                ts = d.get('timestamp') or ''
                if ts > newest:
                    newest = ts
    except OSError:
        return 0.0
    out = 0.0
    if newest:
        try:
            out = datetime.datetime.fromisoformat(newest.replace('Z', '+00:00')).timestamp()
        except ValueError:
            out = 0.0
    _MSGTIME[str(thread_id)] = (key, out)
    return out


def detect_session(thread_id):
    """Work out which transcript a *bound* conversation is actually writing.

    A bound row adopted a session that was already running, and nothing tells us
    its id: the pane's process carries no --session-id, and Claude appends and
    closes the file rather than holding it open. So the id is inferred -- the
    newest transcript in this vault's project directory that no other
    conversation has claimed.

    It matters more than it sounds. The id decides which file `last_active`
    reads, so a wrong one leaves the conversation being typed in looking idle
    and stuck at the bottom of the list; and it decides what `--resume` would
    reopen if that pane ever died. This was wrong once: the terminal was bound
    to the session that started when its predecessor was killed, while the pane
    had gone on to resume the older conversation, and everything downstream read
    a file that had stopped moving forty minutes earlier.
    """
    row = load().get(str(thread_id)) or {}
    if not row.get('bound') or not alive(thread_id):
        return row.get('session_id')
    claimed = {r.get('session_id') for t, r in load().items()
               if str(t) != str(thread_id) and r.get('session_id')}
    best, best_m = row.get('session_id'), -1
    try:
        names = os.listdir(_project_dir())
    except OSError:
        return best
    for n in names:
        if not n.endswith('.jsonl'):
            continue
        sid = n[:-6]
        if sid in claimed:
            continue
        try:
            m = os.path.getmtime(os.path.join(_project_dir(), n))
        except OSError:
            continue
        if m > best_m:
            best, best_m = sid, m
    if best and best != row.get('session_id'):
        touch(thread_id, session_id=best)
    return best


def sweep():
    """Drop the ttyd of any conversation whose session is gone.

    A session can die without anything here being asked -- Ctrl-C in the pane
    ends Claude, and the tmux session ends with it. Its ttyd stays bound to the
    port and would happily serve `tmux new -A`, which is a *new* conversation
    wearing the old one's name. Take the viewer down with the session.
    """
    gone = []
    for tid, row in list(load().items()):
        if row.get('bound'):
            detect_session(tid)
    for tid, row in list(load().items()):
        dead = not alive(tid)
        if not dead and row.get('port') and not running_claude(tid):
            # Claude was quit inside the pane. End the session too, or ttyd
            # serves the empty shell tmux leaves behind as if it were the
            # conversation.
            #
            # Checked twice, a beat apart. This sweep kills things, and it
            # already killed two working conversations once by trusting a
            # single reading -- a cheap second look is worth more than the
            # second it costs.
            time.sleep(0.4)
            if not running_claude(tid):
                close(tid, reason='exited')
                dead = True
        if row.get('port') and dead:
            stop_ttyd(tid)
            gone.append(tid)
    return gone


def running_claude(thread_id):
    """Is Claude still the process in this session's pane?

    tmux staying alive is not the same as the conversation being alive. Ctrl-C
    ends Claude but leaves the pane if anything else is running in it, and a
    session someone started by hand can hold a plain shell. The session then
    exists, the name matches, and the dashboard would show a terminal that is
    not the conversation it is labelled with. (ttyd used to *recreate* the
    session as well, with `tmux new -A`; it attaches now -- see `ensure_ttyd`.)

    Read the pane's *process*, not `pane_current_command`. That field reports
    whatever is in the foreground, which during a tool call is bash or python --
    so it reported "not Claude" for two conversations that were merely working,
    and the sweep below closed them. The pane's own pid is claude (the launcher
    execs it), and checking its descendants covers a session someone started by
    hand.
    """
    try:
        r = subprocess.run([_tmux(), 'list-panes', '-t', target(thread_id),
                            '-F', '#{pane_pid}'], capture_output=True, text=True, timeout=5)
    except Exception:
        return False
    pids = [p for p in r.stdout.split() if p.isdigit()]
    if not pids:
        return False
    for pid in pids:
        try:
            with open('/proc/%s/comm' % pid) as fh:
                if 'claude' in fh.read():
                    return True
        except OSError:
            continue
        try:
            kids = subprocess.run(['pgrep', '-P', pid], capture_output=True, text=True, timeout=5)
            for k in kids.stdout.split():
                with open('/proc/%s/comm' % k) as fh:
                    if 'claude' in fh.read():
                        return True
        except Exception:
            continue
    return False


def state(thread_id):
    """working | waiting | closed.

    Claude Code prints "esc to interrupt" in its status line for exactly as long
    as it is doing something, so the pane itself answers the question. Read from
    the terminal rather than tracked in the registry: an instance can start and
    finish work without this process being told, and a state we maintained would
    drift the moment it did.
    """
    if not alive(thread_id) or not running_claude(thread_id):
        return 'closed'
    # The *status line* only -- the last non-empty line of the pane. Scanning
    # the whole pane made any conversation that merely displayed the words "esc
    # to interrupt" look permanently busy, which is not a hypothetical: a
    # session discussing this very check stayed yellow after it had finished.
    # Look at the bottom of the pane, not just its last line: the footer is
    # rewritten several times a second and a capture lands on a blank frame
    # often enough to matter -- which is what made a long turn flicker to green
    # and read as finished. The spinner line counts too; during a long tool call
    # it is the only thing on screen that says work is happening.
    #
    # Both tests are anchored to how those lines *start*, so a conversation that
    # merely prints the words "esc to interrupt" -- this one, constantly -- is
    # not mistaken for a busy one.
    lines = [l.strip() for l in _pane(thread_id).splitlines() if l.strip()][-14:]
    busy = any((l.startswith('\u23f5\u23f5') and 'esc to interrupt' in l)
               or (l.startswith('\u273b') and ('tokens' in l or 'esc to interrupt' in l))
               for l in lines)
    if busy:
        _SEEN[str(thread_id)] = time.time()
        return 'working'
    # Sticky for 25 seconds after the marker was last actually seen: a long tool
    # call can leave nothing on screen that says "busy" for a while. It is deliberately *not*
    # inferred from the transcript being written: resuming a conversation writes
    # to it, which lit the dot yellow for a session that had done nothing but
    # come back.
    if time.time() - _SEEN.get(str(thread_id), 0) < 25:
        return 'working'
    return 'waiting'


def listing():
    """Every conversation we know of, in a stable order.

    Ordered by the last *message*, newest first -- measured from the transcript,
    which is the only record that sees a message typed straight into a terminal
    as well as one delivered from Discord. Reading a conversation does not move
    it, and neither does anything else the machinery writes: an earlier version
    sorted on a registry stamp that every incidental write bumped, and a later
    one stopped bumping it at all, which left the conversation being typed in
    sitting at the bottom of the list.
    """
    out = []
    for tid, row in load().items():
        out.append(dict(row, thread_id=tid, alive=alive(tid), state=state(tid),
                        serving=(_port_open(row.get('host') or '127.0.0.1', int(row['port']))
                                 if row.get('port') else False),
                        last_active_ts=last_active(tid),
                        idle_for=int(time.time() - last_active(tid)) if last_active(tid) else None))
    # last_active_ts is the newest of the registry stamp and the transcript's
    # mtime. The registry only sees messages this process delivered, so sorting
    # on it alone left out everything typed straight into a terminal -- which is
    # every message in the conversation the operator is actually sitting in.
    out.sort(key=lambda r: r['last_active_ts'] or 0, reverse=True)
    return out


def reap(notify=None):
    """Close conversations that have gone quiet for longer than the cache holds.

    Not a token saving -- an idle instance costs nothing to leave running. It
    is a *price signal*: past this point the next message re-reads the whole
    conversation at full input price instead of hitting the prompt cache, and
    the operator asked to be told before that happens rather than discover it
    on the bill.
    """
    closed = []
    for row in listing():
        if not row['alive'] or row.get('idle_for') is None:
            continue
        if row.get('pinned'):
            continue          # a bound conversation is somebody's live terminal
        if row['idle_for'] < IDLE_NOTICE:
            continue
        tid = row['thread_id']
        if notify:
            try:
                notify(tid, '_[conversation idle, closing — the prompt cache has expired, '
                            'so picking this up again re-reads it at full price]_')
            except Exception:
                pass
        close(tid, reason='idle')
        closed.append(tid)
    return closed


def _ago(ts):
    s = max(0, int(time.time() - ts))
    if s < 90:
        return '%ds' % s
    if s < 5400:
        return '%dm' % round(s / 60)
    return '%.1fh' % (s / 3600.0)


def cmd_conversations(a):
    """See what is running, and close what should not be."""
    if a.close:
        r = close(a.close, reason='manual', force=a.force)
        if not r['ok']:
            print('conversations: %s' % r['error'])
            return 1
        print('closed conversation %s' % a.close)
        return 0
    rows = listing()
    if not rows:
        print('conversations: none yet')
        return 0
    print('%-22s %-7s %-10s %s' % ('thread', 'state', 'idle', 'session id'))
    for r in rows:
        idle = _ago(r['last_active_ts']) if r['last_active_ts'] else '-'
        print('%-22s %-7s %-10s %s' % (r['thread_id'],
                                       'live' if r['alive'] else 'closed',
                                       idle, r.get('session_id', '')[:8]))
    print('\nidle close at %d min. A closed conversation resumes on the next message;\n'
          'its transcript is on disk either way.' % (IDLE_NOTICE // 60))
    return 0
