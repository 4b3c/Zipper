#!/usr/bin/env python3
"""Does a message reach a conversation without a terminal?

    python3 tests/headless.py

The counterpart to `tests/delivery.py`. Same question, same safety model, same
witness -- the other delivery path. Run both and compare; that comparison is the
point, and it is the only way to know whether `zipper.convhead` is actually an
improvement rather than a differently-shaped guess.

What it asserts
---------------
Every case checks delivery **twice, from two different kinds of evidence**:

  - what the protocol claims (`echoed` and `recorded` from the event stream)
  - what the session wrote down (`convcore._user_rows`, the transcript)

Checking both is not redundancy. The pane path's whole history is the screen
saying yes while the transcript said nothing, and a protocol that agreed with
itself would reproduce that failure in a new dialect. If these two ever
disagree, the protocol is lying and this file is the thing that notices.
`_user_rows` is the same witness `tests/delivery.py` uses, deliberately: it
already knows to skip tool results and sidechains, which is what made it wrong
once (2026-09-16) and correct now.

The negative case matters as much as the positive ones. A delivery path that
cannot fail is not passing, it is lying.

What does and does not correspond
---------------------------------
**Do not read the two files as the same suite with a different backend.** Only
two cases are genuinely comparable; the rest differ in what they assert, and
comparing pass counts across the files would be meaningless.

    delivery.py            headless.py           relationship
    ------------------------------------------------------------------------
    case_cold_start        case_cold             comparable. Also shows the
                                                 asymmetry: the pane needs
                                                 `prime_trust` for the "do you
                                                 trust this folder?" dialog,
                                                 `-p` skips it entirely
    case_resume            case_resume           comparable, and headless is
                                                 stricter -- it checks the
                                                 resumed session *remembers*
                                                 the prior turn, which the
                                                 pane case never asserts
    case_warm              --                    inapplicable. "Warm" is a
                                                 live pane to paste into;
                                                 here every turn is its own
                                                 process, so there is no warm
                                                 state to be in
    case_busy              case_busy             **opposite assertions.** The
                                                 pane must deliver mid-turn;
                                                 this must queue and land
                                                 after (decided 2026-09-17)
    case_undeliverable     case_silent_success   same shape, different
                                                 mechanism: a pane with no
                                                 Claude in it vs a process
                                                 that exits success having
                                                 delivered nothing
    --                     case_timeout          no counterpart; the pane
                                                 path has no turn timeout

Safety
------
Identical to `tests/delivery.py`, and it reuses that file's helpers rather than
re-implementing them:

- **Thread ids are `local-selftest-*`**, the namespace the Stop hook skips
  (`hooks/forward_reply.py`), so a test reply is never posted to Discord.
- **The working directory is a sandbox**, not the vault. `convcore.VAULT` and
  `convhead.VAULT` are both repointed for this process only, which moves the
  session's cwd *and* its transcript directory, so a test conversation cannot
  edit notes and its transcripts do not land among the real ones.
- **Registry rows and lock files are deleted** at the end, pass or fail.

It starts real `claude` processes and spends tokens -- a few hundred per case.
That is the price of testing delivery rather than a mock of it.
"""
import os, sys, time, shutil, threading, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))   # the repo, for `zipper`
sys.path.insert(0, HERE)                    # this directory, for `delivery`

from zipper import convcore, convhead                          # noqa: E402
import delivery as pane        # sandbox() / forget_sandbox()  # noqa: E402

PASS, FAIL = [], []
SBOX = ''               # set by main(); case_silent_success writes a stub into it


def check(name, ok, detail=''):
    (PASS if ok else FAIL).append(name)
    print('  %s  %s%s' % ('PASS' if ok else 'FAIL', name,
                          '  -- %s' % detail if detail else ''))
    return ok


def agree(tid, before, r, label):
    """The protocol and the transcript must tell the same story."""
    after = convcore._user_rows(tid)
    grew = after > before
    check('%s: reports ok' % label, r.get('ok'), r.get('error', ''))
    check('%s: session recorded it' % label, grew,
          'user rows %d -> %d' % (before, after))
    return check('%s: protocol agrees with transcript' % label,
                 bool(r.get('ok')) == grew,
                 'ok=%s rows_grew=%s' % (r.get('ok'), grew))


def case_cold(tid):
    """A conversation that has never spoken.

    Also the test that `-p` skips the trust dialog. `tests/delivery.py` needs a
    whole `prime_trust` step for this -- a fresh directory opens on "do you
    trust this folder?" instead of an input box, and a paste lands in the
    dialog. If this case passes in a brand-new sandbox with no priming, the
    headless path does not have that failure mode at all.
    """
    print('\ncold start (unprimed sandbox -- also tests the trust dialog)')
    before = convcore._user_rows(tid)
    r = convhead.deliver(tid, 'Reply with the single word OK. Do nothing else.')
    agree(tid, before, r, 'cold')
    check('cold: got a reply', bool(r.get('reply')), repr(r.get('reply'))[:60])
    check('cold: not reported as resumed', not r.get('resumed'))
    return r


def case_resume(tid):
    """A second process against the same session id must remember the first.

    This is the claim the whole design rests on. If resume does not carry
    context, a per-turn process is not a conversation and the pane has to stay.
    """
    print('\nresume (continuity across processes)')
    before = convcore._user_rows(tid)
    r = convhead.deliver(
        tid, 'What single word did you reply with a moment ago? Answer with just that word.')
    agree(tid, before, r, 'resume')
    check('resume: reported as resumed', r.get('resumed'))
    check('resume: remembered the prior turn', 'ok' in (r.get('reply') or '').lower(),
          repr(r.get('reply'))[:60])


def case_busy(tid):
    """**A message arriving while the conversation is mid-turn.**

    The counterpart to `delivery.py:case_busy`, and the case the two files
    assert *opposite* things about. There, a paste into a running turn had to be
    delivered mid-turn. Here it has to **wait** and then land -- mid-turn
    steering was dropped on 2026-09-17 -- so the requirement is queued, ordered,
    not lost, and never reported delivered while it is still waiting.

    The busy turn has to be a real one. `delivery.py` makes it a stream of ten
    sequential bash commands on purpose: Claude Code writes every tool result as
    a `type: "user"` row, and that continuous stream of rows is the condition
    under which a naive transcript check reports success within half a second of
    a paste it never confirmed -- the bug that lost three messages on
    2026-09-16. An earlier version of this case sent two one-word messages
    concurrently, which exercised the lock but produced no tool results at all,
    so the historically dangerous condition was never reached. Keep the tools.
    """
    print('\nbusy conversation (message arrives mid-turn -- must wait, then land)')
    before = convcore._user_rows(tid)
    out = {}

    def work():
        out['work'] = convhead.deliver(
            tid,
            'Using Bash, run each of these as a separate command, one at a time: '
            '`sleep 2 && echo 1`, `sleep 2 && echo 2`, `sleep 2 && echo 3`, '
            '`sleep 2 && echo 4`, `sleep 2 && echo 5`, `sleep 2 && echo 6`. '
            'Then reply DONE.')

    t = threading.Thread(target=work)
    t.start()

    # Wait until the turn is genuinely in flight -- the lock being held is the
    # honest signal, and it is the same fact `deliver` blocks on.
    held, end = False, time.time() + 60
    while time.time() < end:
        try:
            with convhead._turn_lock(tid, timeout=0.1):
                pass
        except TimeoutError:
            held = True
            break
        time.sleep(0.25)
    if not check('busy: the conversation is mid-turn', held):
        t.join(); return

    mid = convcore._user_rows(tid)
    r = convhead.deliver(tid, 'Reply with the single word LATE. Do nothing else.')
    t.join()

    check('busy: the follow-up waited for the running turn', (r.get('queued_for') or 0) > 0,
          'queued_for = %.2fs' % (r.get('queued_for') or 0))
    check('busy: the long turn completed', (out.get('work') or {}).get('ok'),
          (out.get('work') or {}).get('error', ''))
    agree(tid, mid, r, 'busy')
    check('busy: the follow-up was answered, not dropped',
          'late' in (r.get('reply') or '').lower(), repr(r.get('reply'))[:60])
    after = convcore._user_rows(tid)
    # Both turns, counted at the end. An earlier version also asserted
    # `mid > before` -- that the busy turn's own row was already on disk while
    # it was running -- and it failed: rows went 2 -> 2 -> 4. Claude Code does
    # not flush the transcript synchronously, so a read taken mid-turn can
    # still show the pre-turn count.
    #
    # Worth keeping in mind beyond this assertion: it means the transcript is a
    # witness to a *completed* turn, not a live one. `convcore._user_rows` is
    # read mid-turn by the pane path on every busy delivery, which is one more
    # reason that reading was never the reliable signal it was taken for.
    check('busy: both turns are in the transcript', after >= before + 2,
          'user rows %d -> %d -> %d (want +2 overall)' % (before, mid, after))


def case_echo_mode(tid):
    """`wait='echo'` must return on the handover, not on the answer.

    The contract the Discord door runs on, and the reason `deliver` has two
    modes at all: `bot/client.py` holds the request for at most 300s and its
    docstring says it "waits on delivery, not on the answer". A turn that takes
    longer than that is normal -- so if this mode blocked to completion, the bot
    would post "Zipper hasn't answered in 300s" into the thread while the turn
    was running perfectly well.

    So the assertion is about *timing*: a turn given real work must be
    acknowledged quickly and still be running when this returns.
    """
    print("\necho mode (returns on delivery, not on completion)")
    before = convcore._user_rows(tid)
    t0 = time.time()
    r = convhead.deliver(
        tid,
        'Using Bash, run `sleep 4 && echo one`, then `sleep 4 && echo two`, '
        'then `sleep 4 && echo three`. Then reply DONE.',
        wait='echo')
    took = time.time() - t0

    check('echo: reports ok on the handover', r.get('ok'), r.get('error', ''))
    check('echo: the session acknowledged it', r.get('echoed'))
    check('echo: returned before the turn finished', took < 20,
          'returned in %.1fs' % took)
    check('echo: does not claim the turn completed', not r.get('recorded'))
    check('echo: does not claim a reply', not r.get('reply'), repr(r.get('reply'))[:40])

    # The turn is still running in a daemon thread. Wait it out so the next
    # case is not racing it, and confirm it actually landed.
    end = time.time() + 180
    while time.time() < end and convcore._user_rows(tid) <= before:
        time.sleep(1)
    check('echo: the turn completed behind us',
          convcore._user_rows(tid) > before,
          'user rows %d -> %d' % (before, convcore._user_rows(tid)))
    # Let the background turn release the lock before the next case.
    end = time.time() + 120
    while time.time() < end:
        try:
            with convhead._turn_lock(tid, timeout=0.1):
                break
        except TimeoutError:
            time.sleep(1)


def case_silent_success(tid):
    """**The negative case that actually corresponds.**

    `delivery.py:case_undeliverable` puts a bare shell in the tmux session: the
    pane exists, the target resolves, `load-buffer` and `paste-buffer` both
    succeed, so everything a screen-based check looks at goes right and nothing
    is delivered. Its point is not "can this error" -- it is that a path can
    report success over a message no session ever saw.

    The protocol's version of that is a process that exits cleanly having
    delivered nothing: a `result` with `subtype: success` and no `user` echo.
    That is 2026-09-10 in this dialect -- the turn looks finished, so a check
    that trusted `result` alone would return ok over a message that was never
    received. `deliver` must require *both* witnesses, which is the entire
    reason it tracks `echoed` and `recorded` separately instead of one flag.

    A stub `claude` is the only way to produce that state on demand; the real
    one cannot be made to lie this particular way, which is the point.
    """
    print('\nsilent success (a clean result with no echo -- must NOT report ok)')
    stub = os.path.join(SBOX, 'fake-claude')
    with open(stub, 'w') as fh:
        fh.write('#!/bin/sh\n'
                 'cat >/dev/null\n'          # swallow the message, deliver nothing
                 'echo \'{"type":"system","subtype":"init"}\'\n'
                 'echo \'{"type":"result","subtype":"success","result":"OK"}\'\n'
                 'exit 0\n')
    os.chmod(stub, 0o755)

    real, convhead._claude = convhead._claude, lambda: stub
    try:
        before = convcore._user_rows(tid)
        r = convhead.deliver(tid, 'Reply with the single word OK.')
        after = convcore._user_rows(tid)
    finally:
        convhead._claude = real

    check('silent: refuses to report success', not r.get('ok'),
          'error=%r' % (r.get('error') or '')[:90])
    check('silent: saw no echo', not r.get('echoed'))
    check('silent: nothing was recorded', after == before,
          'user rows %d -> %d' % (before, after))
    check('silent: names the echo as the missing witness',
          'acknowledg' in (r.get('error') or ''), r.get('error', '')[:60])


def case_timeout(tid):
    """A turn that cannot finish in the time allowed.

    Weaker than `case_silent_success` and kept separate from it -- this only
    asserts the path gives up cleanly rather than hanging or claiming a reply it
    never received. It is not the analogue of anything in `delivery.py`; the
    pane path has no turn timeout at all, so a wedged TUI simply sat there.
    """
    print('\ntimeout (must give up without claiming a reply)')
    r = convhead.deliver(tid, 'Reply with the single word OK.', timeout=0.4)
    check('timeout: refuses to report success', not r.get('ok'),
          'error=%r' % (r.get('error') or '')[:80])
    check('timeout: claims no reply', not r.get('reply'), repr(r.get('reply'))[:60])
    check('timeout: did not claim the turn completed', not r.get('recorded'))


def cleanup(tid, sbox):
    print('\ncleanup')
    with convcore.mutate() as d:
        d.pop(str(tid), None)
    # Cleanup runs in a `finally`, so it must survive a case that blew up
    # mid-run -- anything raising here would mask the real failure.
    for p in (convhead._lock_path(tid), convcore.transcript(tid)):
        try:
            os.remove(p)
        except Exception:
            pass
    pane.forget_sandbox(sbox)
    shutil.rmtree(sbox, ignore_errors=True)
    print('  registry row, lock, transcript and sandbox removed')


def main():
    if not shutil.which('claude') and not os.path.exists(
            os.path.expanduser('~/.local/bin/claude')):
        print('claude not installed'); return 1

    global SBOX
    sbox = SBOX = pane.sandbox()
    # Repoint both modules: convhead runs the process in convhead.VAULT and
    # derives the transcript path through convcore.VAULT. Setting one and not
    # the other would run in the sandbox while writing transcripts next to the
    # real ones, which is exactly the leak the sandbox exists to prevent.
    convcore.VAULT = convhead.VAULT = sbox
    convcore.INBOX = convhead.INBOX = os.path.join(sbox, 'Inbox')
    os.makedirs(convhead.INBOX, exist_ok=True)
    convcore.CONV_JSON = os.path.join(convhead.INBOX, 'conversations.json')

    tid = 'local-selftest-head-%d' % int(time.time())

    # `convcore._user_rows` finds the transcript via `convcore.transcript`,
    # which rebuilds the project directory name and gets it wrong whenever the
    # path contains an underscore -- see `convhead.transcript`. `mkdtemp` draws
    # a random suffix, so leaving this alone makes the witness itself
    # intermittently blind, and a blind witness reports a delivered message as
    # lost. Resolve by session id instead.
    sid = convcore.session_id(tid)
    convcore.transcript = lambda _t=None, _s=sid: convhead.transcript(_s)

    print('sandbox: %s\nthread:  %s\nsession: %s' % (sbox, tid, sid))

    try:
        case_cold(tid)
        case_resume(tid)
        case_busy(tid)
        case_echo_mode(tid)
        case_silent_success(tid)
        case_timeout(tid)
    finally:
        cleanup(tid, sbox)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    for f in FAIL:
        print('  FAILED: %s' % f)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
