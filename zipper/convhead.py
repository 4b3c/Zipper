"""Delivering a message without a terminal.

The same job as `convcore.paste`, against Claude Code's headless protocol
instead of its TUI. Nothing here captures a pane, and nothing here guesses.

**Why this exists.** `paste()` drives a conversation by typing at a rendering of
one, and a rendering has no commit point: a redraw mid-frame and a real state
change look identical in a capture. That is one bug, and it has been fixed three
times in three shapes -- a window too short (2026-09-07), a check reading a line
the text was never on (2026-09-09), a box that filled and then emptied without
Claude ever seeing the message (2026-09-10) -- plus a fourth when the transcript
counter was made the sole witness and a tool result ticked it (2026-09-16). Each
fix made the reader sharper and left the evidence in the same place.

`claude -p --input-format stream-json --output-format stream-json` has the
commit points the screen lacks, and they are the exact four things `paste()`
infers:

    convcore.py                        here
    -----------------------------------------------------------------
    _wait_ready   prompt char drawn    the process accepts stdin
    _await_echo   box looks non-empty  the `user` event, echoed by
                                       --replay-user-messages
    _submit       box cleared twice    writing the line *is* the submit
    _await_recorded  transcript grew   the `result` event

So the two failure modes that cost four days are gone by construction, not by a
better heuristic. A message either produced a `user` echo and a `result`, or it
did not, and the difference is a field in a JSON object rather than the shape of
a terminal at a moment.

**One process per turn, not one per conversation.** A long-lived process with
stdin held open is the better production shape -- it is the honest analogue of
the pane, and it takes a mid-turn message the way the input box does. It also
needs a supervisor that outlives the caller, which is a separate problem. This
resumes by session id instead, which Claude Code supports directly (verified:
`--resume` recovered a prior turn's reply in a fresh process), and serializes
turns on a lock file so the case the pane handled by queueing is handled here by
waiting. See `deliver` for what that costs.

Nothing in this module is wired into `deliver`/`serve` yet. It is additive on
purpose: run `tests/headless.py` against it and `tests/delivery.py` against the
pane, and compare.
"""
import os, json, glob, time, fcntl, shutil, select, threading, subprocess, contextlib

from .core import *          # noqa: F401,F403 -- the shared vocabulary
from . import convcore

# `--verbose` is not optional. `--output-format stream-json` under `-p` refuses
# to start without it ("requires --verbose") -- it is how the CLI distinguishes
# the event stream from a summary, not a logging preference.
BASE_FLAGS = ['-p', '--verbose',
              '--input-format', 'stream-json',
              '--output-format', 'stream-json',
              '--replay-user-messages']

# How long to wait for the whole turn. A turn here does real work -- tools, file
# edits, a commit -- so this bounds a hang, not a normal reply. The pane path had
# no equivalent: a wedged TUI simply sat there.
TURN_TIMEOUT = float(os.environ.get('ZIPPER_HEAD_TIMEOUT', 900))


def _claude():
    c = shutil.which('claude') or os.path.expanduser('~/.local/bin/claude')
    if not os.path.exists(c):
        raise RuntimeError('claude not installed')
    return c


def _env(thread_id):
    """The environment a conversation runs in.

    `HOME` and `PATH` are both load-bearing and both have bitten this system
    before. A systemd unit starts with no `$HOME`, so git falls back to
    `root@<hostname>` and commits land under the wrong author; and `claude`
    lives in `~/.local/bin`, which systemd's default `PATH` omits -- the same
    trap that once left the terminal card claiming ttyd was not installed.
    """
    env = dict(os.environ)
    env['ZIPPER_DISCORD_THREAD'] = str(thread_id)
    env['ZIPPER_VAULT'] = VAULT
    env.setdefault('HOME', '/root')
    env['PATH'] = '%s/.local/bin:/usr/local/bin:%s' % (env['HOME'], env.get('PATH', ''))
    return env


def transcript(sid):
    """Where Claude Code put this session's transcript, found rather than derived.

    **Do not rebuild the project directory name.** Claude Code names it after
    the working directory with the separators replaced, and `convcore` encodes
    that as `path.replace('/', '-')` -- which is incomplete: an underscore
    becomes a dash too. `/opt/vault` has neither, so production never noticed,
    but any path with an `_` in it resolves to a directory that does not exist.
    That is what made the first run of `tests/headless.py` report a delivered
    message as unrecorded: `mkdtemp` had handed it `/tmp/zipper-selftest-5o2853l_`,
    and the transcript was in `...-5o2853l-`. It also makes `tests/delivery.py`
    intermittently wrong, depending on the random suffix it happens to draw.

    Globbing for the session id sidesteps the encoding entirely. The id is a
    uuid5 and the filename is `<id>.jsonl`, so the match is unambiguous wherever
    Claude chose to write it -- and it stays correct if that naming scheme ever
    changes again, which guessing at the mangling does not.
    """
    hits = glob.glob(os.path.join(convcore.CLAUDE_PROJECTS, '*', sid + '.jsonl'))
    return hits[0] if hits else ''


def _resume_flags(thread_id):
    """`--session-id` assigns an id; `--resume` takes one back up.

    They are not interchangeable -- handing `--session-id` an id Claude already
    knows fails outright ("Session ID ... is already in use"), which is how the
    incomplete path encoding above surfaced: the transcript looked absent, so
    every turn after the first tried to create a session that existed. So an
    existing transcript decides which flag this is, same rule as
    `convcore.start`, and the transcript is now located by id rather than by
    reconstructing a directory name.
    """
    sid = (convcore.load().get(str(thread_id)) or {}).get('session_id') \
        or convcore.session_id(thread_id)
    if transcript(sid):
        return ['--resume', sid], sid, True
    return ['--session-id', sid], sid, False


def _lock_path(thread_id):
    return os.path.join(INBOX, 'head-%s.lock' % thread_id)


@contextlib.contextmanager
def _turn_lock(thread_id, timeout=TURN_TIMEOUT):
    """One turn at a time per conversation. A message arriving mid-turn waits.

    Two `--resume` processes against one session id would interleave writes to
    the same transcript, so turns have to serialize somewhere. They serialize
    here, and the second caller blocks until the first finishes.

    **Waiting is the intended behaviour, not a limitation to route around.**
    The pane let a mid-turn paste into the input box, so Claude could see it
    within the turn it was already running; that is the one thing the box did
    that this does not, and it was considered and dropped on 2026-09-17 --
    a follow-up should land after the current turn rather than steer it. So
    there is deliberately no interrupt and no mid-turn injection path: a
    message is queued, in arrival order, and delivered when the conversation
    is next free. `deliver` reports the wait as `queued_for` so a caller can
    say "queued behind a running turn" instead of going quiet.

    What this must never do is lose the message or report it delivered while it
    is still waiting -- see `tests/headless.py:case_busy`, which holds a
    conversation in a long tool-using turn and then sends into it.
    """
    os.makedirs(INBOX, exist_ok=True)
    fh = open(_lock_path(thread_id), 'a+')
    waited, end = 0.0, time.time() + timeout
    try:
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.time() >= end:
                    raise TimeoutError('another turn is still running')
                time.sleep(0.25)
                waited += 0.25
        yield waited
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def _message(text):
    """One line of stream-json: a user turn.

    Newline-delimited, so the text is JSON-escaped rather than typed. This is
    what removes the reason bracketed paste existed: a multi-line message
    cannot submit a fragment, because no newline in it is ever a keystroke.
    """
    return json.dumps({'type': 'user',
                       'message': {'role': 'user',
                                   'content': [{'type': 'text', 'text': text}]}}) + '\n'


def _events(proc, timeout):
    """Yield parsed stdout events until the process ends or the clock runs out.

    Unparseable lines are yielded as `None` rather than dropped -- a caller
    deciding "nothing arrived" needs to know the difference between silence and
    output it could not read. The distinction between those two is precisely
    what the pane could never make.
    """
    end = time.time() + timeout
    buf = b''
    while True:
        if time.time() >= end:
            return
        if not select.select([proc.stdout], [], [], 0.5)[0]:
            if proc.poll() is not None:
                break
            continue
        chunk = os.read(proc.stdout.fileno(), 65536)
        if not chunk:
            break
        buf += chunk
        while b'\n' in buf:
            raw, buf = buf.split(b'\n', 1)
            if not raw.strip():
                continue
            try:
                yield json.loads(raw.decode('utf-8', 'replace'))
            except ValueError:
                yield None


def _run_turn(thread_id, text, timeout, out, on_echo=None):
    """Hand the message over and read the turn to its end. Fills `out` in place.

    Separated from `deliver` so the two questions can be answered at different
    times -- see there.
    """
    timeout = TURN_TIMEOUT if timeout is None else timeout
    flags, sid, resumed = _resume_flags(thread_id)
    out.update(session_id=sid, resumed=resumed)

    mode = os.environ.get('ZIPPER_PERMISSION_MODE', 'auto')
    cmd = [_claude()] + BASE_FLAGS + flags + ['--permission-mode', mode]

    try:
        with _turn_lock(thread_id, timeout) as waited:
            out['queued_for'] = waited
            proc = subprocess.Popen(
                cmd, cwd=VAULT, env=_env(thread_id),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                # Its own session, so the turn is not a child that dies with
                # `systemctl restart zipper-web`. The pane got this from tmux
                # plus `KillMode=process`; a subprocess has to ask. The reply
                # does not depend on us surviving either way -- the Stop hook
                # runs inside this process and forwards it.
                start_new_session=True)
            try:
                # No readiness probe. The pane needed one because a paste
                # before the TUI was listening was dropped silently; a pipe
                # buffers, so the write cannot be early.
                proc.stdin.write(_message(text).encode('utf-8'))
                proc.stdin.flush()
                proc.stdin.close()          # this turn is the whole conversation

                chunks = []
                for ev in _events(proc, timeout):
                    if ev is None:
                        continue
                    kind = ev.get('type')
                    if kind == 'user':
                        out['echoed'] = True
                        if on_echo:
                            on_echo()   # releases a `wait='echo'` caller
                    elif kind == 'assistant':
                        for b in (ev.get('message') or {}).get('content') or []:
                            if isinstance(b, dict) and b.get('type') == 'text':
                                chunks.append(b.get('text') or '')
                    elif kind == 'result':
                        out['recorded'] = ev.get('subtype') == 'success'
                        if ev.get('subtype') != 'success':
                            out['error'] = str(ev.get('subtype') or 'result not success')
                        if ev.get('result'):
                            chunks = [str(ev['result'])]
                        # The session id the CLI actually used. On a first run
                        # this confirms the derived id took; on a resume it
                        # catches a fork we did not ask for.
                        if ev.get('session_id'):
                            out['session_id'] = ev['session_id']
                out['reply'] = ''.join(chunks).strip()
                proc.wait(timeout=10)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
                err = (proc.stderr.read() or b'').decode('utf-8', 'replace').strip()
                for h in (proc.stdout, proc.stderr):
                    with contextlib.suppress(OSError):
                        h.close()
    except (TimeoutError, RuntimeError) as e:
        out['error'] = str(e)
        return out
    except OSError as e:
        out['error'] = 'could not start claude: %s' % e
        return out

    if out['echoed'] and out['recorded']:
        out['turn_ok'] = True
        convcore.touch(thread_id, active=True, session_id=out['session_id'])
    elif not out['echoed']:
        out['error'] = out['error'] or (
            'the session never acknowledged the message%s'
            % (' -- %s' % err[:300] if err else ''))
    elif not out['recorded']:
        out['error'] = out['error'] or (
            'acknowledged but the turn did not complete%s'
            % (' -- %s' % err[:300] if err else ''))
    return out


# How long to wait for the session to acknowledge a message, in `wait='echo'`.
# Bounds the handover only; the turn behind it can run for as long as it likes.
ECHO_TIMEOUT = float(os.environ.get('ZIPPER_HEAD_ECHO_TIMEOUT', 120))


def deliver(thread_id, text, wait='turn', timeout=None):
    """Send `text` to a conversation. `wait` decides which question is answered.

    Returns the `ok`/`error` shape `convcore.paste` returns, so the two can be
    swapped at a call site, plus what the pane path could not report:

        echoed      the session acknowledged the message (`user` event)
        recorded    the turn ran to completion (`result` event)
        reply       the assistant text, which `paste` never saw at all
        queued_for  seconds spent waiting on a turn already in flight

    **The two witnesses answer different questions, and callers want different
    ones.** `echoed` is delivery: Claude Code handing back the message it
    accepted. `recorded` is completion, which can be an hour later. Treating
    them as one flag was a mistake in the first draft of this module -- it made
    `ok` mean "the turn finished", which is not what any caller of
    `convcore.deliver` has ever waited for.

    `wait='echo'` -- **the Discord door.** Returns as soon as the message is
    acknowledged; the turn continues in a daemon thread. This is the contract
    `bot/client.py` already documents ("this request waits on delivery, not on
    the answer") and enforces with `POST_TIMEOUT = 300`. Blocking for the whole
    turn instead would post a false "Zipper hasn't answered in 300s" into the
    thread on any turn longer than five minutes, while the turn was in fact
    running fine. The reply comes back the way it always has, through the Stop
    hook -- verified to fire under `-p` -- so nothing here posts it, and
    nothing here may, or the thread gets it twice.

    `wait='turn'` -- runs to completion and reports `reply`. What the tests use,
    and the default, because a caller that wants a reply should have to say so
    rather than get a silently truncated one.

    On `wait='echo'`, `ok` reflects the handover alone and `recorded`/`reply`
    are necessarily still false/empty when this returns -- they belong to a turn
    that has not finished. Do not read them in that mode.
    """
    out = {'ok': False, 'error': '', 'echoed': False, 'recorded': False,
           'turn_ok': False, 'reply': '', 'session_id': '', 'resumed': False,
           'queued_for': 0.0}
    echoed, done = threading.Event(), threading.Event()

    def run():
        try:
            _run_turn(thread_id, text, timeout, out, on_echo=echoed.set)
        except Exception as e:                      # a thread dying silently is
            out['error'] = out['error'] or repr(e)  # how a message goes missing
        finally:
            echoed.set()                            # never leave a caller hanging
            done.set()

    t = threading.Thread(target=run, name='head-%s' % thread_id, daemon=True)
    t.start()

    if wait == 'turn':
        done.wait(timeout if timeout is not None else TURN_TIMEOUT)
        out['ok'] = out['turn_ok']
        if not done.is_set() and not out['error']:
            out['error'] = 'the turn did not finish in time'
        return out

    # wait='echo': the handover, bounded independently of the turn.
    echoed.wait(min(ECHO_TIMEOUT, timeout or ECHO_TIMEOUT))
    out['ok'] = out['echoed']
    if not out['ok'] and not out['error']:
        out['error'] = 'the session did not acknowledge the message in %gs' % ECHO_TIMEOUT
    return out
