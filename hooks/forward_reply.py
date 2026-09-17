#!/usr/bin/env python3
"""Forward a finished reply to the Discord thread that asked for it.

Wired to Claude Code's **Stop** hook, which fires once when a turn ends and
hands over `transcript_path` on stdin.

Why this exists
---------------
A conversation that arrived from Discord used to be answered twice: once as the
argument to `zipper discord send`, and once as the session's own reply into the
terminal that nobody was reading. That is the same words through the model
twice -- measured at **44% of the paired output** on 2026-09-06 -- to say one
thing to one person.

It also asked the session to decide, every turn, which surface it was talking
to. It got that wrong eight times in a row that day, replying to Discord for
messages that had been typed at the keyboard, because the only marker was on
the *first* message of the session and it generalised from there. A decision
made from ambient context every turn is a decision that will drift.

So the session no longer decides and no longer sends: it writes one reply, and
this decides where that reply goes.

The rule
--------
Post if the turn was *started* from Discord. The bot records what it delivered
(`conversations.note_delivery`), and the last user message in the transcript is
compared against that record. A match means the bot put it there; anything else
means he typed it, and the terminal already showed him the answer.

The comparison is against the **last** user message rather than the first,
because that is the one that started this turn -- a conversation can begin on a
phone and continue at the keyboard, and each turn is routed on its own.

Rules that matter
-----------------
- **Always exit 0.** A non-zero exit from a Stop hook is not advisory: exit code
  2 *prevents the turn ending* and feeds stderr back to the model. A Discord
  outage must not trap a session in a loop, so every failure here is swallowed.
  Silence is the correct failure mode -- the terminal still has the answer.
- **Dedupe on the assistant message uuid.** The hook can fire more than once for
  a turn; forwarding is not idempotent from Discord's side.
- **Skip `local-` threads.** `new_conversation()` falls back to a local id when
  Discord is unreachable, precisely so a conversation can still start without
  it. There is no thread to post to, and that is not an error.
- **Wait for the closing row before reading the reply.** Stop fires while Claude
  Code is still appending the final assistant message, so the answer is often
  not on disk yet -- see `main`.
- **A turn can be prompted by more than one message.** Provenance is asked of
  all of them, not only the last -- see `read_turn`.
"""
import os, sys, json, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _tail(path, limit=1_000_000):
    """The end of a transcript. These files reach megabytes and only the last
    turn matters, so never read the whole thing."""
    with open(path, 'rb') as fh:
        try:
            if os.fstat(fh.fileno()).st_size > limit:
                fh.seek(-limit, os.SEEK_END)
                fh.readline()          # drop the partial line the seek landed in
        except OSError:
            pass
        for raw in fh:
            try:
                yield json.loads(raw.decode('utf-8', 'replace'))
            except ValueError:
                continue


def _text_blocks(row):
    c = (row.get('message') or {}).get('content')
    if isinstance(c, str):
        return [c]
    if isinstance(c, list):
        return [b.get('text') or '' for b in c
                if isinstance(b, dict) and b.get('type') == 'text']
    return []


def read_turn(path):
    """The last user message, and the last assistant text of this turn.

    A user row carrying only `tool_result` blocks is the harness feeding a tool
    call back in, not a person speaking -- taking it as the prompt would mean
    every turn looked like it came from the terminal.

    Returns `(last_user, reply, uuid, closed, turn_users)`.

    `closed` says whether the turn's final assistant row -- the one whose
    `stop_reason` is *not* `tool_use` -- is on disk yet. The hook fires and
    reads the file in the same second Claude Code is appending that row, so the
    answer is sometimes not there; `main` waits for it. Without the wait the
    hook reads a turn whose reply is still empty, logs `empty turn`, and returns
    -- the reply is then never forwarded at all, because Stop does not fire
    twice. That is what ate five replies on the morning of 2026-09-16.

    `turn_users` is **every** prompt this turn is answering, not just the last.
    The opening message starts the turn; anything he sends while it runs arrives
    as an `enqueue` and is answered by the same turn. They do not all come
    through the same door -- a turn opened from Discord can be added to from the
    dashboard -- so "did this turn come from Discord?" cannot be answered from
    the most recent message alone, and answering it that way sent a Discord
    turn's reply to a terminal nobody was reading.
    """
    last_user, last_asst, uuid_, closed = '', '', '', False
    turn_users = []
    for row in _tail(path):
        t = row.get('type')
        if t == 'user':
            blocks = _text_blocks(row)
            body = '\n'.join(b for b in blocks if b.strip())
            if body.strip():
                last_user, last_asst, uuid_, closed = body, '', '', False
                # A `user` row starts a new turn, so the previous turn's
                # prompts go with it.
                turn_users = [body]
        elif t == 'queue-operation' and row.get('operation') == 'enqueue':
            # **A message that arrives mid-turn never becomes a `user` row.**
            # Claude Code queues it and records it here instead, as
            # `{"operation": "enqueue", "content": "..."}`, then surfaces it
            # inside the running turn. So the turn is genuinely answering it
            # while the last `user` row still holds whatever came before --
            # on 2026-09-08 that was an image paste from the terminal, so the
            # hook compared the wrong text, found no delivery, and called a
            # Discord message "typed at the keyboard". Two replies were lost
            # this way before `forward.log` made it visible in one line.
            #
            # Only `enqueue` carries the prompt. `remove` and `dequeue` repeat
            # or omit the same content as the queue drains and would just
            # re-set what is already correct.
            body = (row.get('content') or '').strip()
            if body:
                last_user, last_asst, uuid_, closed = body, '', '', False
                # An `enqueue` never *starts* a turn -- it interrupts one that
                # is already running -- so it adds to this turn's prompts
                # rather than replacing them. That is what keeps a
                # Discord-opened turn recognisable after he types into the
                # dashboard mid-turn.
                turn_users.append(body)
        elif t == 'assistant':
            for b in _text_blocks(row):
                if b.strip():
                    last_asst, uuid_ = b, row.get('uuid') or ''
            # The closing row is the one that did *not* stop to call a tool. A
            # row with no text still closes the turn -- a turn can end on a tool
            # result with nothing said after it, and waiting for words that are
            # never coming is how this would hang on its own timeout.
            if (row.get('message') or {}).get('stop_reason') != 'tool_use':
                closed = True
    return last_user, last_asst, uuid_, closed, turn_users


def _log(line):
    """Why this hook did what it did, appended to `Inbox/forward.log`.

    **Silence is the right failure mode for the model and the wrong one for
    diagnosis.** Every branch below is a `return` and every exception was
    swallowed, so a reply that never reached Discord left no trace anywhere:
    not in the journal, not in the registry, not on screen. Three separate
    forwarding bugs on 2026-09-08 each had to be reconstructed afterwards from
    timestamps and uuids, and the third could not be attributed at all.

    So each decision now says itself, once, in one line. Inbox/ is gitignored
    machine state, the file is trimmed, and a failure to log is still never
    allowed to reach the model.
    """
    try:
        from zipper.core import INBOX
        p = os.path.join(INBOX, 'forward.log')
        stamp = __import__('datetime').datetime.now().isoformat(timespec='seconds')
        with open(p, 'a', encoding='utf-8') as fh:
            fh.write('%s  %s\n' % (stamp, line))
        # Keep it readable rather than eternal; this is a diagnostic, not a record.
        if os.path.getsize(p) > 200_000:
            with open(p, encoding='utf-8') as fh:
                tail = fh.readlines()[-1000:]
            with open(p, 'w', encoding='utf-8') as fh:
                fh.writelines(tail)
    except Exception:
        pass


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    path = payload.get('transcript_path')
    if not path or not os.path.exists(path):
        _log('skip  no transcript_path')
        return

    from zipper import conversations, chat

    last_user, reply, uuid_, closed, turn_users = read_turn(path)
    # **The closing row is often not on disk yet.** Waiting costs nothing when
    # it is already there, and the hook's own timeout is 45s, so six seconds is
    # well inside it.
    for _ in range(12):
        if closed:
            break
        time.sleep(0.5)
        last_user, reply, uuid_, closed, turn_users = read_turn(path)

    if not last_user or not reply.strip():
        _log('skip  empty turn (user=%d reply=%d closed=%s)'
             % (len(last_user), len(reply), closed))
        return
    if not closed:
        # The turn ended on a tool use -- interrupted, or stopped by another
        # hook -- so there is no closing row to wait for. What was said before
        # the tools was still said, and it is the only thing he will get for
        # this turn. Send it rather than letting the turn vanish.
        _log('note  turn never closed; forwarding %d chars anyway' % len(reply))

    # **A pane never forwards, whatever else is in its environment.**
    # `ZIPPER_CONVERSATION` is set only by `convcore.start`, i.e. only inside a
    # tmux pane, and `convhead` never sets it -- so its presence is a positive
    # identification of a conversation that has no Discord thread to answer.
    # Checked before the thread is resolved at all, because the failure being
    # prevented is precisely a pane resolving *some* thread and posting a
    # keyboard reply into it (2026-09-17). Env hygiene in `start` already
    # prevents this; this makes it an invariant rather than a convention.
    if os.environ.get('ZIPPER_CONVERSATION'):
        _log('skip  dashboard pane (conversation=%r) -- panes do not forward'
             % os.environ.get('ZIPPER_CONVERSATION'))
        return

    # Which conversation is this? The env var is set by `convhead` for a
    # headless turn answering a Discord thread; the registry lookup by session
    # id is the fallback for a turn whose process never had it.
    tid = os.environ.get('ZIPPER_DISCORD_THREAD') or ''
    if not tid:
        sid = payload.get('session_id') or ''
        for k, row in (conversations.load() or {}).items():
            if sid and row.get('session_id') == sid:
                tid = k
                break
    if not tid or tid.startswith('local-'):
        _log('skip  no discord thread (tid=%r)' % tid)
        return

    if not any(conversations.delivered(tid, u) for u in (turn_users or [last_user])):
        # Typed at the keyboard; he already saw it. Logged anyway, because
        # "decided it was typed" is exactly the wrong call that ate a reply
        # twice today, and it is indistinguishable from a real one in hindsight.
        _log('skip  %s not a delivered message -- treated as typed (%r)'
             % (tid, last_user[:60]))
        return

    row = conversations.load().get(str(tid)) or {}
    if uuid_ and row.get('last_forwarded') == uuid_:
        _log('skip  %s already forwarded %s' % (tid, uuid_))
        return

    try:
        chat.discord_send(reply, thread_id=tid)
    except Exception as e:
        # Never block the turn on Discord -- but never fail invisibly either,
        # and never leave the thread showing a typing indicator for an answer
        # that is not coming. That combination is what made him wait in Discord
        # long after the reply had been written to a terminal he wasn't reading.
        _log('FAIL  %s send failed after %d chars: %s: %s'
             % (tid, len(reply), type(e).__name__, e))
        try:
            chat.discord_typing(False, tid)
        except Exception:
            pass
        return
    _log('sent  %s %d chars uuid=%s' % (tid, len(reply), uuid_))
    conversations.touch(tid, last_forwarded=uuid_)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        pass                            # see the module docstring: always exit 0
    sys.exit(0)
