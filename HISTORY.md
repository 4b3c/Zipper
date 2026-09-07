# History

Changes that are finished. Nothing here describes how Zipper works today — that is
`README.md`, `zipper/README.md` and `CLAUDE.md`. This file exists so that the reasoning
behind a removal survives the removal, without the code having to carry a description of
something that is no longer there.

**What belongs here:** a decision that deleted or replaced something, and what it cost to
learn. **What does not:** rationale for the current shape of the code. That stays at the
call site, stated in the present tense, because someone editing that line needs it.

---

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
