# Zipper

A personal assistant built in two halves that finally met.

**`brain/`** — a queryable model of one person's work, in plain markdown. A vault of notes
with structured frontmatter, a stdlib-only engine that reads it, and a local dashboard that
puts the day in front of you. This is the part that works.

**`bot/`** — a Discord relay kept from the original Zipper: it opens a thread per
conversation, forwards messages to an HTTP endpoint, and delivers replies back. Roughly
seventy lines, and the right front door for something that works for a while and then
reports in.

Everything else Zipper used to be — the self-modifying agent, its tool loop, its own web
dashboard — was removed in September 2026. It is in the history if you want it. Zipper's
problem was never the model; it was that it had no job. `brain/` is the job.

---

## brain

Notes are the database. Every note carries frontmatter — `type`, `status`, `stage`,
`last_touched`, `next_action`, `repos` — and the engine answers questions from that rather
than from memory: what is drifting, what got pushed but never written down, what claims to
be active and hasn't been touched in six weeks.

```bash
export BRAIN_VAULT=~/path/to/vault
python3 brain/brain.py catchup     # fetch, then regenerate every derived view
python3 brain/brain.py views       # recompute the query views into Inbox/views.json
python3 brain/brain.py lint        # validate frontmatter; run before committing
python3 brain/serve.py --port 8800 --open
```

### Views

Twenty-one saved queries — next action per active project, the revenue scoreboard, three
different definitions of drift, decisions coming due, who is on what — computed in one pass
and written to `Inbox/views.json`.

Every view is the same shape: a title, columns, and rows, where a cell is a scalar or a
link. That is the whole reason this is a JSON file and not twenty panels — the renderer
never has to know which view it is holding, so a new view is a function returning rows and
costs nothing on the HTML side.

They are served at `/views/<page>` (`now`, `ventures`, `school`, `drift`), and the two that
belong in a morning glance — next actions, and the scoreboard — are also cards on the front
page.

No dependencies. No virtualenv. `python3` and the standard library.

### The dashboard

A single local page: today's schedule as a real time grid (block height is duration,
overlaps get lanes, a hairline marks the current minute), a ranked list of what to work on
drawn from coursework and tasks, the flags the queue raised, and an embedded terminal so an
agent session survives a browser reload.

It owns no data. Every panel reads what the engine already wrote to disk, which means a
wrong number is fixed in the vault or the fetcher, never in the view.

### What it ingests

| Source | How |
|---|---|
| GitHub | REST API — push dates, commit counts, per-author attribution |
| Calendars | ICS feeds, with `RRULE` expanded locally and `EXDATE`/`RECURRENCE-ID` honoured |
| Canvas LMS | planner API — the only source that knows *submitted* rather than merely *due* |
| Budget | a bank CSV reduced to monthly totals; transactions never enter the vault |

Configuration is environment-only — see `.env.example`. Nothing about any particular
person, school, or host is compiled in.

### Design rules worth stating

- **The vault holds conclusions, not caches.** When something external matters, extract the
  durable part and leave the original where it lives. A dead pointer is honest; a stale copy
  lies quietly.
- **Generated files are never hand-edited.** Four views are overwritten on every run.
- **Evidence moves dates forward, never backward.** Deferring a project is not touching it.
- **Facts are the engine's to write; judgments are not.** It will correct a push date. It
  will not decide that a project is dormant.

---

## The lifecycle

Two processes stay up; the third comes and goes.

```
brain-web.service       serve.py --daemon     dashboard, views, POST /discord
brain-discord.service   bot/                  the gateway connection, always on
   └── claude           ttyd + tmux           only while a conversation exists
```

The Claude session is deliberately not a service. Opening the dashboard must not
start one — merely looking at your day should not cost tokens — so it starts when you
start it, survives a closed tab because tmux owns it, and can be resumed later.

**A message from Discord lands in whichever of those three states the session is in:**

| tmux session | ttyd | what happens |
|---|---|---|
| live | serving | pasted straight into the running conversation |
| live | stopped | ttyd is brought back up, then pasted |
| none | — | a new conversation starts, primed with the message |

The last case takes the quiet path: rather than racing a TUI that has not drawn yet, the
message is written to the ready-file that `claude-session.sh` reads before exec'ing, so it
becomes the conversation's opening prompt.

Messages arrive tagged `[via discord]` and carry the command to reply with, because the
sender is not watching the terminal.

## Talking back

```bash
python3 brain/brain.py discord send "the build finished"
python3 brain/brain.py discord send "results" --file report.html
python3 brain/brain.py discord read --limit 5
python3 brain/brain.py discord status
```

Stdlib-only — `brain/` never imports `discord`, and the bot is the only process holding a
gateway connection. That split is what lets a scheduled job or a finished long-running task
say something without owning a socket.

## Running it

```bash
pip install -r requirements.txt        # bot only; brain/ needs nothing
cp .env.example .env                   # fill in BRAIN_VAULT and the Discord pair
python3 brain/serve.py --daemon        # stays up when the last tab closes
python3 -m bot.discord_bot
```

`deploy/` has systemd units for both and an nginx vhost. **Bind it to a tailnet address,
never `0.0.0.0`** — nothing in this server is authenticated, and it can start a shell.

---

## Layout

```
brain/          the vault engine, the dashboard, and their docs
bot/            Discord relay
utils/          the two constants the bot needs; nothing else survived
docs/           setup journal
```

## Not in this repository

The vault itself. The code is public; the notes are not, and the split is the point —
`BRAIN_VAULT` is the only thing joining them. Anything host-specific (tokens, calendar
URLs, tailnet addresses) lives in the environment, never in the tree.

## Licence

MIT. See `LICENSE`.
