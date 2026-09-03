# Scripts

`brain.py` — stdlib-only, no pip installs. macOS ships a usable `python3`.

    cd ~/path/to/zipper
    python3 brain/brain.py --help

Optional shell alias — add to `~/.zshrc`:

    alias brain='BRAIN_VAULT=~/path/to/vault python3 ~/path/to/zipper/brain/brain.py'

Then `brain today`, `brain status`, `brain lint`.

## Commands

| Command | What it does |
|---|---|
| `today` | Creates `Log/YYYY-MM-DD.md` from the template |
| `lint` | Validates all frontmatter against the schema. Run before committing |
| `sync` | Reads `[[links]]` in `Log/` and sets `last_touched` on the notes mentioned |
| `status` | Regenerates `Meta/Status.md` — scoreboard, drift, stale, reviews, attention |
| `agenda [--days N]` | Regenerates `Meta/Agenda.md` from ingested calendars |
| `touch <Note>` | Manually bump `last_touched` |
| `metric <key> <value>` | Appends a dated row to `Metrics/metrics.csv` |
| `metrics` | Prints every series with its trend |
| `decide "<title>"` | Scaffolds a decision note with a review date 90 days out |
| `ingest-ics <url\|file> --label X` | Parses an ICS feed into `Inbox/calendar-X.json` |
| `ingest-budget <csv>` | Reads a bank CSV, writes monthly spend/income metrics |

## Getting the feeds

**Canvas assignments** — Canvas → Calendar → *Calendar Feed* (bottom right) → copy the
`webcal://` URL:

    python3 brain/brain.py ingest-ics "webcal://<your-canvas-host>/feeds/calendars/xxx.ics" --label canvas

**Google Calendar** — Settings → *Settings for my calendars* → pick the calendar →
*Secret address in iCal format*:

    python3 brain/brain.py ingest-ics "https://calendar.google.com/calendar/ical/.../basic.ics" --label gcal

That secret URL grants read access to your calendar to anyone holding it. It will sit in
your shell history and in `Inbox/`. If that bothers you, save the `.ics` file manually and
pass a file path instead — or use the Google Calendar connector, which is the better path.

**Budget** — export a CSV from your bank, then:

    python3 brain/brain.py ingest-budget ~/Downloads/transactions.csv

Column detection is best-effort (looks for date/amount headers). It writes only monthly
totals to metrics — individual transactions are never copied into the vault.

## Weekly, in one line

    python3 brain/brain.py sync && python3 brain/brain.py status && python3 brain/brain.py agenda

## Notes

- `Inbox/` and `Scripts/` are excluded from vault scans, so ingested JSON never pollutes
  queries or lint.
- `status` and `agenda` write **generated** files. Anything you hand-edit there is lost on
  the next run — put durable thinking in the real notes.
- `sync` only ever moves `last_touched` forward from log evidence. It never invents dates.

## Canvas submission status

`Inbox/canvas.json` is the only place the vault knows **submitted** rather than merely
**due** — the ICS feed carries due dates alone. `brain.py canvas` fills it.

With a token (unattended, works on the VPS):

    export CANVAS_TOKEN=...        # ASU disables self-service tokens; request via UTO
    python3 brain/brain.py canvas

Without one, dump the JSON from a **logged-in** browser and pass `--file`. Paste this in
the devtools console on your Canvas host — it follows pagination, which a plain URL visit
does not, and a busy month silently truncates at 100 items without it:

    (async () => {
      let url = '/api/v1/planner/items?start_date=2026-08-25&end_date=2026-12-31&per_page=100';
      const all = [];
      while (url) {
        const r = await fetch(url, {credentials: 'same-origin'});
        let t = await r.text();
        if (t.startsWith('while(1);')) t = t.slice(9);
        all.push(...JSON.parse(t));
        const m = (r.headers.get('Link') || '').match(/<([^>]+)>;\s*rel="next"/);
        url = m ? m[1] : null;
      }
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([JSON.stringify(all)], {type: 'application/json'}));
      a.download = 'planner.json'; document.body.appendChild(a); a.click(); a.remove();
      console.log('saved', all.length, 'items');
    })();

Then:

    python3 brain/brain.py canvas --file ~/Downloads/planner.json
    python3 brain/brain.py agenda

The ASU session expires within the hour, so this is an **on-demand reconciliation**, not
something launchd can drive. Only a token makes it unattended.

## Dashboard

Double-click **Brain.app** on the Desktop. It fetches once, opens a tab, and quits when you
close the tab. Rebuild the bundle with `./brain/install-app.sh`.

    python3 brain/serve.py --port 8800 [--open]     # the same thing, by hand

Stdlib only — no Flask, no pip, no venv. The VPS needs nothing but `python3`.

**One fetch per launch.** The refresh runs at startup, never on a page load, so reloading
the page shows the same data. To refetch, quit and relaunch. Sources publish as they land
and the page fills in live over SSE — no reload, no spinner.

**The Schedule panel is a time grid, not a list.** Each block's height is its real
duration at `PX_PER_MIN` (0.85px per minute, so an hour is ~51px), overlapping events get
their own lane side by side, and a hairline marks the current minute — framed into view
when it falls within 90 minutes of the day's first or last event. Anything already finished
is dimmed and struck through.

Events with a note in `Events/` get an accent-coloured left border, a 📝, and the note's
*why* inside the block — the whole thing, as much of it as the block holds, ending in a fade
mask where it runs out. The block is a flex column and the description takes the space that
is left, so it degrades correctly from a 45-minute slot (title and time only) to a 3-hour one
(five lines). `brain.event_note_map()` does the `(uid, start)` lookup; see `Meta/Schema.md`
for the note format.

**Click a block to expand it** — the description un-truncates and an action row appears:
*open note* (or *write the debrief*, when the event has passed and the note is still open),
*join* if a Zoom/Meet/Teams URL is sitting in the location field, *calendar* for the real
Google Calendar page, *Canvas* for a Canvas item's own URL. Escape or a click outside closes
it. A block with **no** note gets **+ event note** instead, which POSTs to `/api/eventnote`
and scaffolds one through `brain.cmd_event` — the same code path the CLI uses, so both
produce identical notes.

The *calendar* link is derived, not stored. Google's `eid` is
`base64url("<event id> <calendar id>")`; the calendar id is parsed out of the remembered
iCal URL (`.../ical/<calendar id>/private-<token>/basic.ics`) and a recurring occurrence
needs its instance suffix `_<UTC stamp>` rebuilt from the local start. A wrong `eid` fails
as a Google error page rather than an exception, so check any change against a real
`htmlLink` from the Calendar API.

**`‹ ›` in the card header walk to any other day**, with ←/→ as shortcuts and a `today`
button that appears once you've moved. The viewed day lives in `window.__day` and is passed
to `/api/panels?day=YYYY-MM-DD`, so an SSE refresh mid-browse redraws the day you are on
rather than snapping back. `day_events(day)` is the fetch — `upcoming()` starts at today and
so cannot look backwards. Nothing strikes through and no now-line is drawn off today.

The grid needs `end` on every event. `brain.parse_ics` carries the master event's duration
onto each expanded occurrence for exactly this reason — before that, every recurring meeting
arrived without one and would have drawn as a 30-minute stub.

**The run queue** at the bottom is the diff for *this launch*: new and removed calendar
events, newly submitted Canvas items, repo pushes, new flags. `no changes` when the
sources were already current.

It lives in `Inbox/feed.json`, so it survives a restart of the server — it used to be
in-memory only, and a queue you were halfway through vanished with the app. Every row
has a tick box; ticking one crosses it off for every open tab. The count in the header
is what is still *outstanding*, not what arrived.

The file is the interface, not just storage. The Claude session in the terminal crosses
items off with

    python3 brain/serve.py --mark <key>      # key, or any unique prefix
    python3 brain/serve.py --queue           # print the queue, keys and all

and a watcher thread notices the file move and pushes the new state to every open
dashboard within a second. That is why the queue prompt hands Claude the keys. A row is
keyed by a hash of its text, so the same fact arriving twice is one queue item, not two.
`no changes` and `terminal` lines are chatter: shown once, never persisted, no tick.

**Freshness per source** across the top, amber past its threshold. Every data bug this
vault has produced was stale data presented as current; the page states its own age.

**Closing the tab stops the server.** The SSE stream doubles as the liveness signal, with
a 4s grace window so a reload does not kill it. Note `EventSource` auto-reconnects: a Brain
tab left open in another window will keep the server alive and silently reattach to the
next one you start. Close them if you want a clean run.

**Canvas** cannot be fetched server-side without a token, so the chip links to Canvas.
For one-click updates, open `/bookmarklet`, drag it to the bookmarks bar, and click it on
your Canvas host — it pages the planner API in your logged-in session, POSTs to
`/api/canvas`, and the queue shows what changed.

### The embedded Claude session

`brew install ttyd` (already done). After the refresh finishes, the server spawns

    ttyd -p 8801 -i 127.0.0.1 -W  brain/claude-session.sh [prompt-file]

and the page mounts it in an iframe. **fullscreen** fills the window (Esc exits);
**pop out** opens it as its own tab. The session dies with the server.

If the run produced real changes, Claude opens with the queue as its first instruction —
read `Meta/Queue.md` and `Inbox/queue.json`, update whatever the changes affect, flag
contradictions. If nothing changed, it is a blank session in the vault.

**`-W` gives out a live shell, so it is bound to `127.0.0.1` and must stay there.** Do not
expose the ttyd port through nginx.

**PATH.** Finder launches an app with `/usr/bin:/bin:/usr/sbin:/sbin`, where none of `ttyd`,
`tmux`, `gh` or `claude` exist. `serve.py` appends `~/.local/bin`, `/opt/homebrew/bin` and
`/usr/local/bin` at startup and the app launcher exports the same. Before that fix the
terminal card said *ttyd not installed* under Brain.app and worked fine from a shell — and
the same trap made `gh auth token` fail, so a launch-time fetch quietly wrote public-repo
data over the notes.

**Nothing starts on its own.** Opening the dashboard spawns no ttyd, no tmux and no Claude
— looking at the day must not cost tokens. What the card offers depends on whether a
conversation is already alive, and it asks the server rather than guessing.

No live session:

* **start blank session** — Claude in the vault, no opening instruction
* **start session to clear queue** — Claude opened on this run's queue: read `Meta/Queue.md`
  and `Inbox/queue.json`, update what the changes affect, flag contradictions

A live session:

* **resume conversation** — attach to it, mid-stream
* **start new conversation** — kill it and open a blank one
* **new conversation with queue** — kill it and open a new one on this run's queue

The last two confirm first, because they end the running conversation.

**The session survives the browser.** ttyd runs `tmux new -A -s brain`, so tmux owns the
Claude process rather than the websocket. Closing the tab *detaches*; reopening reattaches
to the same live conversation, mid-stream. Without tmux, ttyd spawns a fresh command per
connection and every tab close silently started a new Claude.

**new conversation** in the card header does the same as the button: kills the tmux
session, so the next attach starts fresh. **refresh** re-fetches on demand; the launch
fetch still happens, this just means you do not have to relaunch to get current data.

Relaunching Brain.app reattaches to the *existing* conversation, so a new run's queue is
not injected into it — attaching to a live tmux session runs no command, and the prompt
file is never read. **new conversation with queue** is the button that means it: it kills
the session first, so `claude-session.sh` actually runs and actually reads the prompt.
Before that it was silent — the old queue button against a live session wrote a prompt
nobody read, mounted the old conversation, and looked like it had worked.

**Crossing things off by hand.** Every row has a tick box. A **task** is written back to
its markdown file, so the vault stays the source of truth and the ledger sees the close.
**Canvas** cannot be written to, so those go in `Inbox/overrides.json` and are a display
override only — Canvas remains authoritative for what was actually submitted.

**Paste:** text paste works. Image paste is unverified — Claude Code reads the macOS
pasteboard itself, and the process runs locally, so `ctrl+v` may work through xterm.js; it
may equally be swallowed by the browser. If it does not work, drop the image anywhere on
disk and paste the path instead.

### Reaching it from another device

With Tailscale up, serve the tailnet rather than loopback — `tailscale ip -4` gives
the address to bind:

    python3 brain/serve.py --host "$(tailscale ip -4)" --port 8800

The terminal stays on loopback unless you pass **both** `--term-host` and `--term-cred` —
`serve.py` refuses to bind an unauthenticated shell off `127.0.0.1`. iOS Safari often will
not show a basic-auth prompt inside an iframe, so use **pop out** on a phone.

### Deploying to the VPS

    git clone <repo> /srv/brain && cd /srv/brain
    python3 brain/serve.py --host 127.0.0.1 --port 8800   # behind nginx

Put nginx in front with TLS **and auth** — this page is coursework, projects, revenue and
people. Basic auth is the ten-minute version; binding to a Tailscale address so it never
faces the internet is better. `Inbox/` is gitignored except `canvas.json`, so the iCal
token never leaves the laptop.
