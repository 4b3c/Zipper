# Zipper

A personal assistant built in two halves that finally met.

**`brain/`** — a queryable model of one person's work, in plain markdown. A vault of notes
with structured frontmatter, a stdlib-only engine that reads it, and a local dashboard that
puts the day in front of you. This is the part that works.

**everything else** — the original Zipper: a self-modifying agent that ran 24/7 on a VPS,
took tasks over Discord, and could rewrite and restart itself. Retired in September 2026;
kept here because the Discord relay is being reused and the LLM loop is worth reading.

Zipper's problem was never the model. It was that it had no job. `brain/` is the job.

---

## brain

Notes are the database. Every note carries frontmatter — `type`, `status`, `stage`,
`last_touched`, `next_action`, `repos` — and the engine answers questions from that rather
than from memory: what is drifting, what got pushed but never written down, what claims to
be active and hasn't been touched in six weeks.

```bash
export BRAIN_VAULT=~/path/to/vault
python3 brain/brain.py catchup     # github + calendars + agenda + status + queue
python3 brain/brain.py lint        # validate frontmatter; run before committing
python3 brain/serve.py --port 8800 --open
```

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

## Zipper (retired)

The original: a FastAPI service that held a tool-using loop, a Discord bot as its front
door, and cron hitting a `/wake` endpoint on a schedule. It could edit its own source,
restart itself, and roll back if the restart crashed.

```
main.py         FastAPI — /chat /discord /wake /status
bot/            Discord gateway; a thin relay, ~70 lines
llm/            the conversation loop and model routing
tools/          bash, file, web, task, restart
storage/        conversations, memory, schedule, todos
prompts/        system prompts per surface
```

It died of economics and purpose: local models were too slow, cloud models too expensive,
and there was no task worth the tokens. The interesting fragment is `bot/` — a Discord
thread is a good interface for something that works for a while and reports back.

See `docs/CLAUDE.md` for the original architecture notes.

---

## Layout

```
brain/          the vault engine, the dashboard, and their docs
bot/            Discord relay (reused)
llm/ tools/ storage/ utils/ prompts/    original Zipper runtime
docs/           architecture notes, setup journal
```

## Not in this repository

The vault itself. The code is public; the notes are not, and the split is the point —
`BRAIN_VAULT` is the only thing joining them. Anything host-specific (tokens, calendar
URLs, tailnet addresses) lives in the environment, never in the tree.

## Licence

MIT. See `LICENSE`.
