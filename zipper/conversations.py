"""zipper.conversations

Many Claude conversations at once, one per Discord thread.

A Discord thread is a conversation. Everything else here follows from that:
its tmux session, its Claude session id, whether it is alive, and when it was
last spoken to. A message in a thread reaches the instance holding it; a
message in the channel starts a new one.

**Two conversations can edit the vault at the same time, and nothing stops
them.** Deliberate, 2026-09-06: parallel instances are cheap to run and the
locking to make them safe is not worth writing for one person who knows what
he has running. The failure it invites is real, though -- two sessions editing
one note, or committing over each other, produce conflicts and lost edits that
neither instance can see. If that starts happening, this is where the lock
goes; until then, don't work the same project in two threads at once.
"""
import os, json, time, uuid, shutil, subprocess, datetime

from .core import *          # noqa: F401,F403 -- the shared vocabulary
from . import core


CONV_JSON = os.path.join(INBOX, 'conversations.json')

# The Claude session id is derived, never stored: uuid5 over the thread id is
# stable, so a thread finds its conversation again with no mapping file to fall
# out of sync. Losing conversations.json costs the titles and the timers, not
# the conversations themselves.
NS = uuid.uuid5(uuid.NAMESPACE_URL, 'zipper-discord-thread')

# Claude keeps one jsonl per session under a directory named for the working
# directory, with the separators replaced by dashes. That file's mtime is the
# only honest measure of when a conversation last did anything -- the registry
# only knows when we last spoke *to* it.
CLAUDE_PROJECTS = os.path.expanduser('~/.claude/projects')

IDLE_NOTICE = int(os.environ.get('ZIPPER_IDLE_SECONDS', 55 * 60))


def session_id(thread_id):
    return str(uuid.uuid5(NS, str(thread_id)))


def tmux_name(thread_id):
    """Which tmux session holds this thread.

    Normally derived, like the session id. The exception is a *bound* thread:
    an already-running conversation -- the dashboard's own terminal, say --
    adopted by a thread so it can be carried on from a phone. Its pane is not
    ours to name, so the registry records the real one.
    """
    row = load().get(str(thread_id)) or {}
    return row.get('tmux') or 'zipper-%s' % thread_id


def bind(thread_id, tmux, session_id=None, title=''):
    """Point a Discord thread at a conversation that is already running.

    Bound rows are pinned: the reaper must never close one, because the session
    on the other end is something the operator is using -- closing the terminal
    he is typing in to save a cache he isn't paying for would be a poor trade.
    """
    return touch(thread_id, tmux=tmux, bound=True, pinned=True,
                 session_id=session_id or (load().get(str(thread_id)) or {}).get('session_id'),
                 title=title, closed=False)


def _project_dir(path=None):
    return os.path.join(CLAUDE_PROJECTS, (path or VAULT).replace('/', '-'))


def transcript(thread_id):
    row = load().get(str(thread_id)) or {}
    return os.path.join(_project_dir(), (row.get('session_id') or session_id(thread_id)) + '.jsonl')


def _tmux():
    t = shutil.which('tmux')
    if not t:
        raise RuntimeError('tmux not installed')
    return t


def alive(thread_id):
    """Is this thread's tmux session actually running?

    The `=` prefix makes the target an **exact** name, not a prefix. Without it
    tmux resolves `-t zipper` onto `zipper-<some other thread>`, so a bound row
    naming the session `zipper` reads its liveness off whichever dashboard
    terminal happens to be up. That row is also pinned against the reaper, so it
    listed as live forever and blocked `zipper commit` on every pass -- which is
    exactly what a hand-bound row did on 2026-09-06.
    """
    try:
        return subprocess.run([_tmux(), 'has-session', '-t', '=' + tmux_name(thread_id)],
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0
    except RuntimeError:
        return False


def load():
    try:
        return json.load(open(CONV_JSON, encoding='utf-8'))
    except Exception:
        return {}


def save(d):
    os.makedirs(os.path.dirname(CONV_JSON), exist_ok=True)
    tmp = CONV_JSON + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(d, fh, indent=1, sort_keys=True)
    os.replace(tmp, CONV_JSON)


def touch(thread_id, active=False, **fields):
    """Record something about a conversation.

    `active` is what moves it up the list, and only a message does that. Every
    incidental write used to bump it -- caching a title, remembering a port,
    opening the thing to read it -- so merely looking at a conversation sent it
    to the top, and the order the operator was navigating by rearranged itself
    under his cursor.
    """
    d = load()
    row = d.setdefault(str(thread_id), {})
    row.setdefault('started', datetime.datetime.now().isoformat(timespec='seconds'))
    # A bound row's session id belongs to the conversation it adopted, not to
    # the thread -- overwriting it with the derived one would resume the wrong
    # transcript if that session ever had to be restarted.
    if not row.get('bound'):
        row['session_id'] = session_id(thread_id)
    now = datetime.datetime.now().isoformat(timespec='seconds')
    row.setdefault('last_active', now)
    if active:
        row['last_active'] = now
    row.update(fields)
    save(d)
    return row


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


def _pane(thread_id):
    try:
        r = subprocess.run([_tmux(), 'capture-pane', '-p', '-t', tmux_name(thread_id)],
                           capture_output=True, text=True, timeout=5)
        return r.stdout
    except Exception:
        return ''


def _wait_ready(thread_id, timeout=25.0):
    """Block until Claude's input box is drawn.

    A cold start is not instant, and a paste that lands before the TUI is
    listening goes nowhere -- worse, the Enter after it does nothing and the
    message sits in the box looking sent. The prompt character is the signal
    that it is ready to be typed at.
    """
    end = time.time() + timeout
    while time.time() < end:
        if '\u276f' in _pane(thread_id):
            return True
        time.sleep(0.5)
    return False


def start(thread_id, prompt=None):
    """Bring a conversation up, detached. Resumes if it has spoken before.

    --session-id assigns the id on a first run; --resume takes it back up. They
    are not interchangeable -- passing --session-id an id Claude already knows
    is an error -- so the transcript on disk decides which one this is.
    """
    name = tmux_name(thread_id)
    row = load().get(str(thread_id)) or {}
    sid = row.get('session_id') or session_id(thread_id)
    resumed = os.path.exists(transcript(thread_id))
    flag = ['--resume', sid] if resumed else ['--session-id', sid]
    inner = ' '.join(['exec', 'claude'] + flag)
    env = dict(os.environ)
    env['ZIPPER_DISCORD_THREAD'] = str(thread_id)
    env['ZIPPER_VAULT'] = VAULT
    env.setdefault('HOME', '/root')
    subprocess.run(
        [_tmux(), 'new-session', '-d', '-s', name, '-c', VAULT,
         '-e', 'ZIPPER_DISCORD_THREAD=%s' % thread_id,
         '-e', 'ZIPPER_VAULT=%s' % VAULT,
         # A detached tmux gets whatever PATH the service had. Claude lives in
         # ~/.local/bin, which systemd's default PATH does not include -- the
         # same trap that once left the terminal card saying "ttyd not installed".
         'bash', '-lc',
         'export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"; ' + inner],
        check=True, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _wait_ready(thread_id)      # a paste before the TUI is listening is lost
    # Reopening is not saying something: it must not reorder the list.
    touch(thread_id, resumed=resumed, closed=False)
    if prompt:
        paste(thread_id, prompt)
    return {'ok': True, 'resumed': resumed, 'session_id': sid, 'tmux': name}


def paste(thread_id, text):
    """Type a block into a conversation's pane.

    Bracketed paste, then a separate Enter -- as keystrokes every newline in a
    multi-line message would submit a fragment. See serve.inject_queue.
    """
    name = tmux_name(thread_id)
    if not alive(thread_id):
        return {'ok': False, 'error': 'conversation not running'}
    buf = 'zipper-%s' % thread_id
    try:
        subprocess.run([_tmux(), 'load-buffer', '-b', buf, '-'],
                       input=text.encode('utf-8'), check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run([_tmux(), 'paste-buffer', '-b', buf, '-t', name, '-p', '-d'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.4)
        subprocess.run([_tmux(), 'send-keys', '-t', name, 'Enter'], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # An Enter that arrives while the TUI is still settling is swallowed,
        # and the message then sits in the input box looking delivered -- the
        # one failure here that is invisible from outside. Check that the text
        # actually left the box, and press again if it did not.
        probe = (text.strip().splitlines() or [''])[0][:40]
        for _ in range(6):
            time.sleep(0.6)
            pane = _pane(thread_id)
            tail = pane.rsplit('\u276f', 1)[-1] if '\u276f' in pane else pane
            if probe not in tail:
                break
            subprocess.run([_tmux(), 'send-keys', '-t', name, 'Enter'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        return {'ok': False, 'error': str(e)}
    touch(thread_id, active=True)
    return {'ok': True}


def deliver(thread_id, text, source='discord'):
    """The whole Discord path in one call: start or resume, then hand it over."""
    if alive(thread_id):
        res = paste(thread_id, text)
        if res['ok']:
            return dict(res, state='live')
        return res
    r = start(thread_id, prompt=text)
    return dict(r, state='resumed' if r.get('resumed') else 'new')


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
    name = tmux_name(thread_id)
    stop_ttyd(thread_id)
    try:
        subprocess.run([_tmux(), 'kill-session', '-t', name],
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



# ---------------------------------------------------------------- viewing
#
# ttyd serves exactly one command per port, and ours attaches one tmux session,
# so watching several conversations means one ttyd each. Ports are handed out
# from a base and remembered in the registry; the process is not, because a
# serve.py restart must be able to adopt the ttyd it left behind rather than
# fight it for the port. Whether the port answers is the only durable truth.

TTYD_BASE = int(os.environ.get('ZIPPER_TTYD_BASE', 8810))
TTYD_SPAN = 20
PROCS = {}


def _port_open(host, port, timeout=0.3):
    import socket
    c = socket.socket()
    c.settimeout(timeout)
    try:
        return c.connect_ex((host, port)) == 0
    finally:
        c.close()


def _pick_port(thread_id, host):
    row = load().get(str(thread_id)) or {}
    if row.get('port'):
        return int(row['port'])
    taken = {int(r['port']) for r in load().values() if r.get('port')}
    for p in range(TTYD_BASE, TTYD_BASE + TTYD_SPAN):
        if p in taken or _port_open(host, p):
            continue
        return p
    raise RuntimeError('no free ttyd port in %d-%d' % (TTYD_BASE, TTYD_BASE + TTYD_SPAN))


def ensure_ttyd(thread_id, host='127.0.0.1', cred='', font=13):
    """A ttyd serving this conversation's pane, started if it isn't already.

    Attaches with `tmux new -A`, so the websocket owns nothing: closing the tab
    detaches and the conversation keeps running.

    Bound to loopback and served through nginx under `/t/<port>/`, which is what
    makes the dashboard's origin the only one a browser ever sees. Pointed
    straight at these ports instead, every conversation is a separate origin and
    basic auth prompts again for each -- being signed in to the dashboard has to
    be enough. `--base-path` is load-bearing for that: without it ttyd's own
    asset and websocket URLs are absolute and miss the prefix.

    A credential still applies if one is configured, and is *required* on any
    host but loopback -- ttyd -W hands out a live shell.
    """
    if not alive(thread_id):
        return {'ok': False, 'error': 'conversation not running'}
    port = _pick_port(thread_id, host)
    if _port_open(host, port):
        touch(thread_id, port=port, host=host)
        return {'ok': True, 'port': port, 'adopted': True}
    exe = shutil.which('ttyd')
    if not exe:
        return {'ok': False, 'error': 'ttyd not installed'}
    if host != '127.0.0.1' and not cred:
        return {'ok': False, 'error': 'refusing to expose an unauthenticated shell'}
    tmux = _tmux()
    args = [exe, '-p', str(port), '-i', host, '-W']
    if cred:
        args += ['-c', cred]
    args += ['-b', '/t/%d' % port,
             # See serve.start_terminal: with mouse reporting on, macOS needs
             # this before Option-drag can select anything.
             '-t', 'macOptionClickForcesSelection=true',
             '-t', 'rightClickSelectsWord=true',
             '-t', 'fontSize=%d' % font,
             '-t', 'fontFamily=SFMono-Regular,Menlo,monospace',
             '-t', 'theme={"background":"#171614","foreground":"#ece8e1"}',
             tmux, 'new', '-A', '-s', tmux_name(thread_id)]
    PROCS[str(thread_id)] = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL)
    end = time.time() + 6
    while time.time() < end:
        if _port_open(host, port):
            touch(thread_id, port=port, host=host)
            return {'ok': True, 'port': port}
        time.sleep(0.15)
    return {'ok': False, 'error': 'ttyd did not come up on port %d' % port}


def stop_ttyd(thread_id):
    proc = PROCS.pop(str(thread_id), None)
    if proc:
        try:
            proc.terminate()
        except Exception:
            pass
    d = load()
    if str(thread_id) in d:
        d[str(thread_id)].pop('port', None)
        save(d)


# Claude Code names its own conversations: it writes an `ai-title` line into the
# transcript and rewrites it as the subject moves. That is the right name for
# the list -- it describes the conversation rather than its delivery mechanism,
# and it exists for sessions that never touched Discord at all.
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
    ends Claude, and ttyd -- which attaches with `tmux new -A` -- will happily
    recreate the session on the next connection with a plain shell in it. The
    session then exists, the name matches, and the dashboard shows a terminal
    that is not the conversation it is labelled with.

    Read the pane's *process*, not `pane_current_command`. That field reports
    whatever is in the foreground, which during a tool call is bash or python --
    so it reported "not Claude" for two conversations that were merely working,
    and the sweep below closed them. The pane's own pid is claude (the launcher
    execs it), and checking its descendants covers a session someone started by
    hand.
    """
    try:
        r = subprocess.run([_tmux(), 'list-panes', '-t', tmux_name(thread_id),
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
