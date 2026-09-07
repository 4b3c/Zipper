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
"""
import os, sys, json

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
    """
    last_user, last_asst, uuid_ = '', '', ''
    for row in _tail(path):
        t = row.get('type')
        if t == 'user':
            blocks = _text_blocks(row)
            body = '\n'.join(b for b in blocks if b.strip())
            if body.strip():
                last_user, last_asst, uuid_ = body, '', ''
        elif t == 'assistant':
            for b in _text_blocks(row):
                if b.strip():
                    last_asst, uuid_ = b, row.get('uuid') or ''
    return last_user, last_asst, uuid_


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    path = payload.get('transcript_path')
    if not path or not os.path.exists(path):
        return

    from zipper import conversations, chat

    last_user, reply, uuid_ = read_turn(path)
    if not last_user or not reply.strip():
        return

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
        return

    if not conversations.delivered(tid, last_user):
        return                          # typed at the keyboard; he already saw it

    row = conversations.load().get(str(tid)) or {}
    if uuid_ and row.get('last_forwarded') == uuid_:
        return                          # this hook already ran for this turn

    try:
        chat.discord_send(reply, thread_id=tid)
    except Exception:
        return                          # never block the turn on Discord
    conversations.touch(tid, last_forwarded=uuid_)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        pass                            # see the module docstring: always exit 0
    sys.exit(0)
