"""A conversation: its identity, its tmux session, and getting a message into it.

The registry (`Inbox/conversations.json`), the derived session ids, the pane
naming rules, and start/paste/deliver. Nothing here knows about ttyd or about
whether a conversation looks busy -- those are `zipper.ttyd` and
`zipper.convstate`, which both build on this. Keeping the dependency one-way is
what lets this file be read on its own.

Split out of `zipper/conversations.py` on 2026-09-07 (857 lines).
Import it as `zipper.conversations`, which re-exports all three.
"""
import os, re, json, time, uuid, fcntl, shutil, signal, hashlib, subprocess, datetime
import contextlib

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

    Normally derived, like the session id. The exception is a *bound* thread: an
    already-running conversation adopted by a thread so it can be carried on
    from a phone. Its pane is not ours to name, so the registry records the real
    one. Every conversation starts with a thread, so binding is only ever the
    adoption case.
    """
    row = load().get(str(thread_id)) or {}
    return row.get('tmux') or 'zipper-%s' % thread_id


def target(thread_id):
    """This thread's session as an **exact** tmux `-t` target.

    tmux resolves a bare `-t` by prefix, so any session name that is a prefix of
    another matches both. Every `-t` here is therefore anchored, not just the
    liveness check: unanchored, `close()` killed a live conversation and
    `deliver()` pasted into one.

    The case that produced those bugs is gone -- a fixed session named `zipper`
    alongside every `zipper-<thread>` -- but a bound row still carries a name
    this module did not choose, so the anchoring stays.

    The trailing colon matters: `=name` is a *session* target, and the commands
    that actually carry a message -- `capture-pane`, `send-keys`,
    `paste-buffer` -- want a **pane** target and refuse it ("can't find pane").
    `=name:` is a pane target with the session part still exact, and every
    session-target command takes it as well, so one form covers all of them.

    Only for `-t` -- the `-t` flags passed to ttyd are its own option, not
    tmux's.
    """
    return '=%s:' % tmux_name(thread_id)


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

    The `=` prefix makes the target an **exact** name, not a prefix -- see
    `target()`. Read a bound row's liveness off the wrong pane and it reports as
    live forever; pinned against the reaper, it then blocked `zipper commit` on
    every pass, which is what a hand-bound `zipper` row did on 2026-09-06.
    """
    try:
        return subprocess.run([_tmux(), 'has-session', '-t', target(thread_id)],
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


CONV_LOCK = CONV_JSON + '.lock'


@contextlib.contextmanager
def mutate():
    """Read-modify-write the registry with nobody else in the middle.

    **Every writer must go through this.** `load()` + edit + `save()` is a
    read-modify-write across *processes* -- the hook, the web server, the CLI
    and every conversation's own Claude all hold this file -- and the write is
    a whole-file replace. Two overlapping writers means the slower one saves a
    dict it read before the faster one's change and puts the file back the way
    it was.

    That is not a theoretical race. It ate replies: `note_delivery` records the
    Discord message under `last_delivered`, and the Stop hook forwards only if
    the transcript's last user message matches it. A second conversation
    touching the registry during the first one's turn -- caching a title,
    remembering a ttyd port, marking itself active -- restored the *previous*
    `last_delivered`, the hook compared against a stale key, decided the message
    had been typed at the keyboard, and dropped the reply on the floor. Long
    turns lost more often because the window is the whole turn.

    An advisory flock on a sidecar file, so an interrupted holder releases it.
    """
    os.makedirs(os.path.dirname(CONV_JSON), exist_ok=True)
    with open(CONV_LOCK, 'a+') as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            d = load()
            yield d
            save(d)
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def touch(thread_id, active=False, **fields):
    """Record something about a conversation.

    `active` is what moves it up the list, and only a message does that. Every
    incidental write used to bump it -- caching a title, remembering a port,
    opening the thing to read it -- so merely looking at a conversation sent it
    to the top, and the order the operator was navigating by rearranged itself
    under his cursor.
    """
    with mutate() as d:
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
    return row




def _pane(thread_id):
    try:
        r = subprocess.run([_tmux(), 'capture-pane', '-p', '-t', target(thread_id)],
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

    **Auto permission mode.** A conversation Zipper starts is usually one nobody
    is watching: reached from a phone, or resumed to answer a message. In manual
    mode the first tool call stops it dead behind a prompt only someone at the
    keyboard can clear, and from Discord that looks exactly like Zipper having
    gone quiet. `ZIPPER_PERMISSION_MODE` overrides it.
    """
    name = tmux_name(thread_id)
    row = load().get(str(thread_id)) or {}
    sid = row.get('session_id') or session_id(thread_id)
    resumed = os.path.exists(transcript(thread_id))
    flag = ['--resume', sid] if resumed else ['--session-id', sid]
    mode = os.environ.get('ZIPPER_PERMISSION_MODE', 'auto')
    inner = ' '.join(['exec', 'claude'] + flag + ['--permission-mode', mode])
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
    out = {'ok': True, 'resumed': resumed, 'session_id': sid, 'tmux': name}
    if prompt:
        # The paste result is the answer to "did the message arrive", which is
        # the whole point of the call. Discarding it meant a cold start that
        # left the text sitting unsent still reported ok, so nothing upstream --
        # the bot, the typing indicator, the caller -- had any way to know.
        r = paste(thread_id, prompt)
        if not r.get('ok'):
            out.update(ok=False, error=r.get('error') or 'paste failed')
    return out


def _input_line(pane):
    """What is currently typed but unsent, or None if the box isn't on screen.

    The input box is the last line starting with the prompt character. Reading
    "everything after the last ❯" instead swept up the footer, which is fine
    until the footer is the only thing that redrew.
    """
    for line in reversed((pane or '').splitlines()):
        s = line.lstrip()
        if s.startswith('❯'):
            return s[1:].strip()
    return None


def _submit(thread_id, tgt, text, timeout=20.0):
    """Press Enter until the message actually leaves the input box.

    Two things make this harder than one keystroke:

    **A cold Claude draws the prompt before it will accept a submit.** So
    `_wait_ready` returns, the first Enters go nowhere, and the message sits in
    the box looking delivered -- the only failure here invisible from outside.
    On 2026-09-07 a new conversation from Discord did exactly that, and a single
    Enter by hand a minute later submitted it instantly: the keystroke was always
    right, the window (six tries at 0.6s) was too short for a first start.

    **A single frame is not evidence.** The TUI redraws several times a second,
    so a capture can land mid-redraw with the input line blank. Believing one
    such frame is what made the first fix report success over an unsent message.
    Clearing has to be seen twice in a row, and a frame with no input box at all
    counts as neither.
    """
    probe = (text.strip().splitlines() or [''])[0][:40]
    clear = 0
    end = time.time() + timeout
    while time.time() < end:
        time.sleep(0.5)
        line = _input_line(_pane(thread_id))
        if line is None:
            continue                       # mid-redraw: no evidence either way
        if probe not in line:
            clear += 1
            if clear >= 2:
                return True
        else:
            clear = 0
            subprocess.run([_tmux(), 'send-keys', '-t', tgt, 'Enter'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return False


def paste(thread_id, text):
    """Type a block into a conversation's pane.

    Bracketed paste, then a separate Enter -- as keystrokes every newline in a
    multi-line message would submit a fragment.
    """
    tgt = target(thread_id)
    if not alive(thread_id):
        return {'ok': False, 'error': 'conversation not running'}
    buf = 'zipper-%s' % thread_id
    try:
        subprocess.run([_tmux(), 'load-buffer', '-b', buf, '-'],
                       input=text.encode('utf-8'), check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run([_tmux(), 'paste-buffer', '-b', buf, '-t', tgt, '-p', '-d'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.4)
        subprocess.run([_tmux(), 'send-keys', '-t', tgt, 'Enter'], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # An Enter that arrives while the TUI is still settling is swallowed,
        # and the message then sits in the input box looking delivered -- the
        # one failure here that is invisible from outside. Check that the text
        # actually left the box, and press again if it did not.
        #
        # **Keep pressing for 20 seconds, not 4.** A *cold* Claude draws the
        # prompt character well before it will accept a submit, so `_wait_ready`
        # returns and every Enter for the next several seconds goes nowhere. Six
        # tries at 0.6s covered a resume and not a first start: on 2026-09-07 a
        # new conversation from Discord sat with "Test" in its box, and a single
        # Enter by hand a minute later submitted it instantly -- the keystroke
        # was always right, the window was too short.
        if not _submit(thread_id, tgt, text):
            return {'ok': False, 'error': 'message stayed in the input box'}
    except Exception as e:
        return {'ok': False, 'error': str(e)}
    touch(thread_id, active=True)
    return {'ok': True}


def delivery_key(text):
    """A stable fingerprint of a message, for matching it later.

    Whitespace-collapsed because the same text does not survive the trip
    byte-identical: it is pasted into a terminal at one end and read back out
    of a JSONL transcript at the other, and line wrapping is not information.
    """
    return hashlib.sha1(' '.join((text or '').split()).encode('utf-8')).hexdigest()


def note_delivery(thread_id, text):
    """Remember that *this* text reached the conversation from Discord.

    **This is the only record of where a message came from, and it lives here
    rather than in the message.** Replies are forwarded automatically, so the
    session never needs to know its own provenance -- and a tag in the prompt
    would be a fact about one past turn sitting in the context window forever,
    misrouting every later turn. (It did: HISTORY.md, 2026-09-06.)

    Something still has to know, because the terminal is the other input and it
    produces no event anyone can observe: Abram typing into the pane is invisible
    to the bot, to this process, and to systemd. So provenance is recorded at the
    one moment it is unambiguous -- delivery -- and the Stop hook answers "did
    this turn come from Discord?" by comparing the transcript's last user message
    against this. A match means the bot put it there; anything else means he
    typed it.

    **It is a set, not a slot.** A single `last_delivered` assumed one message
    in flight at a time, and a conversation does not work that way: he sends a
    follow-up while a long turn is still running, delivery overwrites the slot,
    and when the *first* turn ends the hook compares its prompt against the
    *second* message's key, decides it was typed, and drops the reply. That is
    exactly what happened on 2026-09-08 -- an eight-minute bookkeeping pass
    answered into a terminal nobody was reading while he waited on Discord.

    So every delivery is remembered, not just the newest. The list is bounded
    two ways, because an unbounded provenance log is its own bug: `KEEP` entries,
    and `TTL` seconds. Both exist to stop a key outliving the conversation it
    describes -- a message from this morning still matching at midnight would
    forward a reply to something he typed at the keyboard hours later.
    """
    now = datetime.datetime.now()
    with mutate() as d:
        row = d.setdefault(str(thread_id), {})
        rows = [e for e in (row.get('deliveries') or [])
                if isinstance(e, dict) and e.get('key')]
        rows.append({'key': delivery_key(text),
                     'at': now.isoformat(timespec='seconds')})
        row['deliveries'] = _fresh_deliveries(rows, now)
        # Kept in step for anything still reading the old field -- the dashboard
        # shows it, and a rollback should not lose today's provenance.
        row['last_delivered'] = row['deliveries'][-1]


DELIVERY_KEEP = 12
DELIVERY_TTL = 6 * 3600


def _fresh_deliveries(rows, now):
    """The last `DELIVERY_KEEP` deliveries that are younger than `DELIVERY_TTL`."""
    out = []
    for e in rows:
        try:
            age = (now - datetime.datetime.fromisoformat(e['at'])).total_seconds()
        except (KeyError, TypeError, ValueError):
            age = 0                    # unparseable: keep, let the count bound it
        if age <= DELIVERY_TTL:
            out.append(e)
    return out[-DELIVERY_KEEP:]


def delivered(thread_id, text):
    """Was `text` a message this conversation was handed from Discord?

    Any live delivery, not only the most recent one -- see `note_delivery`. The
    legacy single slot is still read so a registry written by an older version
    keeps routing correctly through the upgrade.
    """
    row = load().get(str(thread_id)) or {}
    key = delivery_key(text)
    now = datetime.datetime.now()
    for e in _fresh_deliveries([e for e in (row.get('deliveries') or [])
                                if isinstance(e, dict) and e.get('key')], now):
        if e['key'] == key:
            return True
    d = row.get('last_delivered') or {}
    return bool(d.get('key')) and d['key'] == key


def deliver(thread_id, text, source='discord'):
    """The whole Discord path in one call: start or resume, then hand it over."""
    note_delivery(thread_id, text)
    if alive(thread_id):
        res = paste(thread_id, text)
        if res['ok']:
            return dict(res, state='live')
        return res
    r = start(thread_id, prompt=text)
    return dict(r, state='resumed' if r.get('resumed') else 'new')
