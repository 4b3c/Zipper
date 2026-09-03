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

## The bot

```bash
pip install -r requirements.txt
DISCORD_TOKEN=... DISCORD_CHANNEL_ID=... ZIPPER_URL=http://127.0.0.1:8800 \
  python3 -m bot.discord_bot
```

It POSTs each message to `ZIPPER_URL/discord` and serves an HTTP API on `BOT_URL` for the
handler to push replies back through. **The service that used to answer is gone**, so the
endpoint is now yours to provide.

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
