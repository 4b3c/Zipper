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
    return 'zipper-%s' % thread_id


def _project_dir(path=None):
    return os.path.join(CLAUDE_PROJECTS, (path or VAULT).replace('/', '-'))


def transcript(thread_id):
    return os.path.join(_project_dir(), session_id(thread_id) + '.jsonl')


def _tmux():
    t = shutil.which('tmux')
    if not t:
        raise RuntimeError('tmux not installed')
    return t


def alive(thread_id):
    try:
        return subprocess.run([_tmux(), 'has-session', '-t', tmux_name(thread_id)],
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


def touch(thread_id, **fields):
    d = load()
    row = d.setdefault(str(thread_id), {})
    row.setdefault('started', datetime.datetime.now().isoformat(timespec='seconds'))
    row['session_id'] = session_id(thread_id)
    row['last_active'] = datetime.datetime.now().isoformat(timespec='seconds')
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
    p = transcript(thread_id)
    if os.path.exists(p):
        stamps.append(os.path.getmtime(p))
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
    sid = session_id(thread_id)
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
    touch(thread_id)
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


def close(thread_id, reason='idle'):
    name = tmux_name(thread_id)
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


def listing():
    """Every conversation we know of, newest activity first."""
    out = []
    for tid, row in load().items():
        out.append(dict(row, thread_id=tid, alive=alive(tid),
                        last_active_ts=last_active(tid),
                        idle_for=int(time.time() - last_active(tid)) if last_active(tid) else None))
    out.sort(key=lambda r: r['last_active_ts'], reverse=True)
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
        close(a.close, reason='manual')
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
