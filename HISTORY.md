# History

Changes that are finished. Nothing here describes how Zipper works today — that is
`README.md`, `zipper/README.md` and `CLAUDE.md`. This file exists so that the reasoning
behind a removal survives the removal, without the code having to carry a description of
something that is no longer there.

**What belongs here:** a decision that deleted or replaced something, and what it cost to
learn. **What does not:** rationale for the current shape of the code. That stays at the
call site, stated in the present tense, because someone editing that line needs it.

---

## 2026-09-15 — streaming replies to Discord, removed the same day

For about two hours `hooks/stream_watch.py` posted a Discord reply as it was written,
editing the message in place as more arrived, with `forward_reply.py` on `PostToolUse`
forwarding each block and reconciling at `Stop`. It is gone. `Stop` alone posts the
finished turn, which is what it did before.

It was removed because Abram did not want it, not because it could not be made to work —
by the end it did work. That is the part worth recording: the feature shipped and then
broke five times in one morning, once per concurrency scenario nobody had walked it
through, and each fix was sound. Several conversations sharing one state file. A turn
that had ended being read as still running. A watcher and a hook disagreeing about
whether a message had been delivered, leaving a truncated reply standing as the final
answer. The cost was not the streaming; it was that live editing of a message Discord
keeps no history of has no undo, so every bug destroyed something instead of merely
showing it late.

What survives the removal is the guidance it earned, kept in `CLAUDE.md` because it is
not about Discord: walk every change through several conversations at once, messages
arriving mid-turn, a process killed at any line, and two components answering the same
question differently. Also **overwriting is worse than duplicating**, and **a freshness
check fed by the thing it is checking measures nothing** — the watcher rewrote its own
state file on every post, so the mtime it was gated on never aged out.

The two `~/.claude/settings.json` hooks it needed (`PostToolUse`, `UserPromptSubmit`)
were unwired at the same time. A mid-turn hook is the shape to be suspicious of: it is
the one that cannot be tested without a live conversation to break.

## 2026-09-15 — last-block-only forwarding

For a day `hooks/forward_reply.py` posted exactly one message per turn: the assistant text
block whose row did not stop to call a tool. Earlier blocks were classed as preamble and
deliberately dropped, so the Discord thread read as clean question-and-answer while the
working narration stayed in the terminal.

That rule was introduced on 2026-09-14 to fix a real ordering bug — a 95-character "let me
check" forwarded *instead of* the 3057-character answer behind it — and it fixed it by
suppression, which was the wrong axis. The cost: a phone showed nothing while a long turn
ran, and nothing at all for a turn that ended on a tool call, since no closing row was ever
written. A turn could be worked and still deliver silence.

Replaced by forwarding every block in order, deduped per message uuid. A preamble cannot
displace an answer when the two are not competing for one slot.

Forwarding every block, but still only on `Stop`, lasted about ten minutes in use: the whole
turn arrived in one burst at the end, which is the same silence as before with a longer
message at the end of it. The hook now also runs on `PostToolUse`, so narration reaches the
thread while the turn is still working.

## 2026-09-15 — `/stream`, the reveal that made delivery slower

A `bot/server.py` endpoint that posted a message and then edited it forward a few words at a
time, on a one-second tick, so a reply appeared to type itself. It lived about half an hour.

It was built to answer "would streaming look good in Discord", and it answered a different
question, because **the text was already complete before the first word was posted.** Nothing
upstream streamed: hooks hand over finished messages. So the effect was a typewriter playing
back a finished reply — six seconds to deliver something that had been ready at zero. Strictly
worse latency, no information sooner, and it spent the channel's whole edit budget doing it.

The measurements it produced are the part worth keeping, and they moved to
`hooks/stream_watch.py`: editing once per 0.3s asks ~3.3 edits/s against a limit of about 5
per 5s, discord.py absorbs the 429 by sleeping *inside* the bot, and the visible result is a
stream that freezes for five seconds and lurches — 51 word-by-word edits took 57.9s against a
requested 15.3s, p90 latency 4.51s. At ~1 edit/s the same text took 15.4s, p90 0.99s.

Replaced by reading the tmux pane, which is the only place the words exist before the turn
ends.

## 2026-09-07 — the removal archaeology moved here

The comments this file opens with were, until today, in `zipper/web/conv.py`,
`zipper/web/http.py` and `bot/client.py`. They described the Path A removal (below) in
three places, in the past tense, next to code that had no trace of it left. Each call site
keeps a one-line invariant in the present tense instead.

## 2026-09-06 — Path A: the dashboard's own fixed terminal

Until this date there were **two kinds of conversation**, side by side:

- **Path A** — one fixed conversation owned by the dashboard: tmux session `zipper`, ttyd on
  a fixed port 8801, held in a module-level `TERM` dict in `serve.py`.
- **Path B** — one conversation per Discord thread, owned by `conversations.py`, ttyd on
  8810-8829.

Path A predated threads and was simply never removed when they arrived. It was deleted
whole: the `TERM` dict, `serve.py`'s local `_t()` anchoring helper, and
`conv.deliver_to_claude` (which took a Discord message with no thread and pasted it into
the fixed terminal, starting it if cold).

**Why keeping both cost more than the duplication.** tmux resolves `-t` by prefix, so a bare
`-t zipper` matched `zipper-<any thread>`. Both files carried anchoring workarounds for that
collision, and it still produced: a dead terminal reported as alive, a reaper aimed at
somebody else's pane, and a bound row that read as live forever and blocked `zipper commit`
on every pass. One naming scheme makes that unrepresentable rather than defended against
twice.

**And Path A could not answer.** Every conversation is keyed on a Discord thread, which is
what reply forwarding posts to. A conversation without one is a conversation whose answers
cannot get back out.

The same reasoning removed the fallback in `bot/client.py`: when thread creation fails the
bot now says so, where it used to fall back to the channel id and open a session with
nowhere to reply to.

## 2026-09-06 — `_tagged()`, and eight replies posted to the wrong place

`conv.py` had a `_tagged()` that prefixed `[via discord]` to a delivered message and appended
an instruction to reply by running `zipper discord send`. The reasoning was that the session
had to know where a message came from, because the reply went back the same way.

Both halves became wrong when the `Stop` hook (`hooks/forward_reply.py`) took over
forwarding. The session neither sends nor needs to know: provenance is recorded at delivery
(`conversations.note_delivery`) and read back from the registry, where it is a fact the bot
can look up rather than a fact sitting in the context window forever.

Removing it also removed a real failure. The tag only ever landed on the message that
*started* a conversation, so a session that began on a phone and continued at the keyboard
still looked like Discord — and on 2026-09-06 that produced **eight replies posted to
Discord for messages typed at the terminal**. Routing is per-turn now, and nothing about it
is inferred from the prompt.

## 2026-09-06 — the second thing called the queue

There were two: `Inbox/feed.json` (the real one), and a `Meta/Queue.md` that diffed the
notes against a hand-rolled baseline in `Inbox/state.json`, reset by `zipper queue`.
Clearing the wrong one was silent and cost an hour on 2026-09-03. Git already answers what
that baseline answered.

`zipper queue` still runs and prints a deprecation line; it resets nothing. `runqueue.py`
unlinked a leftover `state.json` on every run for a year of hourly fetches — that cleanup
was removed on 2026-09-07.

## 2026-09-03 — the laptop era ended

Zipper ran on his Mac: a `Brain.app` launcher, a `Scripts/` directory inside the vault, ttyd
and tmux from Homebrew, and reconciliation something `launchd` could drive. All of it now
runs on the Debian VPS under systemd.

Two scars remain in the code on purpose:

- `zipper/web/base.py` and `claude-session.sh` prepend `~/.local/bin`, `/opt/homebrew/bin`
  and `/usr/local/bin` to `PATH`. Finder launched an app with
  `/usr/bin:/bin:/usr/sbin:/sbin`, where none of `ttyd`, `tmux`, `gh` or `claude` existed, so
  every shell-out failed silently — including `gh auth token`, which meant a launch-time
  fetch quietly wrote public-repo data over the notes. The Homebrew path is dead weight on
  Debian; the habit of not trusting an inherited `PATH` is not.
- `core.SKIP_DIRS` still lists `Scripts`. The directory was deleted from the vault on
  2026-09-03 and the entry now excludes nothing.

`ZIPPER_VAULT` exists because of this move: the code no longer has to live inside the data.
Its fallback — the parent directory — is the old `<vault>/Scripts/` layout.

## 2026-09-03 — `zipper-web` stopped killing its own terminals

The unit had no `KillMode`, so systemd's default control-group kill took ttyd and the tmux
server down with the service on every restart. From the browser that looked like the
terminal disconnecting at random. `KillMode=process` fixed it; conversations now survive a
`systemctl restart zipper-web`. Verify with `tmux ls` — the same creation time means the
conversation lived.

The same change added `Environment=HOME=/root`, so a service-started session reads
`~/.gitconfig` and the stored GitHub credentials instead of committing as `root@<hostname>`.
