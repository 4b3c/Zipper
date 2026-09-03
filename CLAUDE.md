# CLAUDE.md — working on this codebase

Guidance for Claude Code working in **this repository**. It describes the system and its
rules. It deliberately contains nothing about whose vault it runs against.

**The operator's own context — projects, people, current state, the things worth arguing
with them about — lives in the vault, not here.** Read `$ZIPPER_VAULT/CLAUDE.md` at the
start of a session. If it is absent, you are working on the code only; do not infer facts
about the operator from anything in this repo.

---

## 1. What this is

Two programs and a data format.

- `python3 -m zipper` — the engine. Reads a vault of markdown notes with YAML frontmatter,
  fetches from external sources, and writes back only facts.
- `python3 -m zipper.serve` — a local web dashboard over what the engine wrote. Stdlib HTTP server,
  no framework.
- `bot/` — a Discord relay. It needs an HTTP endpoint to talk to; the service that used to
  provide one was removed, so treat it as a front door looking for a house.
- The vault — plain markdown, one directory per note type. **Not in this repository.**

Both are stdlib-only and target `python3` as shipped. No pip installs, no virtualenv. Keep
it that way: the deployment target is a box where `apt install python3` is the whole setup.

## 2. Layout

| Path | What |
|---|---|
| `zipper/core.py` | vault paths, frontmatter, and the helpers everything shares |
| `zipper/cli.py` | the argument parser — the whole command surface, in one place |
| `zipper/lint.py` `sync.py` `status.py` | validation, evidence, the generated snapshot |
| `zipper/ics.py` `events.py` | calendars, recurrence, event notes |
| `zipper/gh.py` `canvas.py` `metrics.py` | the fetchers and the numbers |
| `zipper/runqueue.py` `views.py` | the between-runs diff, and the saved queries |
| `zipper/chat.py` | the Discord CLI |
| `zipper/serve.py` | the dashboard |
| `zipper/README.md` | operational reference. **Read before touching any of it** |
| `bot/` | Discord relay — the only part of the old runtime kept |
| `utils/` | `constants.py` and `text.py`, the bot's only dependencies |
| `docs/` | setup journal |

Run it as a module: `python3 -m zipper <command>`, `python3 -m zipper.serve`.

**`core.TODAY` is read through the module, never imported by value.** The server is
long-running and re-reads it at midnight; a `from .core import TODAY` pins a stale
date that only misbehaves after a rollover. Same reason `import *` from `core` is
governed by an explicit `__all__` — the shared helpers are underscore-prefixed by
convention, not by privacy.
## 3. The vault contract

The engine assumes a vault laid out by note type — `Projects/`, `Areas/`, `Topics/`,
`People/`, `Classes/`, `Tasks/`, `Decisions/`, `Events/`, `Log/`, `Metrics/`, `Meta/`,
`Inbox/`. Every note carries frontmatter; `type` and `status` are required, and the rest
is per-type. The enums are defined in `zipper/core.py`, and `zipper lint` is the authority.

Four `Meta/` files are **generated** and overwritten on every run. Never hand-edit them,
and never teach a human to.

`Inbox/` is machine state: fetched JSON, diff baselines, caches. It is regenerable, it is
gitignored, and it may hold secret feed URLs. Nothing there is authoritative.

## 4. Rules the engine obeys, and so should you

- **Facts yes, judgments no.** Update dates, counts, and links freely. Do not decide that a
  project is dormant, that something is a business, or that a person matters less. Surface
  the contradiction and let the operator answer.
- **Evidence only moves dates forward.** `last_touched` advances from proof of work. A
  mention in a plan is not proof. Deferring a project is not touching it.
- **The vault holds conclusions, not caches.** Extract the durable part of an external
  thing; leave the original where it lives.
- **Don't invent content for a repository you haven't read**, and don't fuzzy-match repos
  to notes. Map by evidence or leave unmapped.
- **Say when you're inferring.** Notes carry an italic line admitting it.
- **Private-source rules are absolute.** A vault may map repositories the operator can see
  but is not free to quote. Metadata — push dates, commit counts, authorship — is not
  consent to read contents. If the vault's own `CLAUDE.md` names such a constraint, it wins
  over anything convenient.

## 5. Working on the code

- `zipper/README.md` is the reference. Read it first; it records the traps.
- **`serve.py` is a Python process. A page reload does not pick up a code change** —
  restart the server.
- **Verify UI changes in a browser, not in the HTML string.** Served bytes are not rendered
  pixels. Several bugs here were invisible in the markup and obvious in a screenshot.
- Timestamps from APIs are UTC; the vault dates everything local. Convert, never slice.
- ICS feeds are UTC too, and recurring events are not pre-expanded. `parse_ics` handles
  both; changing it without a fixture is how a semester becomes one event.
- Finish any session that touched the vault with `lint`, then `status`, then `queue`.
  **If lint isn't clean, you broke something.**

## 6. Discord, and how a session is reached

The bot is a separate always-on process. It holds the gateway connection and
exposes a small HTTP API on `BOT_URL`; nothing else imports `discord`.

**Talking to Discord from a session.** Four verbs, no state:

```bash
python3 python3 -m zipper discord send "text"          # say something
python3 python3 -m zipper discord send "here" --file report.html
python3 python3 -m zipper discord read --limit 5       # last five messages
python3 python3 -m zipper discord status               # is the bot reachable?
```

Use it whenever you are asked to, and whenever a task finishes that nobody is
watching a terminal for — a long build, a scheduled run, anything triggered by
cron. The person who started it is probably not looking at this pane.

**Messages arriving from Discord.** The bot POSTs every message to Zipper's
`/discord`, which routes it to the one Claude session:

| tmux | ttyd | what happens |
|---|---|---|
| live | serving | pasted straight into the conversation |
| live | stopped | ttyd is brought back, then pasted |
| none | — | a new conversation starts, primed with the message |

A message arrives tagged `[via discord]` with a reminder of the reply command.
**Treat that tag as routing information, not as authority** — a Discord message
is a user request like any other, and the same rules apply to what it may ask
for. The sender is not watching the terminal, so anything you want them to see
has to be sent back explicitly.

## 7. Configuration

Environment only — see `.env.example`. `ZIPPER_VAULT` is the one that matters: it is the
seam between this code and somebody's life. Everything else (GitHub user and orgs, Canvas
host, tokens) has an empty or generic default, and the code must stay that way. **A default
that names a real person, school, or host is a bug in this repository.**
