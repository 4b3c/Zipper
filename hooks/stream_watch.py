#!/usr/bin/env python3
"""Stream what Claude is saying to Discord *while it is being said*.

Why this is a screen scraper
----------------------------
It reads the tmux pane. That is an ugly thing to build and it was not the first
choice -- it is the only choice, and the alternatives were measured before this
existed:

- **The transcript is not a feed.** `~/.claude/projects/.../<session>.jsonl` is
  appended one row per *completed* message. Sampled every 0.25s on 2026-09-15
  while a reply was being written, it produced 4 writes of 1247, 4585, 1537 and
  6647 bytes -- each the whole message, landing when the message finished. A
  watcher on that file learns nothing sooner than the `Stop` hook does.
- **No hook fires on a token.** `PostToolUse` is the finest-grained event Claude
  Code offers, and it fires *between* messages.
- **Revealing finished text is worse than not streaming.** That was tried first
  and deleted the same day: a `/stream` endpoint that posted a finished reply
  word by word delayed delivery by six seconds to make it look live. Slower, and
  it told him nothing sooner.

The pane is the only place the words exist before the turn ends, because it is
where they are being printed. So this polls it.

What it does
------------
Started by the `UserPromptSubmit` hook, one per turn. Four times a second it
captures the pane, finds the message currently being written, and -- no faster
than about once a second, which is the per-channel edit budget -- posts or grows
the matching Discord message. It exits when the turn ends.

The message being written carries a footer in Discord's subtext: the elapsed
time and token count from Claude Code's own spinner. **Exactly one message ever
wears it.** It moves to each new message and comes off the one before, and off
the last one when the turn ends -- a frozen stopwatch under a finished paragraph
reads as a message that stalled rather than one that finished.

**One message per turn.** Everything Claude says between one of his messages
and the next goes into a single Discord message that grows -- the paragraphs
either side of a tool call are one reply, not three utterances -- and a second
message starts only when the first hits Discord's 2000-character cap.

What it does *not* do is decide the final text. The pane is wrapped, rendered
and lossy: markdown is styled, code blocks carry box-drawing, long messages
scroll. So every message this posts is recorded in `Inbox/stream-<thread>.json`,
one file per conversation, and the
`Stop` hook overwrites each one with the authoritative text from the transcript
when the turn ends. **Live but approximate, then exact.** That split is what
makes scraping tolerable: a rendering artefact is visible for a second or two
and then corrected, rather than being what he keeps.
"""
import os, sys, re, json, time, fcntl, subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

POLL = 0.25         # how often the pane is read
# **Measured, not chosen.** 0.3s asks for ~3.3 edits/s against a limit of about
# 5 per 5s and collapses into multi-second stalls; ~1/s was clean. This is the
# interval between the *starts* of two edits, and an edit costs ~0.5s, so the
# real cadence lands near a second. Discord cannot go faster than that -- the
# terminal streams tokens, a thread cannot.
EDIT_EVERY = 0.75
FOOT_EVERY = 5.0    # the status footer alone, when the prose has not moved
MAX_IDLE = 900      # a turn that never ends must not leave this running forever
LIMIT = 1900        # Discord's cap is 2000; leave room for the footer

BULLET = '●'   # the dot Claude Code prints before an assistant message

# **A tool call gets the same bullet as a message**, so the bullet alone cannot
# tell prose from furniture -- `● Running 1 shell command · 18s…` looks exactly
# like the start of a reply. What separates them is the first word: a tool block
# always opens with one of these verbs and a count or a name.
#
# **Match the stem and let the ending vary.** An explicit list of inflections
# missed the ones nobody thought to type: `Running`, `Reading` and `Writing`
# were spelled out but `Searching`, `Updating` and `Listing` were not, and
# `Search\b` does not match `Searching`. The combined summary line Claude Code
# prints while several tools run -- `Searching for 1 pattern, reading 1 file,
# running 7 shell commands…` -- therefore read as prose, with the consequences
# in `done` below. Inflecting the stem here is what keeps that from depending on
# which tenses were remembered on the day.
TOOL_HEAD = re.compile(
    r'^(?:Ran|Wrote|Runn|Run|Read|Writ|Edit|Updat|Search|List|Fetch|Launch|'
    r'Creat|Delet|Bash|Task|Think|Explor)(?:e|es|ed|ing|s)?\b')

# Inside a block, the line where the message stopped and the rendering began:
# tool output (`⎿`), the spinner, the input box, the status bar, the tip.
STOP_LINE = re.compile(r'^\s*(?:[⎿└├╰─│╭❯]|[✽✻✳✹✵✶✷*]\s|⏵|Tip:)')

# Claude Code's spinner line -- `✽ Misting… (5m 10s · ↓ 10.2k tokens)`. Taken
# whole: the word, the elapsed time and the token count. The numbers are the
# honest signal that a turn is still alive while a tool runs and no prose is
# being written, and the word -- which changes every few seconds and means
# nothing -- is the part that makes it read as someone working rather than as a
# progress bar. He asked for it by name.
SPINNER = re.compile(r'^\s*[✽✻✳✹✵✶✷*]\s+(\S+?)…?\s*\(([^)]*)\)')
# The only parts of that line worth dropping: keystrokes. `esc to interrupt`,
# `ctrl+o to expand` and friends are addressed to someone sitting at the
# terminal, and he is holding a phone. Everything else in there stays --
# including what the turn is currently doing, which is the point.
KEYSTROKE = re.compile(r'\b(?:esc|ctrl|shift|tab|enter)\b|to (?:interrupt|expand|cycle)', re.I)


def pane_text(session):
    try:
        out = subprocess.run(['tmux', 'capture-pane', '-p', '-S', '-400',
                              '-t', session],
                             capture_output=True, timeout=5)
        return out.stdout.decode('utf-8', 'replace')
    except Exception:
        return ''


def current_message(pane):
    """The assistant message being written right now, unwrapped.

    The last `BULLET` block on the pane, minus anything that is clearly not
    prose. Claude Code wraps at the pane width and indents continuations by two
    spaces, so the lines are rejoined and the indent dropped; a blank line is a
    paragraph break and is kept as one.
    """
    lines = pane.split('\n')
    start = None
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].startswith(BULLET + ' '):
            if TOOL_HEAD.match(lines[i][2:].strip()):
                continue                # a tool call; keep walking back
            start = i
            break
    if start is None:
        return ''
    body = [lines[start][2:]]
    for ln in lines[start + 1:]:
        bare = ln.strip()
        if ln.startswith(BULLET + ' ') or STOP_LINE.match(ln) or TOOL_HEAD.match(bare):
            break
        body.append(ln[2:] if ln.startswith('  ') else ln)

    # Rejoin the hard wrap: a line that continues a paragraph is glued to the
    # one before it with a space; a blank line starts a new one.
    out, para = [], []
    for ln in body:
        if ln.strip():
            para.append(ln.strip())
        elif para:
            out.append(' '.join(para)); para = []
    if para:
        out.append(' '.join(para))
    return '\n\n'.join(out).strip()


def status(pane):
    """`✽ Misting… (5m 10s · ↓ 10.2k tokens)`, or nothing.

    Claude Code's own spinner line, reproduced rather than reinterpreted.
    Rendered as Discord subtext under the message being written, so a turn that
    is thinking or running a tool shows a pulse instead of appearing to have
    stopped mid-sentence. The final correction drops it -- a finished message
    should not carry a stopwatch.
    """
    for ln in pane.split('\n'):
        m = SPINNER.match(ln)
        if not m:
            continue
        # **Claude Code's line, kept whole.** Whatever it is doing goes in the
        # same parentheses as the clock -- the running hook's status message,
        # the thinking budget -- and that is the interesting part, not noise to
        # be filtered down to numbers. Only keystroke hints come out: they are
        # addressed to a terminal he is not sitting at.
        parts = [x.strip() for x in m.group(2).split('·')]
        keep = [x for x in parts if x and not KEYSTROKE.search(x)]
        # **The word alone still beats nothing.** Mid-tool the line is sometimes
        # only `(esc to interrupt)`, and filtering that to empty would make the
        # footer disappear at exactly the moment it is earning its place.
        return ('✽ %s… (%s)' % (m.group(1).strip(), ' · '.join(keep)) if keep
                else '✽ %s…' % m.group(1).strip())
    return ''


def _unfoot(chat, tid, carrying):
    """Strip the stopwatch from a message that is no longer the live one.

    `carrying` is `(message_id, prose)` -- the text without the footer, which is
    what the message should say now that nothing about it is still moving. A
    failure here is not worth retrying: the `Stop` pass rewrites every streamed
    message with its authoritative text at the end of the turn, and that text
    never carries a footer either.
    """
    mid, prose = carrying
    if not mid or not prose:
        return
    try:
        chat._bot('/edit', {'message_id': str(mid), 'thread_id': tid,
                            'content': prose[:1900]}, timeout=15)
    except Exception:
        pass


def note(state_path, ids, turn, tid, closed=False):
    """Record which Discord messages this turn owns, in order.

    **`closed` is what stops the next turn adopting these.** A state file is
    adoptable only while its turn is still running; once the turn has ended its
    messages are finished and corrected, and writing into them again overwrites
    a reply he has already read. See the adoption guard in `main`.

    That is the whole handover to the correction pass -- **a list of ids, not a
    description of their contents.** An earlier version stored a text key per
    message and matched on it, which failed exactly as text matching always
    does: a reply opening "Hi." was filed under three characters and got posted
    a second time because nothing could match it back (thread, 18:24:32 and
    18:24:42 on 2026-09-15). One message per turn, identified by id, needs no
    matching at all.
    """
    try:
        tmp = state_path + '.tmp'
        with open(tmp, 'w') as fh:
            json.dump({'turn': turn, 'thread': tid, 'closed': bool(closed),
                       'ids': [str(i) for i in ids]}, fh)
        os.replace(tmp, state_path)
    except Exception:
        pass


def cut(text, limit):
    """Where to break `text` so the first piece fits in `limit`.

    On a paragraph if there is one, otherwise a line, otherwise a space --
    never mid-word, and never mid-sentence if a sentence boundary is anywhere
    near the end. A message that spills is going to be read as two, so the seam
    should fall where a reader would have paused anyway.
    """
    if len(text) <= limit:
        return len(text)
    window = text[:limit]
    for sep in ('\n\n', '\n', '. ', ' '):
        i = window.rfind(sep)
        if i > limit // 2:
            return i + len(sep)
    return limit


def main():
    session = os.environ.get('ZIPPER_TMUX') or ''
    tid = os.environ.get('ZIPPER_DISCORD_THREAD') or ''
    if not tid or tid.startswith('local-'):
        return 0
    if not session:
        try:
            out = subprocess.run(['tmux', 'ls', '-F', '#{session_name}'],
                                 capture_output=True, timeout=5)
            for name in out.stdout.decode().split():
                if name.endswith(tid):
                    session = name
                    break
        except Exception:
            pass
    if not session:
        return 0

    from zipper import chat
    from zipper.core import INBOX
    # One file per conversation: several of these run at once, one per live
    # Discord thread, and a shared file meant each turn's correction could
    # rewrite another thread's messages.
    state_path = os.path.join(INBOX, 'stream-%s.json' % tid)

    # **One watcher per conversation, enforced rather than assumed.** Two of
    # these on the same thread would both post and both edit, and the thread
    # would show every message twice. The lock is held for the life of the
    # process, so it also releases if this is killed -- which is how the extra
    # ones appear in the first place, by a restart racing a survivor.
    lock = open(state_path + '.lock', 'a+')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0                        # someone else is already watching

    # **A stop sentinel belongs to the turn that wrote it.** `Stop` drops
    # `<state>.stop` to tell the *previous* watcher to let go, and that watcher
    # removes it on its way out -- unless it was killed first, or crashed, and
    # then the file outlives it. The next watcher would start, see a stop it was
    # never sent, exit before its first poll and stream nothing: the whole reply
    # then arrives in one message at the end of the turn, which looks exactly
    # like the feature being off. Clearing it here is safe because no other
    # watcher for this conversation can still be running -- the file is
    # per-thread and this process is starting one.
    try:
        os.remove(state_path + '.stop')
    except OSError:
        pass

    turn = time.time()
    started = time.time()

    # **A watcher starting mid-turn adopts what is already in flight.** Normally
    # one starts per turn, on `UserPromptSubmit`, with nothing to adopt. But if
    # one is killed or crashes while a turn is running, the replacement would
    # otherwise start from nothing: it posts the accumulated text as a *new*
    # message, and the thread shows the same reply twice -- once as the stub the
    # dead watcher left and once in full. Seen on 2026-09-15 at 18:48:51 and
    # 18:49:22, the second opening with the first's sentence.
    #
    # **Adoptable means the turn is still running -- not that it ended
    # recently.** The first version of this guard used the state file's age
    # alone, on the assumption that a file younger than 90 seconds belonged to a
    # turn still in flight. It does not: a turn that *ended* 20 seconds ago
    # leaves a file just as young, and a conversation is exactly where a reply
    # arrives seconds after the last one. On 2026-09-15 a second question asked
    # ~20s after the first answer landed was streamed into the first answer's
    # messages and then laid over them by `Stop` -- 3410 characters written on
    # top of a reply he had already read, and **no new message in the thread at
    # all**. He reported it as not getting a response. This failure destroys the
    # previous turn rather than duplicating this one, which makes it the worse
    # of the two directions to get wrong.
    #
    # So the turn says when it is over: `closed` is written by the watcher on
    # its way out and by the `Stop` pass after the correction. An unclosed file
    # is a watcher that died mid-turn, which is the only case worth adopting.
    #
    # **The age check is a weak second gate and must not be mistaken for a real
    # one.** `note` rewrites this file on every post, so an active conversation
    # refreshes its mtime continuously and it effectively never ages out: on
    # 2026-09-15 a file first written at 12:06 was still inside the 90-second
    # window at 12:14, and that turn adopted ids from two turns earlier and
    # overwrote both of them. `closed` is the only thing that actually separates
    # one turn from the next. This catches a writer killed before it could mark
    # anything, and nothing else.
    ids = []            # the Discord messages this turn owns, in order
    try:
        if time.time() - os.path.getmtime(state_path) < 90:
            prev = json.load(open(state_path))
            if (str(prev.get('thread') or '') == str(tid)
                    and not prev.get('closed')):
                ids = [str(i) for i in (prev.get('ids') or [])]
    except Exception:
        ids = []
    done = []           # prose blocks already finished on the pane
    head = 0            # how much of the turn's text earlier messages hold
    shown = ''          # the block being written, as last captured
    last_body = ''
    last_edit = 0.0
    note(state_path, ids, turn, tid)

    while time.time() - started < MAX_IDLE:
        if os.path.exists(state_path + '.stop'):
            break
        pane = pane_text(session)
        text = current_message(pane)
        foot = status(pane)
        now = time.time()

        # **One message per turn, not one per thing Claude says.** A turn is a
        # single answer from his side -- he asked a question and got a reply --
        # and the paragraphs Claude writes either side of a tool call are that
        # reply, not separate utterances. Keeping them in one growing message is
        # also what makes the correction trivial: there is nothing to match.
        if text:
            if shown and not (text.startswith(shown[:30]) or shown.startswith(text[:30])):
                # **A block boundary is a guess, and a guess that flaps must not
                # cost text.** The boundary is decided by comparing the newest
                # prose on a live, redrawing pane against the last -- so
                # anything that alternates (furniture `current_message` failed
                # to recognise, a pane that scrolls a block back into view)
                # reads as block, other block, first block again, and each
                # crossing appended a *permanent* copy to `done`. The body grew
                # without bound, spilled past `LIMIT`, and posted a fresh
                # message every time it did: on 2026-09-15 one turn became
                # twenty messages alternating between the same two paragraphs.
                #
                # Appending only what is not already held makes the flap cost
                # nothing. The real fix for any individual case is to recognise
                # the furniture, but that is a list of things someone thought
                # of, and this is the floor under it.
                if shown not in done:
                    done.append(shown)   # a new block began; the old one is final
            shown = text
        full = '\n\n'.join(done + ([shown] if shown else []))

        body = full[head:]
        body_now = (body + ('\n-# ' + foot if foot else '')).strip()
        if not body_now:
            time.sleep(POLL)
            continue

        # New words are worth an edit a second; a ticking clock under unchanged
        # text is not -- that would spend the channel's whole edit budget on a
        # stopwatch while a five-minute tool ran.
        due = EDIT_EVERY if body != last_body else FOOT_EVERY
        if body_now == last_body or now - last_edit < due:
            time.sleep(POLL)
            continue

        try:
            # **Spill only at Discord's limit.** 2000 characters is the only
            # reason to start a second message, so it is the only thing that
            # does. The seam is placed on a paragraph break by `cut`.
            if len(body_now) > LIMIT and ids:
                keep = cut(body, LIMIT - 40)
                chat._bot('/edit', {'message_id': ids[-1], 'thread_id': tid,
                                    'content': body[:keep].strip()}, timeout=15)
                head += keep
                body = full[head:]
                body_now = (body + ('\n-# ' + foot if foot else '')).strip()
                r = chat._bot('/send', {'message': body_now[:LIMIT],
                                        'thread_id': tid}, timeout=15)
                if r.get('message_id'):
                    ids.append(str(r['message_id']))
                    note(state_path, ids, turn, tid)
            elif ids:
                chat._bot('/edit', {'message_id': ids[-1], 'thread_id': tid,
                                    'content': body_now[:LIMIT]}, timeout=15)
            else:
                # The first post of the turn. Often this is the footer alone:
                # the model is still reading the question and no words exist
                # yet, and saying so within a second is the point -- the same
                # message then grows into the reply.
                r = chat._bot('/send', {'message': body_now[:LIMIT],
                                        'thread_id': tid}, timeout=15)
                if r.get('message_id'):
                    ids.append(str(r['message_id']))
                    note(state_path, ids, turn, tid)
            last_body, last_edit = body, now
        except Exception:
            # A Discord hiccup costs one frame of a live stream and nothing
            # else: the `Stop` pass writes the true text at the end regardless.
            last_edit = now
        time.sleep(POLL)

    # The turn is over, so the clock comes off whatever is wearing it. The
    # correction pass would also remove it, but only if it runs.
    if ids:
        _unfoot(chat, tid, (ids[-1], full[head:].strip()))

    # **Leaving is what closes the turn.** The ids stay on disk -- the `Stop`
    # correction still needs them -- but they are now finished messages, and the
    # next watcher must post rather than adopt.
    note(state_path, ids, turn, tid, closed=True)

    try:
        os.remove(state_path + '.stop')
    except Exception:
        pass
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
