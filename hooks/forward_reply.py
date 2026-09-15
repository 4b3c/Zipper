#!/usr/bin/env python3
"""Forward what a turn says to the Discord thread that asked for it.

Wired to two of Claude Code's hooks, both handing over `transcript_path` on
stdin: **PostToolUse**, which fires after every tool call and forwards what has
been said so far, and **Stop**, which fires once when the turn ends and forwards
what is left. `hook_event_name` in the payload says which pass this is.

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
- **Every message of the turn is forwarded, in order, as it is written** -- the
  ones written before tool calls as well as the one that ends the turn. A turn
  that ends *on* a tool use is still delivered rather than lost.
- **Dedupe on the assistant message uuid**, per message and not per turn, and
  **claimed before the send** -- see `_claim`. Both hooks fire against the same
  transcript, parallel tool calls fire the live one concurrently with itself,
  and forwarding is not idempotent from Discord's side.
- **The live pass never blocks.** It runs between a tool finishing and the model
  seeing the result; time spent here is time added to the turn.
- **Skip `local-` threads.** `new_conversation()` falls back to a local id when
  Discord is unreachable, precisely so a conversation can still start without
  it. There is no thread to post to, and that is not an error.
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
    """The last user message, and **every** assistant message of this turn.

    A user row carrying only `tool_result` blocks is the harness feeding a tool
    call back in, not a person speaking -- taking it as the prompt would mean
    every turn looked like it came from the terminal.

    Returns `(last_user, messages, closed)`, where `messages` is a list of
    `(uuid, text, is_preamble)` in the order they were written and `closed` says
    whether the turn's final assistant row -- the one whose `stop_reason` is
    *not* `tool_use` -- is on disk yet.

    **Every text block is a message, including the ones that precede tool
    calls.** "Let me check the reply path." is written before the tools and the
    answer lands minutes later; both are things that were said, and the thread
    now shows both, in order. This reverses the 2026-09-14 rule that treated a
    preamble as unforwardable -- that rule was aimed at a real bug (95
    characters of throat-clearing forwarded *instead of* the 3057-character
    answer behind it), and the fix for that bug is ordering and completeness,
    not suppression. A preamble can no longer displace an answer because it
    does not compete with it: each is sent once, on its own uuid.

    `closed` is what remains of the race. The hook fires and reads the file in
    the same second Claude Code is appending the final row, so the answer is
    sometimes not flushed yet; `main` waits for `closed` before sending. It
    gives up eventually rather than forever, because a turn that ends *on* a
    tool use -- interrupted, or stopped by another hook -- never produces a
    closing row at all, and those messages are still worth delivering.
    """
    last_user, msgs, closed = '', [], False
    for row in _tail(path):
        t = row.get('type')
        if t == 'user':
            blocks = _text_blocks(row)
            body = '\n'.join(b for b in blocks if b.strip())
            if body.strip():
                last_user, msgs, closed = body, [], False
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
                last_user, msgs, closed = body, [], False
        elif t == 'assistant':
            # Joined, not overwritten: if one row carries more than one text
            # block they are all part of the one message, and keeping only the
            # last is the same "not the full message" bug in miniature.
            body = '\n\n'.join(b for b in _text_blocks(row) if b.strip())
            # A row that stopped to call a tool is **finished being written**:
            # the tool could not have run otherwise. That is what makes it safe
            # to forward mid-turn, and it is the only kind of row the
            # `PostToolUse` pass will send.
            pre = (row.get('message') or {}).get('stop_reason') == 'tool_use'
            if body:
                msgs.append((row.get('uuid') or '', body, pre))
            # The closing row is the one that did *not* stop to call a tool. A
            # row with no text still closes the turn -- a turn can end on a tool
            # result with nothing said after it, and waiting for words that are
            # never coming is how the hook would hang on its own timeout.
            if not pre:
                closed = True
    return last_user, msgs, closed


def _claim(conversations, tid, msgs):
    """Take the messages nobody has forwarded yet, and mark them taken.

    **Claim before sending, not after.** Since 2026-09-15 this runs on every
    tool call as well as at the end of the turn, and *parallel* tool calls fire
    parallel copies of this hook -- same transcript, same pending message, two
    processes. Read-then-send-then-record leaves a window between the read and
    the record where the other copy sees an unclaimed message and posts it
    again, and Discord has no idea the two are the same. Claiming inside the
    registry's flock closes it: exactly one process comes out of `mutate` with
    the uuid in hand.

    The cost is that a claimed message which then fails to send is claimed and
    unsent, which is why `_unclaim` exists. Losing a message is worse than
    sending it twice, but both are avoidable, so avoid both.

    Bounded, because this is a dedupe window and not a history -- a turn's worth
    of messages plus room to spare.
    """
    taken = []
    with conversations.mutate() as d:
        row = d.setdefault(str(tid), {})
        seen = [u for u in (row.get('forwarded') or []) if u]
        # A registry written before per-message dedupe existed.
        if row.get('last_forwarded') and row['last_forwarded'] not in seen:
            seen.append(row['last_forwarded'])
        for uuid_, body, pre in msgs:
            if uuid_ and uuid_ in seen:
                continue
            taken.append((uuid_, body, pre))
            if uuid_:
                seen.append(uuid_)
        if taken:
            row['forwarded'] = seen[-40:]
            row['last_forwarded'] = taken[-1][0] or row.get('last_forwarded')
    return taken


def _unclaim(conversations, tid, uuids):
    """Put unsent messages back, so the next firing retries them."""
    drop = set(u for u in uuids if u)
    if not drop:
        return
    try:
        with conversations.mutate() as d:
            row = d.setdefault(str(tid), {})
            row['forwarded'] = [u for u in (row.get('forwarded') or []) if u not in drop]
            if row.get('last_forwarded') in drop:
                row['last_forwarded'] = (row['forwarded'] or [''])[-1]
    except Exception:
        pass


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

    # **Two firings, one job.** `PostToolUse` runs after every tool call and
    # sends what has been said so far; `Stop` runs once at the end and sends
    # whatever is left. The live pass is what makes a long turn readable from a
    # phone -- before it, the whole turn arrived in one burst at the end, so a
    # ten-minute turn showed nothing for ten minutes and then everything.
    live = payload.get('hook_event_name') == 'PostToolUse'

    last_user, msgs, closed = read_turn(path)
    if live:
        # **Never wait in the live pass.** This hook sits between a tool
        # finishing and the model seeing its result, so every second here is a
        # second added to the turn. Nothing needs waiting for either: a row that
        # stopped to call a tool is on disk by definition, which is exactly the
        # set the live pass sends.
        msgs = [m for m in msgs if m[2]]
    else:
        # **The closing row is often not on disk yet.** Stop fires and this reads
        # the transcript inside the same second Claude Code is appending the final
        # assistant message; on 2026-09-14 it lost that race twice in one
        # conversation. Waiting costs nothing when the row is already there, and the
        # hook's own timeout is 20s, so a few seconds is well inside it.
        for _ in range(12):
            if closed:
                break
            time.sleep(0.5)
            last_user, msgs, closed = read_turn(path)

    if not last_user or not msgs:
        if not live:
            _log('skip  empty turn (user=%d msgs=%d closed=%s)'
                 % (len(last_user), len(msgs), closed))
        return
    if not closed and not live:
        # The turn ended on a tool use -- interrupted, or stopped by another
        # hook -- so there is no closing row to wait for. What was said before
        # the tools was still said, and it is the only thing he will ever get
        # for this turn. Send it rather than letting the turn vanish.
        _log('note  turn never closed; forwarding %d message(s) anyway' % len(msgs))

    # Which conversation is this? The env var is set for every per-thread
    # instance; the registry lookup by session id covers a conversation that was
    # adopted after it started, whose pane never had the variable.
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

    if not conversations.delivered(tid, last_user):
        # Typed at the keyboard; he already saw it. Logged anyway, because
        # "decided it was typed" is exactly the wrong call that ate a reply
        # twice today, and it is indistinguishable from a real one in hindsight.
        _log('skip  %s not a delivered message -- treated as typed (%r)'
             % (tid, last_user[:60]))
        return

    pending = _claim(conversations, tid, msgs)
    if not pending:
        _log('skip  %s nothing new (%d message(s) already forwarded)' % (tid, len(msgs)))
        return

    for i, (uuid_, body, _pre) in enumerate(pending):
        try:
            chat.discord_send(body, thread_id=tid)
        except Exception as e:
            # Never block the turn on Discord -- but never fail invisibly
            # either, and never leave the thread showing a typing indicator for
            # an answer that is not coming. That combination is what made him
            # wait in Discord long after the reply had been written to a
            # terminal he wasn't reading.
            #
            # Stop at the first failure rather than pressing on: the rest would
            # arrive out of order behind a gap. Everything not sent goes back on
            # the queue, so the next firing -- the next tool call, or `Stop` --
            # picks up exactly where this left off.
            _log('FAIL  %s send failed after %d chars: %s: %s'
                 % (tid, len(body), type(e).__name__, e))
            _unclaim(conversations, tid, [u for u, _b, _p in pending[i:]])
            try:
                chat.discord_typing(False, tid)
            except Exception:
                pass
            return
        _log('sent  %s %d chars uuid=%s%s'
             % (tid, len(body), uuid_, '  (live)' if _pre else ''))

    if live:
        # `discord_send` clears the typing indicator, because from its point of
        # view the turn is over. Mid-turn it is not: more is coming, and a
        # thread that stops showing Zipper as typing after a preamble reads as
        # an answer that ended there. Put it back.
        try:
            chat.discord_typing(True, tid)
        except Exception:
            pass


if __name__ == '__main__':
    try:
        main()
    except Exception:
        pass                            # see the module docstring: always exit 0
    sys.exit(0)
