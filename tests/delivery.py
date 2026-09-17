#!/usr/bin/env python3
"""Does a message actually reach a conversation?

    python3 tests/delivery.py

**The one path in this system whose failure is silent.** A lost message produces
no error, no log line and no missing file -- just a thread that never answers.
Every other bug here announces itself; this one is indistinguishable from Claude
taking a while. So it is the one path that cannot be checked by reading the code,
and until this file existed it never was: a refactor on 2026-09-16 was verified
by importing the module and running `zipper conversations`, neither of which
delivers anything, and it silently ate three messages in production.

What it asserts
---------------
Delivery is measured the only honest way -- `convcore._user_rows`, the count of
user messages in the session's *own* transcript. Claude's record that it
received something is a different kind of fact from a screen capture, and the
three bugs in this path (2026-09-07, -09, -10) were all cases of the screen
saying yes while the transcript said nothing.

The negative case matters as much as the positive ones. A delivery path that
cannot fail is not passing, it is lying, and "reported ok over a message that
never arrived" is the exact shape of every bug this file exists to catch.

Safety
------
Nothing here touches a real conversation.

- **Thread ids are `local-selftest-*`.** The Stop hook skips the `local-` prefix
  (`hooks/forward_reply.py:230`), so a reply from a test conversation is never
  posted to Discord. This is the existing fallback namespace, not a new one.
- **The working directory is a sandbox**, not the vault. `convcore.VAULT` is
  repointed for this process only, which moves both the session's cwd and its
  transcript directory, so a test conversation cannot edit notes and its
  transcripts do not land among the real ones.
- **Sessions are closed and their registry rows deleted** at the end, pass or
  fail, so `zipper conversations` is left exactly as it was found.

It does start real `claude` processes and does spend tokens -- a few hundred per
case. That is the price of testing delivery rather than testing a mock of it.
"""
import os, sys, time, shutil, tempfile, subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zipper import convcore, convstate                         # noqa: E402

# Short, and asks for nothing. A test conversation that is given work to do will
# do it, and an agent improvising inside a sandbox is noise in the result.
PING = 'Reply with the single word OK. Do nothing else.'

PASS, FAIL = [], []


def check(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print('  %s  %s%s' % ('PASS' if ok else 'FAIL', name,
                          '  -- %s' % detail if detail else ''))
    return ok


def sandbox():
    """A working directory that is not the vault, with a CLAUDE.md that says so."""
    d = tempfile.mkdtemp(prefix='zipper-selftest-')
    with open(os.path.join(d, 'CLAUDE.md'), 'w') as fh:
        # "Take no other action" is wrong here, and was: it made the test agent
        # politely decline the shell commands `case_busy` uses to make a session
        # busy, so that case ran against an idle session and proved nothing. The
        # instruction has to permit the workload while still keeping the agent
        # from wandering -- there is nothing in this directory to wander into,
        # which is the real containment.
        fh.write('# Test sandbox\n\n'
                 'This is an automated delivery self-test, not a real task.\n\n'
                 '- Do exactly what each message asks, and nothing beyond it.\n'
                 '- If asked to run shell commands, run them.\n'
                 '- Keep replies to a few words.\n'
                 '- Do not create, edit or delete files.\n')
    return d


def prime_trust(tid, sbox):
    """Get the sandbox past Claude's "do you trust this folder?" dialog.

    A directory Claude has never seen opens with that prompt instead of an input
    box, and a paste lands in the dialog rather than the conversation. That is a
    fact about new directories, not about delivery -- the vault was trusted long
    ago -- so it is dealt with here rather than being allowed to fail a case and
    read as a delivery bug. (It did, on the first run of this file.)

    Claude records the answer itself, so the test cases that follow start from
    the same state a real conversation does. Answering by keystroke rather than
    by editing `~/.claude.json` is deliberate: that file is shared with every
    other session on this box, and a read-modify-write of it from here could
    drop somebody else's concurrent change.
    """
    convcore.start(tid)
    # Generously long. A cold `claude` on a loaded box can take well over the
    # 30s this used to allow, and giving up early is silent sabotage: the dialog
    # stays on screen, every case below pastes into it, and the run reports eight
    # unrelated delivery failures against code that is fine. Better to wait.
    ok = False
    end = time.time() + 90
    while time.time() < end:
        pane = convcore._pane(tid)
        if 'trust this folder' in pane:
            subprocess.run([convcore._tmux(), 'send-keys', '-t',
                            convcore.target(tid), 'Enter'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(3)
            ok = 'trust this folder' not in convcore._pane(tid)
            break
        if convcore._input_line(pane) is not None:
            ok = True                  # no dialog: already trusted
            break
        time.sleep(0.5)
    convstate.close(tid, reason='selftest', force=True)
    time.sleep(1)
    # The priming session wrote a transcript; delete it so `_user_rows` starts
    # from zero and the cold-start case is genuinely cold.
    try:
        os.remove(convcore.transcript(tid))
    except OSError:
        pass
    print('  trust:   %s' % ('sandbox primed' if ok else 'PRIMING FAILED'))
    return ok


def forget_sandbox(sbox):
    """Drop the sandbox's entry from Claude's project list.

    Only our own key is touched, and the file is re-read immediately before
    writing, so a concurrent session's changes are preserved rather than
    overwritten by a dict read seconds earlier.
    """
    import json
    p = os.path.expanduser('~/.claude.json')
    try:
        with open(p) as fh:
            blob = json.load(fh)
        if (blob.get('projects') or {}).pop(sbox, None) is None:
            return
        tmp = p + '.selftest.tmp'
        with open(tmp, 'w') as fh:
            json.dump(blob, fh, indent=2)
        os.replace(tmp, p)
    except Exception as e:
        print('  note: could not drop sandbox from ~/.claude.json: %s' % e)


def delivered(tid, before, label):
    """Did the session record a new user message? The only success condition."""
    after = convcore._user_rows(tid)
    return check(label, after > before, 'user rows %d -> %d' % (before, after))


def case_cold_start(tid):
    """No session yet: start one and hand it the message. The slowest path, and
    the one the 2026-09-07 bug lived in -- a cold Claude draws its prompt well
    before it will accept a submit."""
    print('\ncold start')
    if convcore.alive(tid):
        convstate.close(tid, reason='selftest', force=True)
        time.sleep(1)
    before = convcore._user_rows(tid)
    r = convcore.deliver(tid, PING)
    check('cold start reports ok', r.get('ok'), r.get('error', ''))
    return delivered(tid, before, 'cold start delivered')


def case_warm(tid):
    """A session already running: the common case, and the fast one."""
    print('\nwarm session')
    if not convcore.alive(tid):
        return check('warm session delivered', False, 'no live session to test')
    before = convcore._user_rows(tid)
    r = convcore.paste(tid, PING)
    check('warm reports ok', r.get('ok'), r.get('error', ''))
    return delivered(tid, before, 'warm session delivered')


def case_resume(tid):
    """Closed, then spoken to again. Distinct from a cold start: the transcript
    exists, so this is `--resume` rather than `--session-id`, and passing the
    wrong one of those is an error rather than a retry."""
    print('\nresume after close')
    convstate.close(tid, reason='selftest', force=True)
    time.sleep(2)
    check('closed cleanly', not convcore.alive(tid))
    before = convcore._user_rows(tid)
    r = convcore.deliver(tid, PING)
    check('resume reports ok', r.get('ok'), r.get('error', ''))
    check('reported as resumed', r.get('resumed') or r.get('state') == 'resumed',
          'state=%r' % r.get('state'))
    return delivered(tid, before, 'resume delivered')


def case_busy(tid):
    """**A message arriving while the session is mid-turn.**

    The condition every case above quietly avoids, and the one production was
    actually in when three messages were lost on 2026-09-16: each stuck pane had
    a long turn above the input box (`✻ Churned for 45s`). A busy TUI is a
    different machine from an idle one -- it is redrawing a spinner several times
    a second, the input box is present but the app is not reading it the same
    way, and a pane capture is far likelier to land on a frame that does not mean
    what it looks like.

    This is also the ordinary case, not an exotic one: he sends a follow-up while
    a long turn is still running, which is exactly what `note_delivery` was
    rewritten for.
    """
    print('\nbusy session (message arrives mid-turn)')
    if not convcore.alive(tid):
        convcore.start(tid)
    # A *stream* of tool results, not one at the end. Claude Code writes every
    # tool result as a `type: "user"` row, so this is the condition under which
    # a delivery check that counts those rows naively will report success within
    # half a second of a paste it never confirmed -- which is what lost two
    # messages on 2026-09-16. One slow `sleep` does not reproduce it; twelve
    # quick commands land results continuously, right across the paste.
    convcore.paste(tid, 'Using Bash, run each of these as a separate command, '
                        'one at a time: `sleep 3 && echo 1`, `sleep 3 && echo 2`, '
                        '`sleep 3 && echo 3`, `sleep 3 && echo 4`, `sleep 3 && echo 5`, '
                        '`sleep 3 && echo 6`, `sleep 3 && echo 7`, `sleep 3 && echo 8`, '
                        '`sleep 3 && echo 9`, `sleep 3 && echo 10`. Then reply DONE.')
    end = time.time() + 30
    while time.time() < end and convstate.state(tid) != 'working':
        time.sleep(0.5)
    if not check('session is busy', convstate.state(tid) == 'working',
                 'state=%r' % convstate.state(tid)):
        return False
    before = convcore._user_rows(tid)
    r = convcore.paste(tid, PING)
    check('busy delivery reports ok', r.get('ok'), r.get('error', ''))
    return delivered(tid, before, 'busy session delivered')


def case_undeliverable(tid):
    """**The negative case.** A tmux session with no Claude in it.

    The pane exists, the target resolves, `load-buffer` and `paste-buffer` both
    succeed -- everything a screen-based check looks at goes right, and nothing
    is ever delivered, because the text lands in a shell. `paste()` must return
    not-ok here.

    This is the regression test for 2026-09-16. The rewrite that ate three
    messages would pass every case above and fail this one: it trusted a pane
    capture to decide the message had arrived, and a pane can show text that is
    not in any input buffer.
    """
    print('\nundeliverable (session with no claude)')
    name = convcore.tmux_name(tid)
    subprocess.run([convcore._tmux(), 'kill-session', '-t', convcore.target(tid)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run([convcore._tmux(), 'new-session', '-d', '-s', name, 'bash'],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)
    before = convcore._user_rows(tid)
    r = convcore.paste(tid, PING)
    ok = check('refuses to report success', not r.get('ok'),
               'returned %r' % (r,))
    after = convcore._user_rows(tid)
    ok = check('nothing was recorded', after == before,
               'user rows %d -> %d' % (before, after)) and ok
    return ok


def cleanup(tid, sbox):
    print('\ncleanup')
    try:
        convstate.close(tid, reason='selftest', force=True)
    except Exception as e:
        print('  close failed: %s' % e)
    subprocess.run([convcore._tmux(), 'kill-session', '-t', convcore.target(tid)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # The registry is what `zipper conversations` and the dashboard list, so a
    # test row left behind is a fake conversation in his chat list forever.
    try:
        with convcore.mutate() as d:
            d.pop(str(tid), None)
    except Exception as e:
        print('  registry cleanup failed: %s' % e)
    forget_sandbox(sbox)
    shutil.rmtree(sbox, ignore_errors=True)
    print('  session killed, registry row removed, sandbox deleted')


def main():
    tid = 'local-selftest-%d' % os.getpid()
    sbox = sandbox()
    # Repoint the vault for this process only. `_project_dir` derives the
    # transcript directory from it too, so the sandbox catches both the cwd the
    # session runs in and the transcripts it writes.
    convcore.VAULT = sbox
    convstate.VAULT = sbox

    print('delivery self-test')
    print('  thread:  %s  (local- prefix: the Stop hook will not post to Discord)' % tid)
    print('  sandbox: %s' % sbox)
    try:
        if not prime_trust(tid, sbox):
            # Everything below would paste into the trust dialog and report
            # delivery failures that say nothing about delivery.
            print('\nABORTED: could not get the sandbox past the trust dialog.')
            print('This is a harness problem, not a delivery problem.')
            FAIL.append('trust priming')
            raise SystemExit(1)
        case_cold_start(tid)
        case_warm(tid)
        case_resume(tid)
        case_busy(tid)
        case_undeliverable(tid)
    except KeyboardInterrupt:
        print('\ninterrupted')
    finally:
        cleanup(tid, sbox)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    for f in FAIL:
        print('  failed: %s' % f)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
