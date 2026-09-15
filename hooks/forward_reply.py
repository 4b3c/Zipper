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
import os, sys, re, json, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# **Messages this turn already posted live.** `hooks/stream_watch.py` scrapes
# the tmux pane while Claude is typing and posts what it sees, so by the time
# this runs a message may already be in the thread -- approximate, wrapped, and
# possibly truncated. The `Stop` pass overwrites each one with the real text
# instead of sending it again. `ZIPPER_STREAM=0` disables the watcher; this side
# then finds no entries and behaves exactly as it did before.
STREAM = os.environ.get('ZIPPER_STREAM', '1') != '0'

# Set as soon as the pass knows them, so the exit can clear the typing
# indicator no matter which of the returns below it leaves by.
_TID, _LIVE = '', True


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

    Returns `(last_user, messages, closed, turn_users)`, where `messages` is a list of
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
    # **A turn can be prompted by more than one message, through more than one
    # door.** The opening message starts it; anything he sends while it runs
    # arrives as an `enqueue` and is answered by the same turn. They do not all
    # come from the same place -- a turn opened from Discord can be interrupted
    # from the dashboard and vice versa -- so "did this turn come from Discord?"
    # cannot be answered from the most recent message alone. Every prompt this
    # turn is answering is collected here and `main` asks about all of them.
    turn_users = []
    for row in _tail(path):
        t = row.get('type')
        if t == 'user':
            blocks = _text_blocks(row)
            body = '\n'.join(b for b in blocks if b.strip())
            if body.strip():
                last_user, msgs, closed = body, [], False
                # A `user` row is a new turn, so the previous turn's prompts go.
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
                last_user, msgs, closed = body, [], False
                # An `enqueue` never *starts* a turn -- it interrupts one that
                # is already running -- so it adds to this turn's prompts rather
                # than replacing them. That is what keeps a Discord-opened turn
                # recognisable after he types something in the dashboard.
                turn_users.append(body)
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
    return last_user, msgs, closed, turn_users


def _correct(chat, tid, ids, text):
    """Rewrite the turn's live messages with what was actually written.

    The watcher posts what the pane showed -- markdown already rendered, wrapped
    at the pane width, split wherever it happened to hit the limit. This is the
    moment the true text is known, so the whole turn is laid back over the
    messages it already occupies: chunk *i* into id *i*, any extra chunks sent
    after, and any message the true text no longer needs deleted.

    **Deleting the surplus matters.** The live split is made on pane text and
    the real split on source text, so the two disagree about how many messages a
    long turn takes. Leaving the leftovers would show him a duplicated tail.
    """
    from utils.text import smart_split
    chunks = smart_split(text) or ['']
    for n, chunk in enumerate(chunks):
        if n < len(ids):
            chat._bot('/edit', {'message_id': ids[n], 'thread_id': tid,
                                'content': chunk}, timeout=20)
        else:
            chat._bot('/send', {'message': chunk, 'thread_id': tid}, timeout=20)
    for spare in ids[len(chunks):]:
        try:
            chat._bot('/delete', {'message_id': spare, 'thread_id': tid}, timeout=20)
        except Exception:
            pass
    return len(chunks)


def _stop_watcher(state_path):
    """Tell the watcher the turn is over, and wait for it to let go.

    **Order matters.** Corrections come next, and a watcher still polling would
    see the pane, decide the message had changed, and write the wrapped pane
    text back over the corrected version -- the last writer wins and it would be
    the wrong one.
    """
    try:
        open(state_path + '.stop', 'w').close()
    except Exception:
        return
    for _ in range(12):                 # the poll is 0.4s; this is generous
        if not os.path.exists(state_path + '.stop'):
            return
        time.sleep(0.1)


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


def _close_state(state_path, state):
    """Mark the turn's stream state finished, so the next turn cannot adopt it.

    **A finished message must never be written into again.** `stream_watch`
    adopts an *unclosed* state file so that a watcher killed mid-turn can pick
    up the message already in flight. Nothing distinguished that from a turn
    that had simply ended a moment ago, so a question asked ~20 seconds after
    the previous answer landed was streamed into the previous answer's messages
    and then laid over them by the correction below: 3410 characters written on
    top of a reply he had already read, and no new message in the thread at all
    (2026-09-15, reported as "I didn't get a response in Discord").

    The watcher also marks this on its way out. This is the other half, because
    a watcher can be killed before it gets there.
    """
    if not state_path:
        return
    try:
        state['closed'] = True
        tmp = state_path + '.tmp'
        with open(tmp, 'w') as fh:
            json.dump(state, fh)
        os.replace(tmp, state_path)
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
    """The `Stop` pass owns turning the typing indicator off.

    **It cannot be left to the send.** `discord_send` clears typing in a
    `finally`, which covered every exit while sending was the only way a message
    reached Discord. It no longer is: a streamed message is *corrected* with a
    raw `/edit`, and a turn whose messages were all streamed sends nothing at
    all -- so the indicator stayed on after the answer had been delivered and
    read. Every early return below is also an exit from a finished turn, so the
    clear belongs in one `finally` around the whole pass rather than on the path
    that happens to send.
    """
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

    state, state_path = {}, ''

    last_user, msgs, closed, turn_users = read_turn(path)
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
            last_user, msgs, closed, turn_users = read_turn(path)

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
    global _TID, _LIVE
    _TID, _LIVE = tid, live

    # **The watcher's state is per conversation, and has to be.** There is more
    # than one live Claude at a time -- one per Discord thread -- so there is
    # more than one watcher, and a single `stream.json` meant whichever wrote
    # last owned it. Each conversation's `Stop` pass then laid its own text over
    # whatever ids it found, including messages belonging to the other thread,
    # and deleted the rest as surplus. Seen happening on 2026-09-15 with two
    # conversations open.
    #
    # The thread id is in the filename *and* checked inside, because a stale
    # file from a crashed watcher is worth ignoring rather than obeying.
    if STREAM:
        try:
            from zipper.core import INBOX
            state_path = os.path.join(INBOX, 'stream-%s.json' % tid)
            if not live:
                _stop_watcher(state_path)
            with open(state_path) as fh:
                state = json.load(fh)
            if str(state.get('thread') or '') != str(tid):
                _log('skip  %s state file belongs to %r -- ignored'
                     % (tid, state.get('thread')))
                state = {}
        except Exception:
            state = {}

    # **A closed turn's ids are finished messages, and belong to no one now.**
    # `closed` was added so the *watcher* would post rather than adopt them;
    # this side went on reading `ids` unconditionally, which is the same
    # overwrite through the other door -- a later turn's `Stop` laying its reply
    # over messages from a turn that had already ended. Seen on 2026-09-15 with
    # a typed turn whose streamed messages failed to delete: the state stayed
    # closed with live ids in it, and the next turn picked them up.
    ids = [str(i) for i in (state.get('ids') or [])] if STREAM else []
    if state.get('closed'):
        ids = []

    if not any(conversations.delivered(tid, u) for u in (turn_users or [last_user])):
        # **The watcher streams before anyone has checked where the turn came
        # from.** It starts on `UserPromptSubmit` for any session that has a
        # thread at all, and never consults `delivered()` -- so by the time this
        # runs, a turn he typed at the keyboard may already have pane text
        # sitting in Discord. Those messages should not be there.
        #
        # An earlier version of this branch *adopted* them instead, on the
        # reasoning that ids on disk prove the turn is already being streamed.
        # That was wrong in the one way that matters: ids prove only that the
        # watcher ran, which it does for every turn. It made `delivered()` a
        # no-op and sent every terminal-typed reply to Discord. It was also
        # papering over a different bug -- a Discord turn interrupted from the
        # dashboard looked typed, because only the last prompt was checked --
        # and that is fixed at the source now, in `turn_users`.
        # **Only on the `Stop` pass.** The watcher is stopped by then (that
        # happens where the state is loaded, above), so what it posted stays
        # deleted. Deleting on a live pass would race a watcher still polling:
        # it would repost on its next poll and this would delete again, once per
        # tool call, for the length of the turn.
        if ids and not live:
            _log('undo  %s typed turn, removing %d streamed message(s) (%r)'
                 % (tid, len(ids), last_user[:40]))
            for mid in ids:
                try:
                    chat._bot('/delete', {'message_id': mid, 'thread_id': tid},
                              timeout=20)
                except Exception as e:
                    # **The reason is in the response body, not the exception.**
                    # `HTTPError` on its own says only that the bot returned
                    # 500; the body says which Discord error it was. Logging the
                    # class alone cost a diagnosis on 2026-09-15.
                    why = getattr(e, 'read', None)
                    try:
                        why = why().decode()[:200] if why else str(e)
                    except Exception:
                        why = str(e)
                    _log('FAIL  %s delete %s: %s' % (tid, mid, why))
            _close_state(state_path, state)
            return
        # Typed at the keyboard; he already saw it. Logged anyway, because
        # "decided it was typed" is exactly the wrong call that ate a reply
        # twice today, and it is indistinguishable from a real one in hindsight.
        _log('skip  %s not a delivered message -- treated as typed (%r)'
             % (tid, last_user[:60]))
        return

    # **Tell the watcher when a new prompt arrived mid-turn.** It streams from
    # the pane, where a submitted prompt and one he is still typing look the
    # same; the transcript's `enqueue` row is unambiguous. Without this the
    # watcher keeps growing the message it is already on, and that message sits
    # *above* his new question in the thread -- an answer shown before the thing
    # it answers. One message per prompt, not per turn.
    if STREAM and state_path and len(turn_users) > 1:
        try:
            with open(state_path + '.seal', 'w') as fh:
                fh.write(str(len(turn_users)))
        except Exception:
            pass

    pending = _claim(conversations, tid, msgs)
    if not pending:
        _log('skip  %s nothing new (%d message(s) already forwarded)' % (tid, len(msgs)))
        if not live:
            # The turn is over even though there was nothing new to send, and an
            # unclosed state file is one the next turn will write into.
            _close_state(state_path, state)
        return

    if ids and live:
        # The watcher owns the thread until the turn ends: it is still polling,
        # and anything written now would be overwritten by its next edit. Claim
        # the messages so they are not sent twice, and say nothing.
        _log('held  %s %d message(s) streaming live' % (tid, len(pending)))
        return

    # **Everything below is a turn that has ended, so close the state here
    # rather than on each way out.** `ids` is already in hand, so marking the
    # file costs nothing and no later branch has to remember: a correction, a
    # failed correction and a plain send all leave messages that must not be
    # written into again by the next turn. Closing per-exit is how the `nothing
    # new` branch above got missed in the first place.
    _close_state(state_path, state)

    if ids:
        # **One turn, one rewrite.** Everything Claude said is laid over the
        # messages the watcher already posted -- no per-message matching, which
        # is the thing that kept going wrong while each block was tracked
        # separately.
        whole = '\n\n'.join(b for _u, b, _p in msgs if b.strip())
        try:
            n = _correct(chat, tid, ids, whole)
        except Exception as e:
            _log('FAIL  %s correct failed: %s: %s' % (tid, type(e).__name__, e))
            _unclaim(conversations, tid, [u for u, _b, _p in pending])
            return
        _log('fixed %s %d chars over %d message(s)' % (tid, len(whole), n))
        return

    for i, (uuid_, body, _pre) in enumerate(pending):
        try:
            chat.discord_send(body, thread_id=tid)
        except Exception as e:
            # Never block the turn on Discord -- but never fail invisibly
            # either, and never leave the thread showing a typing indicator for
            # an answer that is not coming.
            #
            # Stop at the first failure rather than pressing on: the rest would
            # arrive out of order behind a gap. Everything not sent goes back on
            # the queue, so the next firing picks up exactly where this left off.
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
    # The turn is over; nothing is being typed. See `main`'s docstring for why
    # this is here and not on the send path.
    if _TID and not _LIVE:
        try:
            from zipper import chat as _chat
            _chat.discord_typing(False, _TID)
        except Exception:
            pass
    sys.exit(0)
