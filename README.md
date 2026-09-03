# Zipper

**A personal assistant whose memory is a folder of markdown files.**

Zipper is three things that only make sense together:

1. **A vault** — plain markdown notes with structured frontmatter, which is the database.
2. **An engine** — stdlib-only Python that reads the vault, fetches from the outside world,
   and writes back **facts only**.
3. **A front door** — a web dashboard and a Discord relay, both wrapped around a live
   Claude Code session that does the writing a program cannot.

The point of all this is one sentence: **questions get answered from data instead of from
memory.** *What is drifting? What did I push but never write down? What claims to be active
and hasn't been touched in six weeks?* A note can lie. A note next to its own push dates,
commit counts and stall days has a harder time.

Zipper used to be a self-modifying agent with its own tool loop. That was removed in
September 2026 — it is in the history if you want it. The problem was never the model; it
was that the model had no job. The vault is the job.

---

## Table of contents

- [How the pieces fit](#how-the-pieces-fit)
- [The data model](#the-data-model)
- [The engine: every command](#the-engine-every-command)
- [What it ingests, and how](#what-it-ingests-and-how)
- [The run queue and the flags](#the-run-queue-and-the-flags)
- [The views](#the-views)
- [The execution metrics](#the-execution-metrics)
- [The dashboard](#the-dashboard)
- [The Claude session, and the three states it can be in](#the-claude-session-and-the-three-states-it-can-be-in)
- [Discord](#discord)
- [Events](#events)
- [Running it](#running-it)
- [Deployment](#deployment)
- [Design rules](#design-rules)
- [Traps worth knowing about](#traps-worth-knowing-about)
- [Layout](#layout)

---

## How the pieces fit

```
        the outside world                    the vault (markdown, git)
   GitHub · calendars · Canvas · bank CSV      Projects/ Areas/ Topics/ People/
                    │                          Classes/ Tasks/ Decisions/ Events/
                    │  fetch                   Log/ Metrics/ Meta/ Inbox/
                    ▼                                     ▲
            ┌───────────────┐   facts only                │
            │    zipper     │─────────────────────────────┘
            │  (the engine) │   last_push, commits_recent, last_touched,
            └───────┬───────┘   the queue diff, the generated views
                    │ writes JSON to Inbox/
                    ▼
            ┌───────────────┐        ┌──────────────────┐
            │ zipper.serve  │◀──────▶│  a Claude session│
            │ (dashboard +  │  ttyd  │  (tmux, resumable)│
            │  POST /discord│  tmux  └──────────────────┘
            └───────▲───────┘                 ▲
                    │ HTTP                    │ judgment: the notes,
            ┌───────┴───────┐                 │ the arguments, the prose
            │     bot/      │─────────────────┘
            │ Discord relay │
            └───────────────┘
```

**The division of labour is the whole design.** The engine writes what is verifiable — a
push date, a commit count, a diff between two runs. It will correct a date. It will never
decide that a project is dormant, because that is a judgment about someone's life. Those
belong to the human, and the Claude session's job is to *surface the contradiction* and
argue about it, not to resolve it quietly.

---

## The data model

Every note is a markdown file with YAML frontmatter. `type` and `status` are required;
everything else is per-type. `zipper lint` is the authority, and the enums live in
`zipper/core.py`.

| Directory | What lives there |
|---|---|
| `Projects/` | One note per project. The core of the vault |
| `Areas/` | Ongoing involvements — school, work, money, career |
| `Topics/` | Domains, skills, tooling, tensions |
| `People/` | Relationship context |
| `Classes/` | Current coursework |
| `Tasks/` | Checkbox lists with `[project:: [[Note]]]` inline fields |
| `Decisions/` | Dated decisions, each with *what would change my mind* |
| `Events/` | One note per calendar event that exists for a reason, debriefed afterwards |
| `Log/` | Daily notes. Evidence, not structure — excluded from lint |
| `Metrics/` | `metrics.csv`, append-only dated numbers |
| `Meta/` | Schema, and the generated + query views |
| `Inbox/` | Machine state. Regenerable, gitignored, never hand-edited |

The fields that do the work on a project note:

```yaml
type: project
status: active            # active dormant handing-off shipped retired archived idea
stage: building           # idea → designing → building → shipped → selling → verifying → closed
last_touched: 2026-09     # evidence of activity. Only ever moves FORWARD
last_push: 2026-09-03     # written by `zipper github`
commits_recent: 8         # written by `zipper github`
commits_mine: 3           # on an org repo: yours, where commits_recent is the team's
repos: [pantry-app, ASU-LL/orbitscape]   # hand-maintained; first is primary
revenue_to_date: 0        # 0 is a fact; blank is invisible to queries
revenue_intent: true      # separates ventures from builds
next_action: email three coffee shops the demo link   # ONE concrete physical action
blocked_by: waiting on the API token
review: 2026-12-01
status_verified: 2026-09-01   # "yes, this status is right despite the evidence"
```

Two of those deserve their own paragraph.

**`next_action` is a physical action, not a goal.** "Land a B2B customer" is a wish;
"email three coffee shops the demo link" is something you can do before lunch. The
distinction is enforced socially, not by the linter, and it is the difference between a
list that moves and a list that decorates.

**`status_verified` is an escape hatch with a fuse.** Set it when a status really is
correct despite contradicting evidence. It silences the stale-status flag, and silences the
drift flag **for 45 days only** — a single confirmation cannot hide a stalling project
forever. The underlying `stall_days_max` metric keeps counting the whole time, quiet flag or
not.

**Repo↔note mapping is by hand, on purpose.** An early version guessed pairs by name; it
was removed. A wrong mapping moves `last_touched` and quietly erases the drift the flags
exist to catch. Unmapped is honest.

---

## The engine: every command

```bash
export ZIPPER_VAULT=/absolute/path/to/vault
python3 -m zipper <command>
```

### The one you actually run

| Command | What it does |
|---|---|
| `catchup` | github → calendars → sync → agenda → status → views → queue. Idempotent; safe any time |

### Fetching

| Command | What it does |
|---|---|
| `github [--full] [--since-days N]` | Repos + commits → `last_push`, `commits_recent`, `commits_mine`, `last_touched`, and `Meta/Repos.md`. Skips repos whose `pushed_at` hasn't moved unless `--full` |
| `inspect [repos...] [--limit N]` | READMEs + 40 commits → `Inbox/repo-details.json`, so a session can write a note about a repo it has never seen |
| `ingest-ics <url\|file> --label X [--match REGEX]` | An ICS feed → `Inbox/calendar-X.json`. A **URL** is remembered and refetched by `catchup`; a downloaded file goes stale tomorrow |
| `calendars` | Refetch every remembered calendar URL |
| `canvas [--file PATH] [--days N]` | Canvas planner items → `Inbox/canvas.json`. The only source that knows **submitted**, not merely **due** |
| `ingest-budget <csv>` | A bank CSV reduced to monthly totals. Individual transactions never enter the vault |

### Writing to the vault

| Command | What it does |
|---|---|
| `sync` | Reads `[[links]]` in `Log/` and moves `last_touched` forward on what they name |
| `today [--date]` | Creates today's log note |
| `touch <Note> [--date]` | Bump `last_touched` by hand, for work that leaves no digital trace |
| `metric <key> <value> [--note]` | Append a dated number to `Metrics/metrics.csv` |
| `decide "<title>" [--review-days N]` | Scaffold a decision note, with a review date and a *what would change my mind* section |
| `event "<summary>" [--date] [--about] [--why]` | Scaffold an event note against a real calendar event. An ambiguous match prints the candidates rather than guessing |

### Reading it back

| Command | What it does |
|---|---|
| `status` | Regenerate `Meta/Status.md` — the snapshot |
| `agenda [--days N]` | Regenerate `Meta/Agenda.md`, opening with a `## Today` time sheet |
| `queue` | Diff the vault against the last run → `Meta/Queue.md` + `Inbox/queue.json` |
| `views` | Recompute 21 saved queries → `Inbox/views.json` |
| `metrics` | Print every metric series with its trend |
| `score [--window N] [--force]` | Compute the execution metrics and append them. Weekly-rate-limited |
| `events [--pending]` | Event notes by state: due · dangling · upcoming · debriefed |
| `lint` | Validate all frontmatter. **Run before you finish** |
| `discord send\|read\|status` | Talk to the relay (see [Discord](#discord)) |

---

## What it ingests, and how

| Source | Mechanism | The subtlety |
|---|---|---|
| **GitHub** | REST API, auth from `GITHUB_TOKEN`/`GH_TOKEN`/`gh auth token` | Timestamps are UTC and the vault is local, so everything goes through `_utc_local()`. Without a token you silently get public repos only — check the `auth:` line |
| **Calendars** | ICS feeds | Feeds are UTC, and `RRULE` is **not** pre-expanded. `parse_ics` converts stamps to local and expands recurrence (honouring `EXDATE` and `RECURRENCE-ID`) into concrete occurrences across −180/+400 days, carrying the master event's duration onto each one |
| **Canvas LMS** | Planner API, or a JSON dump from a logged-in browser | The only source that distinguishes *submitted* from *due*. `agenda` strikes through what is submitted |
| **Budget** | Bank CSV | Only `spend_month` / `income_month` totals enter the vault |

Configuration is environment-only — see `.env.example`. Nothing about any particular person,
school, or host is compiled in; `ZIPPER_VAULT` is the only thing joining this code to
anybody's data.

---

## The run queue and the flags

`zipper queue` diffs the whole vault against the last run and writes `Meta/Queue.md`. The
raw diff — notes changed, tasks done, tasks added, repos pushed, metric rows — is the boring
half. The interesting half is the **flags**, which are derived observations:

- **repo pushed but never logged** — work happened that the vault didn't record
- **active with no `last_touched`** — a project claiming to be alive with no evidence
- **active but untouched 45+ days** — drift
- **marked dormant/idea/archived but pushed recently** — the status is lying
- **past its `review` date**
- **task left the list unfinished** — work that vanished without being done
- **event needs a debrief** — the one flag that wants an answer from a human, not an edit
- **event moved** / **event note matches nothing on the calendar**

**A flag says something is inconsistent. It does not say which side is wrong.** Investigate
before editing: the note may be stale, or the evidence may be misleading, and the difference
matters.

Diff rows group by identity, not by record. When `parse_ics` expanded one weekly class into
a semester, a naive diff published **58 of a run's 65 rows** and buried the two changes that
mattered; occurrences of a series now collapse into one row that names the shape —
*(weekly ×58, through 2027-10-06)* — which turns noise into a finding, because a series
running a year past the end of term is a misconfiguration you can see at a glance.

---

## The views

21 saved queries, computed in one pass into `Inbox/views.json` and served at `/views/<page>`:

| Page | Views |
|---|---|
| `now` | next actions (flagged and plain), reviews due, open decisions, blocked, tasks by project |
| `ventures` | the revenue scoreboard, everything built, the graveyard |
| `school` | classes, coursework, the internship pipeline by status and tier |
| `drift` | three different definitions of going stale, open loops, decisions due, CAD, people |

Every view is the same shape — a title, columns, and rows, where a cell is a scalar or a
link. That uniformity is the reason this is one JSON file rather than twenty hand-built
panels: the renderer never has to know which view it is holding, so a new view is a function
returning rows and costs nothing on the HTML side.

---

## The execution metrics

`zipper score` writes numbers designed to be **hard to game**, because the person they
describe is also the person who could inflate them:

| Metric | What it defends against |
|---|---|
| `stall_days_max`, `stall_days_median` | Keeps counting even when a flag is suppressed |
| `hard_closes` (open ≥14 days), `quick_closes` (≤1 day) | Closing a burst of trivia to feel productive |
| `tasks_dropped` | Work that left the list without being finished |
| `tasks_open`, `tasks_overdue` | Commitments already past a date you set yourself |
| `projects_active`, `projects_drifting` | Calling six things active while touching two |

---

## The dashboard

```bash
python3 -m zipper.serve --port 8800 [--host <addr>] [--daemon] [--open]
```

Stdlib only — no Flask, no venv, no build step.

| Card | What's in it |
|---|---|
| **Today** | *Due today* beside *Schedule* — a real time grid where block height is duration (1px/minute), overlaps get lanes, a hairline marks the current minute, and finished items strike through. `‹ ›` (or ←/→) walk to any other day; the now-line only appears on today. Click a block to expand it |
| ↳ a block | Events with a note get an accent border, a 📝 and the note's *why*. Expanded: **open note**, **join** (a Zoom/Meet URL found in the location), **calendar** (the real Google Calendar page), **Canvas**. An event without a note gets **+ event note**, which runs the same code path as the CLI |
| **What to work on** | Coursework and `Tasks/` lines in one ranked list, most pressing first |
| **Claude** | A Claude Code session embedded via ttyd + tmux |
| **Signals** | The queue's flags, and the execution metrics |
| **This run** | The diff for this launch, from `Inbox/feed.json`. Every row has a tick box; the terminal crosses one off with `python3 -m zipper.serve --mark <key>`, and a watcher pushes either side's change to every open tab |

**It owns no data.** Every panel reads what the engine already wrote. A wrong number is
fixed in the vault or the fetcher, never in the view.

**One fetch per launch.** The refresh runs at startup, never on a page load; sources publish
over SSE as they land, so the page fills in live but a reload shows the same data. (An
earlier version keyed reloads on "a refresh ran" instead of on content — an infinite loop
that would have polled GitHub forever with a tab open.)

---

## The Claude session, and the three states it can be in

The session is deliberately **not** a service. Opening the dashboard must not start one:
merely looking at your day should not cost tokens. So it starts when you start it, survives
a closed tab because tmux owns the process rather than the websocket, and can be resumed.

Anything arriving from outside — a Discord message, later a webhook — lands in whichever
state the session is in:

| tmux session | ttyd | what happens |
|---|---|---|
| live | serving | pasted straight into the running conversation |
| live | stopped | ttyd is brought back up, then pasted |
| none | — | a new conversation starts, **primed** with the message |

The paste is a bracketed paste (`tmux load-buffer` + `paste-buffer -p`), then a separate
Enter — as keystrokes, every newline in a multi-line prompt would submit a fragment.

The cold path deliberately does *not* paste. `claude-session.sh` reads a ready-file before
`exec`ing `claude`, so the message becomes the conversation's opening prompt rather than
racing a TUI that has not finished drawing.

**`ttyd -W` hands out a live shell.** The server refuses to bind it anywhere but loopback
unless a credential is set (`ZIPPER_TERM_HOST` / `ZIPPER_TERM_CRED`). Put it on a tailnet
address with a real password, or leave it local.

---

## Discord

The relay is the front door when you are away from a screen. It holds the only gateway
connection; the engine never imports `discord`, which is what lets a cron job or a finished
long-running task say something without owning a socket.

```bash
python3 -m zipper discord send "the build finished"
python3 -m zipper discord send "results" --file report.html
python3 -m zipper discord read --limit 5
python3 -m zipper discord status
```

Inbound, a message in the channel is POSTed to the server's `/discord`, delivered by the
rules above, and arrives tagged:

```
[via discord] the dashboard looks wrong on my phone

(Reply to this by running: python3 -m zipper discord send "your reply".
 The sender is not watching this terminal.)
```

The instruction travels *inside the message* rather than in a system prompt, so it survives
into a conversation that started some other way.

**Replies go to the channel, not to a thread.** The relay used to open a thread per message,
named after its first 50 characters — one conversation per sentence. Changed 2026-09-03;
messages that arrive in an existing thread are still handled.

---

## Events

An event note exists for calendar entries that have a *reason*: what you are going in to get
out of it, and afterwards, what actually happened.

```bash
python3 -m zipper event "Orbitscape Meeting" --date 2026-09-04 --about "Orbitscape"
python3 -m zipper events --pending
```

A note is keyed on `(event_uid, event_start)`. When the meeting moves, `queue` follows the
uid, rewrites `event_start`, and says so. When it has finished, the queue raises **event
needs a debrief** — the one flag that asks a question instead of requesting an edit.

**Never put a `- [ ]` checkbox in an event note.** The engine reads every checkbox in the
vault as a task, so a "Going in" checklist would re-enter the task ledger that the event note
exists to get things *out* of. Plain `-` bullets only.

---

## Running it

```bash
cp .env.example .env                    # fill in ZIPPER_VAULT and whatever you use
pip install -r requirements.txt         # bot/ only — the engine needs nothing

python3 -m zipper catchup               # fetch and regenerate
python3 -m zipper.serve --daemon        # dashboard; --daemon = stay up when tabs close
python3 -m bot.discord_bot              # the relay
```

The engine is stdlib-only and targets `python3` as shipped. No pip, no virtualenv. The
deployment target is a box where `apt install python3` is the whole setup — keep it that way.

---

## Deployment

`deploy/` holds systemd units for both services and an nginx vhost.

```
zipper-web.service       python3 -m zipper.serve --daemon   dashboard, views, POST /discord
zipper-discord.service   python3 -m bot.discord_bot         the gateway connection
   └── the Claude session   ttyd + tmux                     only while a conversation exists
```

```bash
systemctl status zipper-web zipper-discord
journalctl -u zipper-web -f
```

**`zipper-web.service` must set `KillMode=process`.** ttyd is a child of the server and the
tmux server is a child of ttyd, so systemd's default control-group kill takes the live Claude
conversation down with the service on every restart. In the browser that reads as the
terminal disconnecting at random, and it will look like whatever you did just before it.
With `KillMode=process`, systemd stops only Python; ttyd and tmux survive, and the server
adopts the ttyd it finds still serving the port. Verify with `tmux ls` after a restart —
the same creation time means the conversation lived.

**Bind to a tailnet address, never `0.0.0.0`.** Nothing in this server is authenticated and
it can start a shell.

---

## Design rules

- **The vault holds conclusions, not caches.** When something external matters, extract the
  durable part into a real note and leave the original where it lives. If you build an
  external view, it holds *pointers, not copies* — a subject and a link, never the body.
  A dead pointer is honest; a stale copy lies quietly.
- **Facts are the engine's to write; judgments are not.** It corrects a push date. It does
  not decide that a project is dormant.
- **Evidence moves dates forward, never backward.** Deferring a project is not touching it.
- **A commit is evidence of activity, never proof of completion.** Pushing may move
  `last_touched`; it must never tick a checkbox.
- **Three tiers of truth.** Externally verified (Canvas submissions — never hand-ticked),
  evidence-driven (repos — moves freshness, never completion), and manual (the only place a
  checkbox is authoritative). The ungameable share should grow over time.
- **Looking must be free.** The dashboard starts no session on its own.
- **Generated files are never hand-edited.** They are overwritten on every run.
- **A button that cannot do the thing must not offer to.**

---

## Traps worth knowing about

Each of these cost real time once. They are here so they cost it only once.

- **A `[[link]]` in a log is evidence, and `sync` cannot tell a plan from a record.** A link
  under "Tomorrow" moved a project's `last_touched` forward by three months and erased the
  drift the flag exists to catch. Worse and subtler: **a link naming a project as an example
  of *inaction* is still a link** — "two things have *not* happened: `[[PBB Lab]]` is still
  open at 125 days" moved that project's date too. Link only what you actually worked on.
- **Everything external is UTC; the vault is local.** GitHub push times and ICS stamps both.
  Slicing the string instead of converting turns an evening push into the next day.
- **On an org repo, `commits_recent` is the team's.** Only `commits_mine` may move
  `last_touched`, because that field feeds the drift flags.
- **Counting commits on the default branch is not counting commits.** Work on a side branch
  registers as zero. Known, and deliberately not fixed — fixing it safely means listing
  branches per repo and author-filtering the count, or a fork's upstream history lands in
  your numbers.
- **`Inbox/` is gitignored, so a bad `ingest-ics` overwrites a calendar with no undo.**
  Verify a refetch against a known event before trusting it.
- **`serve.py` is Python: a page reload will not pick up a code change.** Restart the
  service.
- **Verify dashboard changes in a browser, not in the HTML string.** A clipped block and a
  missing now-line were both invisible in the served markup and obvious in a screenshot.
- **Don't size text in a block by counting lines from font metrics.** That arithmetic cut
  the last line through the middle of its glyphs, which reads as broken rather than
  truncated. Let it be a flex column ending in a fade mask.
- **`el.hidden` is only a hint until the CSS agrees.** An author `display` rule outranks the
  UA's `[hidden]{display:none}`, so the attribute was set and nothing moved. The global
  `[hidden]{display:none!important}` is load-bearing.
- **The Google Calendar link is built, not stored** — `eid` is
  `base64url("<event id> <calendar id>")`, and a recurring occurrence needs an instance
  suffix rebuilt from its local start. A wrong `eid` fails silently as an error page, so
  re-verify against a real `htmlLink` if you touch it.

---

## Layout

```
zipper/          the engine and the dashboard
  core.py        vault paths, frontmatter, shared helpers
  cli.py         the whole command surface, in one place
  lint.py sync.py status.py      validation, evidence, the snapshot
  ics.py events.py               calendars, recurrence, event notes
  gh.py canvas.py metrics.py     the fetchers and the numbers
  runqueue.py views.py           the between-runs diff, and the saved queries
  chat.py        the Discord CLI
  serve.py       the dashboard
  README.md      operational reference — read before changing any of it
bot/             the Discord relay: gateway client, HTTP surface
utils/           the two modules the bot depends on
deploy/          systemd units and an nginx vhost
docs/            setup journal
```

`CLAUDE.md` governs how an agent should work in this repo. It deliberately contains nothing
about whose vault it runs against.

## Not in this repository

**The vault itself.** The code is public; the notes are not, and that split is the point.
Anything host-specific — tokens, calendar URLs, tailnet addresses — lives in the environment,
never in the tree.

## Licence

MIT. See `LICENSE`.
