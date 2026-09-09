# The engine — operational reference

`zipper` — stdlib-only, no pip installs, no venv. The VPS needs nothing but `python3`.

    cd /opt/zipper
    python3 -m zipper --help

`ZIPPER_VAULT=/opt/vault` is set in `/opt/zipper/.env` and by the systemd units, so the
engine finds the notes from anywhere. Then `zipper today`, `zipper status`, `zipper lint`.

Finished changes and the reasoning behind them are in `../HISTORY.md`, not here.

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

    python3 -m zipper ingest-ics "webcal://<your-canvas-host>/feeds/calendars/xxx.ics" --label canvas

**Google Calendar** — Settings → *Settings for my calendars* → pick the calendar →
*Secret address in iCal format*:

    python3 -m zipper ingest-ics "https://calendar.google.com/calendar/ical/.../basic.ics" --label gcal

That secret URL grants read access to your calendar to anyone holding it. It will sit in
your shell history and in `Inbox/`. If that bothers you, save the `.ics` file manually and
pass a file path instead — or use the Google Calendar connector, which is the better path.

**Budget** — export a CSV from your bank, then:

    python3 -m zipper ingest-budget ~/Downloads/transactions.csv

Column detection is best-effort (looks for date/amount headers). It writes only monthly
totals to metrics — individual transactions are never copied into the vault.

## Weekly, in one line

    python3 -m zipper sync && python3 -m zipper status && python3 -m zipper agenda

## Notes

- `Inbox/` and `Log/` are excluded from vault scans, so ingested JSON and daily notes never
  pollute queries or lint. `Tasks/` is **not** excluded.
- `status` and `agenda` write **generated** files. Anything you hand-edit there is lost on
  the next run — put durable thinking in the real notes.
- `sync` only ever moves `last_touched` forward from log evidence. It never invents dates.

## Canvas submission status

`Inbox/canvas.json` is the only place the vault knows **submitted** rather than merely
**due** — the ICS feed carries due dates alone. `zipper canvas` fills it.

With a token (unattended, works on the VPS):

    export CANVAS_TOKEN=...        # some schools disable self-service tokens; ask IT
    python3 -m zipper canvas

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

    python3 -m zipper canvas --file ~/Downloads/planner.json
    python3 -m zipper agenda

The browser session expires within the hour, so this is an **on-demand reconciliation**, not
something the fetch timer can drive. Only a token makes it unattended.

## Dashboard


    python3 -m zipper.serve --port 8800 [--open]     # the same thing, by hand

Stdlib only — no Flask, no pip, no venv. The VPS needs nothing but `python3`.

**No fetch at launch.** Inputs are pulled hourly by `zipper-fetch.timer` and at the start of
every bookkeeping pass. The **refresh** button forces one; a page load never does, so reloading
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
(five lines). `events.event_note_map()` does the `(uid, start)` lookup; see `Meta/Schema.md`
for the note format.

**Click a block to expand it** — the description un-truncates and an action row appears:
*open note* (or *write the debrief*, when the event has passed and the note is still open),
*join* if a Zoom/Meet/Teams URL is sitting in the location field, *calendar* for the real
Google Calendar page, *Canvas* for a Canvas item's own URL. Escape or a click outside closes
it. A block with **no** note gets **+ event note** instead, which POSTs to `/api/eventnote`
and scaffolds one through `events.cmd_event` — the same code path the CLI uses, so both
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

The grid needs `end` on every event. `ics.parse_ics` carries the master event's duration
onto each expanded occurrence for exactly this reason — before that, every recurring meeting
arrived without one and would have drawn as a 30-minute stub.

**The queue** at the bottom holds the *events* this launch found: new and removed calendar
events, newly submitted Canvas items, repo pushes. When nothing turned up it shows nothing
— a "no changes" row was a queue item announcing that no work had arrived, which is the one
thing a queue of work must never contain: it read as something to deal with, and could be
ticked off.

Every row is a typed event: `system` (github/calendar/canvas/vault), `action`, and
optional `who`/`when` where the source actually knows them. The `vault` events — the working
tree, straight from git — are rows in that same list, **not a section under it**. They had
their own heading for a while, and it made an uncommitted note read as a different kind of
thing to be dealt with separately when it is simply another event this pass has to account
for. The one real difference is how they clear: a note row goes when the pass commits and
cannot be `--mark`ed, where an event row can. That distinction used to be visible as a
missing tick box; now that no row has one, it lives in the caption under the note rows.
The header count includes them, because an uncommitted note is outstanding. It is polled
every four seconds rather than watched: another conversation editing the vault writes no file this server
could watch for, and the card has to show that without waiting for a refresh.

**There is only one queue.** It lives in `Inbox/feed.json` and is crossed off row by row.
`Meta/Queue.md` is not a second one — it is the *rendering* of this queue plus the
uncommitted note diff and the flags, written by `zipper fetch`. "Clear the queue" means
working each row: find what it affected, update that note, tick it off.

**Flags never enter the queue.** They are conditions derived fresh from current state, so
ticking one is meaningless — it re-fires next run while reading as handled. They go to
**Signals**. Until 2026-09-06 `emit_diff` published them as rows; that was the bug.

It lives in `Inbox/feed.json`, so it survives a restart of the server — it used to be
in-memory only, and a queue you were halfway through vanished with the app. The count in
the header is what is still *outstanding*, not what arrived.

**The card is read-only. No row is crossed off from the browser.** Tick boxes were removed
on 2026-09-06: a row means *something happened that the vault has not accounted for*, and
the only thing that makes it accounted for is working out what it affected. A tick box
offers to shorten the list without doing that, and a short list then reads as a reconciled
one — the same objection that says bookkeeping is a pass rather than a command. A row now
clears exactly two ways, both of which mean the reasoning actually happened: `zipper commit`
closing a pass, or `--mark` from the session that just did it. `/api/queuedone` was deleted
along with the button, so there is no live route left for it to come back through.

Tasks in **What to work on** are unaffected and still tick by hand — that box writes back to
the markdown file, so it records a real completion rather than dismissing a notification.

The file is the interface, not just storage. The Claude session in the terminal crosses
items off with

    python3 -m zipper.serve --mark <key>      # key, or any unique prefix
    python3 -m zipper.serve --mark-all        # cross off everything still open
    python3 -m zipper.serve --queue           # print the queue, keys and all

`--mark` toggles, so it doubles as an undo; `--mark-all` only ever crosses off, and so
cannot undo a row you deliberately un-ticked. It writes once for the whole batch rather
than once per row, which is one watcher push to the open tabs instead of N.

and a watcher thread notices the file move and pushes the new state to every open
dashboard within a second. That is why the queue prompt hands Claude the keys. A row is
keyed by a hash of its text, so the same fact arriving twice is one queue item, not two.
**Terminal lifecycle is not an event.** Showing a conversation, a session starting, resuming
or ending — none of it goes near the queue. It was published as a `diff`, which kept it out of
`feed.json` but still drew it as a row in the open tab, so a card meant to show what the world
did filled up with what the dashboard did. Those calls publish `status` now (a transient line
under the date) or nothing at all; only errors and delivered messages still say anything.
`no changes` lines are chatter too: shown once, never persisted, no tick.

**Freshness per source** across the top, amber past its threshold. Every data bug this
vault has produced was stale data presented as current; the page states its own age.

**Closing the tab stops the server.** The SSE stream doubles as the liveness signal, with
a 4s grace window so a reload does not kill it. Note `EventSource` auto-reconnects: a Zipper
tab left open in another window will keep the server alive and silently reattach to the
next one you start. Close them if you want a clean run.

**Canvas** cannot be fetched server-side without a token, so the chip links to Canvas.
For one-click updates, open `/bookmarklet`, drag it to the bookmarks bar, and click it on
your Canvas host — it pages the planner API in your logged-in session, POSTs to
`/api/canvas`, and the queue shows what changed.

### The embedded Claude session

Each conversation gets its own ttyd, allocated from `ZIPPER_TTYD_BASE` (8810) upwards and
remembered in the registry:

    ttyd -p <port> -i 127.0.0.1 -W --base-path /t/<port>  zipper/claude-session.sh [prompt-file]

The page mounts it in an iframe. **fullscreen** fills the window (Esc exits); **pop out**
opens it as its own tab. **The session outlives the server** — `zipper-web.service` sets
`KillMode=process`, so a `systemctl restart` leaves ttyd and the tmux server up and the
conversation is still there. Verify with `tmux ls`: an unchanged creation time means it
lived.

If the run produced real changes, Claude opens with the queue as its first instruction —
read `Meta/Queue.md`, work each row to the note it affected, review the uncommitted diff,
flag contradictions, then `zipper commit "<msg>"`. A pass fetches every input before it
renders, so the brief is never read over stale data. If nothing changed, it is a blank
session in the vault.

**`-W` gives out a live shell, so it is bound to `127.0.0.1` and must stay there.** Do not
expose the ttyd port through nginx.

**PATH.** systemd starts a service with a minimal PATH, where none of `ttyd`, `tmux`, `gh`
or `claude` exist. `zipper/web/base.py` prepends `~/.local/bin` and `/usr/local/bin` at
import and `claude-session.sh` exports the same. Without it every shell-out fails silently —
including `gh auth token`, which makes a fetch write public-repo data over the notes.

**Nothing starts on its own.** Opening the dashboard spawns no ttyd, no tmux and no Claude
— looking at the day must not cost tokens. What the card offers depends on whether a
conversation is already alive, and it asks the server rather than guessing.

No live session:

* **start blank session** — Claude in the vault, no opening instruction
* **start session to clear queue** — Claude opened on `Meta/Queue.md`: work each open row
  to the note it affected, review the uncommitted diff, flag contradictions. Rows stay open
  until something calls `--mark` or `zipper commit "<msg>"`

A live session:

* **resume conversation** — attach to it, mid-stream
* **start new conversation** — kill it and open a blank one
* **new conversation with queue** — kill it and open a new one on the vault queue

The last two confirm first, because they end the running conversation.

**The session survives the browser.** ttyd runs `tmux new -A -s zipper`, so tmux owns the
Claude process rather than the websocket. Closing the tab *detaches*; reopening reattaches
to the same live conversation, mid-stream. Without tmux, ttyd spawns a fresh command per
connection and every tab close silently started a new Claude.

**new conversation** in the card header does the same as the button: kills the tmux
session, so the next attach starts fresh. **refresh** re-fetches on demand; the launch
fetch still happens, this just means you do not have to relaunch to get current data.

not injected into it — attaching to a live tmux session runs no command, and the prompt
file is never read. **new conversation with queue** is the button that means it: it kills
the session first, so `claude-session.sh` actually runs and actually reads the prompt.
Before that it was silent — the old queue button against a live session wrote a prompt
nobody read, mounted the old conversation, and looked like it had worked.

**Crossing things off by hand** — in **What to work on** only; the queue card above is
read-only. A **task** is written back to
its markdown file, so the vault stays the source of truth and the ledger sees the close.
**Canvas** cannot be written to, so those go in `Inbox/overrides.json` and are a display
override only — Canvas remains authoritative for what was actually submitted.

The override has to **survive a re-read**, because the extension rewrites `canvas.json`
wholesale every time he opens Canvas. So it is stored beside the data rather than on the
item, and is reattached at read time by `canvas.stamp_overrides` — matching on **course +
normalized title**, or on **`plannable_id`** if Canvas has since renamed the assignment.
Either match is enough, so a crossed-off item finds its own cross-off again however Canvas
chooses to re-describe it.

Everything reads Canvas through **`canvas.items()`**, which applies them. Loading
`canvas.json` directly is the bug: the dashboard's two cards, the agenda strike-through and
the CLI report each used to do their own load, so crossing something off in one place left
it outstanding in the others and the next visit to Canvas brought it back everywhere.
`zipper canvas` names what has been crossed off, because that is the one line in the report
resting on Abram's word rather than on Canvas.

**Paste:** text paste works. Image paste stores the bytes on the VPS and types the *path*,
which Claude Code opens — the browser and the session are on different machines, so the
pasteboard itself never crosses.

### Reaching it from another device

With Tailscale up, serve the tailnet rather than loopback — `tailscale ip -4` gives
the address to bind:

    python3 -m zipper.serve --host "$(tailscale ip -4)" --port 8800

The terminal stays on loopback unless you pass **both** `--term-host` and `--term-cred` —
`serve.py` refuses to bind an unauthenticated shell off `127.0.0.1`. iOS Safari often will
not show a basic-auth prompt inside an iframe, so use **pop out** on a phone.

### Deploying to the VPS

    git clone <repo> /srv/zipper && cd /srv/zipper
    python3 -m zipper.serve --host 127.0.0.1 --port 8800   # behind nginx

Put nginx in front with TLS **and auth** — this page is coursework, projects, revenue and
people. Basic auth is the ten-minute version; binding to a Tailscale address so it never
faces the internet is better. `Inbox/` is gitignored except `canvas.json`, so the iCal
token never leaves the laptop.

## Conversations — one Claude per Discord thread

A Discord thread *is* a conversation. A message in the channel opens a thread and starts a
new one; a reply inside a thread reaches the instance already holding it. Several run at
once, each in its own detached tmux session.

| Piece | Where |
|---|---|
| Registry, start/resume/paste | `zipper/convcore.py` |
| Liveness, titles, listing, close, the idle reaper | `zipper/convstate.py` |
| A ttyd per conversation, and its port | `zipper/ttyd.py` |
| All three under one name | `zipper/conversations.py` — import this, not those |
| Routing a Discord message to its thread's instance | `zipper/web/http.py`, the `/discord` route |
| Forwarding the reply back to that thread | `hooks/forward_reply.py`, on the `Stop` hook |
| Opening a thread for a channel message | `bot/client.py`, `on_message` |
| Typing indicator on/off | `zipper/chat.py` → the bot's `/typing` |
| `zipper conversations [--close THREAD]` | the CLI surface |

**The session id is derived, not stored:** `uuid5(NS, thread_id)`. A thread finds its
conversation again with no mapping file to fall out of sync, and losing
`Inbox/conversations.json` costs the timers and titles, not the conversations.
`--session-id` assigns that id on a first run and `--resume` takes it back up; they are not
interchangeable, so the transcript on disk is what decides which one a start is.

**Each instance is started with `ZIPPER_DISCORD_THREAD` in its environment**, which is how
both `zipper discord send` and the reply-forwarding hook reach the right thread without the
session having to know its own id. Unset in the dashboard's own terminal, where a send goes
to the channel.

### Replies are forwarded, not sent

`hooks/forward_reply.py` runs on Claude Code's **`Stop`** hook — once per turn, with the
transcript path. It takes the last assistant text block and posts it to this conversation's
thread, but **only if that turn came from Discord**: the bot records what it delivered
(`conversations.note_delivery`) and the hook compares the transcript's last user message
against that record. Typed at the keyboard, and nothing is sent — the answer is already on
screen.

The session therefore writes its reply **once**, into the terminal, and never calls
`discord send` to answer. It used to write it twice, once as the argument to `discord send`
and once as its own terminal reply, which measured at **44% of the paired output** on
2026-09-06 — the same words through the model twice to say one thing to one person. It also
asked the session to decide the destination every turn from ambient context, and that decision
drifted: eight replies went to Discord that day for messages typed at the terminal, because
the `[via discord]` tag only ever landed on the message that *opened* the conversation.

Three rules the hook cannot break:

- **It always exits 0.** Exit code 2 on a `Stop` hook *prevents the turn ending* and feeds
  stderr back to the model, so a Discord outage would trap a session in a loop. Every failure
  is swallowed; the terminal still has the answer.
- **It dedupes on the assistant message uuid**, because the hook can fire more than once for
  a turn and posting is not idempotent from Discord's side.
- **It skips `local-` thread ids**, the fallback `new_conversation()` uses when Discord is
  unreachable. There is no thread to post to, and that is not an error.

Interstitial narration stays in the terminal for free: those are earlier text blocks in the
turn, and only the last one is forwarded. The thread reads as clean question-and-answer while
the terminal keeps the working detail.

**Typing is cleared by `discord_send`**, not by the caller, so no reply path can answer and
leave Discord showing that Zipper is still typing.

**The idle close is a price signal, not a saving.** An idle instance costs nothing to leave
running; what changes at the prompt-cache boundary is the price of the *next* message, which
is re-read in full once the cache is cold. `ZIPPER_IDLE_SECONDS` (default 55 min) sets it.
A closed conversation resumes on the next message — the transcript is on disk either way.

### Traps

- **A paste before the TUI is listening is lost, and the Enter after it does nothing** — the
  message then sits in the input box looking delivered. `_wait_ready` polls for the prompt
  character before pasting, and `paste` re-presses Enter until the text has left the box.
  Both were real: the first cold start pasted fine and never submitted.
- The dim text in a resumed session's input box is Claude Code's placeholder hint, **not** a
  draft. It does not concatenate with a paste — verified, because it looks exactly like the
  bug it isn't.
- **Nothing stops two conversations editing the vault at once.** Deliberate (2026-09-06):
  the locking is a lot of code for a risk one operator can hold in his head. The failure it
  invites is real — two sessions editing one note, or committing over each other, with
  neither able to see the other. Don't work the same project in two threads at once; if it
  starts happening, `convcore.py` is where the lock goes.

### The chat list

The Claude card has a conversation list down its left side. Each row is a Discord thread;
clicking one points the iframe at that conversation's terminal. Nothing is torn down when
you switch -- the conversation you were reading keeps running while you read another, which
is the whole reason for one instance per thread rather than one that resumes.

**One ttyd per conversation.** ttyd serves a single command per port, and ours attaches one
tmux session, so each live conversation gets its own port from `ZIPPER_TTYD_BASE` (8810).
The port is remembered in the registry; the process is not, because a `serve.py` restart has
to be able to adopt the ttyd it left behind rather than lose the port to it. Whether the port
answers is the only durable truth.

**The bound conversation is the exception**: its pane is the dashboard's own terminal, which
already has a ttyd on `--term-port`. Opening it reuses that one. Two ttyds on one tmux
session both work, but they share a cursor and fight over the window size.

Clicking a **closed** conversation selects it and offers a **reload conversation** button; it
does not resume it. Selecting costs nothing, but resuming re-reads the whole transcript at
full price -- the prompt cache is exactly what expired when the conversation was closed -- and
that should be a decision rather than a side effect of clicking a name to see what it was.
The panel says so.

When it does resume, it resumes: the transcript is the conversation, and picking one out of a
list must never start a stranger with the same name. The card also swaps back to a live
terminal on its own only when the conversation came back by some other route -- a Discord
message, say -- because remounting something already running is free and resuming is not.

Titles are user-influenced text either way, so the page escapes them (`chatEsc`): a thread
called `<img onerror=...>` is a thing a person can make.

**Order follows the last message, read from the transcript's timestamps.** Reading a
conversation does not move it, and neither does reopening one; only a message does. Two
near-misses on the way here: the registry stamp alone sees what this process delivered and
nothing typed straight into a terminal, which pinned the conversation being sat in to the
bottom; and the file's *mtime* counts resuming as activity, because reopening appends
bookkeeping — `cost-state`, `bridge-session`, a session header — without a word being said.
Those entries carry no timestamp and `user`/`assistant` messages do, so the newest of those is
the honest answer.

**A reload lands on the most recent live conversation.** `focusRecent()` takes the top row of
`/api/conversations` — already sorted by that same last-message stamp. Before this, a refresh
showed whichever ttyd happened to be serving, which is almost always the dashboard's own
terminal and rarely the conversation he was in.

It only auto-opens a conversation that is still **alive**. A closed one keeps its deliberate
click, for the reason the panel already spells out: resuming costs a full re-read, and a page
refresh must never spend that on its own. If the top row is already serving on the port the
iframe is showing, nothing remounts.

Selecting is *all* it does. It deliberately does not scroll the selected row into view: the
list re-renders on a 6s poll, and a page that moves under you is worse than a selected row you
have to scroll to.

### The usage meters

Under the chat list, two bars: the **5-hour session** window and the **7-day** one, as
percentages of the plan. Amber at 70%, red at 90%.

Each is one line — bar, percentage, reset time — with no label. The order says which is which,
and the reset time says it better than the words did: one resets tonight, the other on a
weekday. The bar takes the leftover width and the percentage sits in a fixed tabular column, so
both readouts line up across the two rows and nothing shifts when 9% becomes 100%. Times are
rendered local (the API returns UTC), as a bare time if the window resets today and weekday +
time if it doesn't — anything longer wraps in 186px and pushes the bar around.

The numbers come from Anthropic's OAuth usage endpoint via `zipper/usage.py` — the same source
Claude Code's own `/usage` reads. **Nothing local can answer this**: the transcripts on this
box know what this box spent, but not the denominator, and not what was spent from the phone.
`/api/usage` caches for five minutes (`ZIPPER_USAGE_TTL`) and the page polls on that interval,
so asking faster only re-serves the same answer.

The access token is read out of `~/.claude/.credentials.json` at call time and never stored,
logged or written anywhere — `Inbox/usage.json` holds the percentages only. Same rule as
`.env`: credentials stay where they are.

`_pct` **hunts** for the number rather than indexing a fixed path, and `normalise` drops a
window it cannot read. The response shape is not a contract we control, and a blank meter is a
far better failure than an authoritative-looking 12% when the truth is 90%. A failed call
falls back to the last good numbers, dimmed (`.stale`), rather than blanking on one flaky
request; a 401 means the token expired and Claude Code has not refreshed it yet.

**A bound row has to work out which transcript it is writing.** It adopted a session that was
already running, and nothing states its id: the pane's process carries no `--session-id`, and
Claude appends and closes the file rather than holding it open. `detect_session()` infers it —
the newest transcript in the project directory that no other conversation has claimed — and
re-checks on every listing. Getting this wrong is quiet and wide: the id decides which file
`last_active` reads *and* what `--resume` would reopen if the pane died. It was wrong once,
pointing at the session that started when its predecessor was killed while the pane had gone
on to resume the older conversation. Everything downstream read a file that had stopped moving
forty minutes earlier.

**A session can die without the page being told** — Ctrl-C in the pane ends Claude and takes
the tmux session with it. `sweep()` drops the ttyd of any conversation whose session is gone,
because that ttyd would otherwise happily serve `tmux new -A`: a *new* conversation wearing
the old one's name. It also ends a session Claude has *left* — tmux alive with a bare shell in
it is not a conversation. The card swaps the terminal for a **load conversation** button.

**ttyd attaches; it never creates.** Every ttyd here runs `tmux attach-session -t <name>`, and
the session is started separately — `conversations.start()` for a thread, `_spawn_session()`
for the dashboard's own terminal. This is what makes Ctrl-C mean something. ttyd re-runs its
command on every connection and the browser reconnects on its own when one drops, so while the
command was `tmux new -A` the conversation was **unkillable**: Ctrl-C ended Claude, the pane
went with it, the page reconnected a second later, `new -A` rebuilt the session out of nothing,
the sweep tore it down, and round again — a conversation flickering back to life instead of
going grey. `attach` can only join a session that exists; when there isn't one the command
exits and it stays closed, which is the whole point of pressing Ctrl-C.

The cost of the split is that `start session to clear queue` against a live ttyd has to create
the session itself rather than letting the next reconnect do it — it does, and publishes
`terminal` so the page remounts instead of waiting for a reconnect that would now find nothing.

**`reap_terminal()` applies the same rule to the dashboard's own terminal**, which was exempt
from `sweep()` because it is not a registry conversation: when the `zipper` session is gone,
its ttyd goes too. Otherwise the port stays open, `terminal_up()` keeps reporting the terminal
as viewable, and the card shows a dead pane instead of offering to start something.

**Kill a ttyd by port, not only by handle** (`kill_ttyd_on`). A ttyd outlives the `serve.py`
that spawned it — on a restart the new process adopts the port and holds no handle — and the
adopted one is precisely what has to go, because it is still running *the command it was born
with*. The first cut of the `attach` fix only killed handles, so every ttyd started before the
change went on serving `tmux new -A` and resurrecting killed conversations for as long as it
lived. The code was right and the running processes were old, which is indistinguishable from a
fix that does not work. **After changing a ttyd's argv, kill the running ttyds** — restarting
`zipper-web` does not. Only a process whose `/proc/<pid>/comm` is `ttyd` is ever signalled.

**Ask the pane's process, not `pane_current_command`.** That field reports whatever is in the
foreground, which during a tool call is `bash` or `python3`. Trusting it, the sweep read two
working conversations as exited and killed them — the transcripts survived, but the sessions
did not. `running_claude()` now reads the pane's pid (and its children) from `/proc`, and the
sweep takes a second reading a beat later before ending anything: it is the one function here
that destroys something, so a cheap double-check is worth the second it costs.

**A row is a name and a light.** Yellow means the instance is working, green means it is
waiting for you, grey means closed — clicking a grey one resumes it. The state is read from
the pane, not tracked: an instance starts and finishes work without this process being told,
so any state we maintained would drift the moment it did. The list polls every 6 seconds,
because a light that lags is worse than no light.

**Read the status line, not the pane.** Claude Code prints `esc to interrupt` for exactly as
long as it is busy — but only in its last line. Scanning the whole pane made any conversation
that merely *displayed* those words look permanently busy, and the session where this was
being built stayed yellow after it had finished, for the obvious reason.

The footer is rewritten several times a second and a capture lands on a blank frame often
enough to matter, which made a long turn flicker green and read as finished. So the check
looks at the bottom fourteen lines, counts the spinner line too (during a long tool call it is
the only thing on screen saying work is happening), and is **sticky for 25 seconds after the
marker was last seen**. Both tests are anchored on how those lines *start*, so a conversation
that merely prints the words is not mistaken for a busy one. It is deliberately not inferred from the transcript being written: that was the first
attempt, and it lit the dot yellow for a conversation that had done nothing but come back from
the dead, since resuming writes to the file.

**Names come from Claude, not from Discord.** Claude Code writes an `ai-title` line into the
transcript and rewrites it as the subject moves; that names the conversation rather than its
delivery mechanism, and it exists for sessions started at the terminal that have no thread at
all. A Discord thread name is the fallback for a conversation too young to have been titled,
and an id is the last resort. Titles used to come from whichever path created the row — the
opening message, a generated `Dashboard · Sun 14:26`, an id — four schemes in one list.

**`new conversation` closes nothing.** It adds one, with its own Discord thread from the
start, so a conversation begun at the keyboard can be picked up on a phone without being
adopted after the fact. It used to kill the running session, which made sense when there was
only ever one.

### Discord threads for conversations that started at the keyboard

`new conversation` opens a Discord thread immediately, before anyone knows what the
conversation is about, so that carrying it on from a phone needs no forethought. The thread is
therefore born as `Dashboard · Sun 14:26`, and once Claude has titled the conversation the
thread is renamed to match — otherwise the phone shows a list of timestamps and finding the
right one means opening all of them.

**Only a thread we named is ever renamed** (`auto_named`). A thread opened from a message in
the channel is named by Discord from what the operator typed, and one he renames himself is a
deliberate act; a generated title is not always better than the words a person chose. This
guard was added after the rename overwrote a descriptive thread name with an `ai-title` — the
wrong one, as it happened, from the wrong transcript.

Discord rate-limits renames, and the title moves as the subject does, so a rename fires only
when the name actually changed and at most once every ten minutes per thread.

### Signed in to the dashboard is signed in to the terminals

Each ttyd binds **loopback** and is served through nginx at `/t/<port>/`, on the dashboard's
own origin. Pointed straight at `host:port`, every conversation is a separate origin, and
basic auth prompts for each one — opening four conversations meant signing in four times.

Three pieces have to agree, and all three are load-bearing:

- **nginx** proxies `^/t/(88[0-9][0-9])(/.*)?$` with the WebSocket upgrade headers. The port
  range is pinned: `proxy_pass` to a variable port is a proxy to wherever the URL says, so it
  must not be walkable onto anything else listening on loopback.
- **ttyd** is started with `--base-path /t/<port>`, or its own asset and websocket URLs come
  out absolute and miss the prefix.
- **the page** builds `location.origin + '/t/' + port + '/'`.

`--term-host` therefore wants to be `127.0.0.1`, and on loopback the credential is dropped —
it would prompt for a sign-in the dashboard has already had. On any other host a credential is
still required and still passed: `ttyd -W` hands out a live shell.

The exposure is not widened by this. Before, ttyd listened on the tailnet with basic auth;
now it listens on loopback and is reachable only through the same tailnet-bound nginx that
already serves an unauthenticated dashboard — one that can start terminals anyway. Verified
after the change: `/t/8810/` and `/t/8811/` return 200 through nginx, `/ws` upgrades with
101, and connecting to the tailnet address on 8810 directly is refused.

### Copy and paste between the terminal and the real machine

Selecting in a terminal puts the text on the clipboard of the machine running the browser,
and pasting an image into one reaches the conversation. Both depend on the terminals being
same-origin (above): the iframe's window is reachable from the dashboard page, and ttyd leaves
the xterm instance on it as `window.term`.

**The selection belongs to Claude Code, not to xterm.** It turns on mouse reporting and does
its own highlighting, then copies what you selected into a **tmux buffer**, saying `copied N
chars to tmux buffer`. So `term.getSelection()` is empty by design, and reading it was the
wrong layer — the text was already on the box the whole time, in `tmux list-buffers`.

The clipboard text therefore comes from tmux: `/api/tmuxbuffer` returns the newest buffer, the
page polls it, and the write happens **inside the iframe**, which is the focused document the
clipboard API demands — a write from the parent page is refused for that reason alone. If a
write is refused for want of a gesture, the text is held and written on the next click or
keypress.

The xterm path is kept for panes where Claude Code is *not* what is running. There a drag does
make an xterm selection — except on macOS, where mouse reporting means the bypass is Option and
only with `macOptionClickForcesSelection`, which is off by default. Both ttyd launches set it,
with `rightClickSelectsWord`. Either route ends at the same clipboard.

`/api/clipdebug` is what settled all of this: the browser is the one thing this box cannot test,
so the page reports failures into `journalctl -u zipper-web`. It stays, quiet unless something
breaks. Three fixes were shipped before it existed, each correct and each invisible, because the
layer underneath them was never producing what they were built on.

**Copy only works from a secure context.** Browsers expose `navigator.clipboard` on https and
localhost and nowhere else, so on `http://<tailnet-ip>:8800` the API is simply absent. That is
what `tailscale serve` is for here: it puts the whole thing behind
`https://<machine>.<tailnet>.ts.net:8443`, a real certificate on the tailnet, proxying to nginx
on `127.0.0.1:8899`. Port 8443 rather than 443 because nginx already owns 443 for other sites.
The plain http address keeps working; copy will not work on it.

`/api/clipdebug` exists because this is the one thing that cannot be tested from the box —
whether it works is a fact about the browser. The page reports which path it took and whether
it succeeded, and it lands in `journalctl -u zipper-web`.

**Copy** reads `term.getSelection()` rather than the page's selection — xterm draws to a canvas,
so the document has no selection to read — and writes it from *inside* the frame, where the
click that just happened is the user gesture the clipboard API insists on. It must run
**synchronously inside the event** — a continuation scheduled with `setTimeout` no longer
counts as a gesture, and both the API and `execCommand` then fail silently, leaving whatever
was on the clipboard before. Ctrl/Cmd+C is handled on `keydown` (before xterm forwards it to
the pty) and only when there is a selection, so a bare Ctrl-C still interrupts Claude. `execCommand('copy')`
into a throwaway textarea is the fallback, since `clipboard.writeText` needs a permission and a
focused document and refuses in an iframe in some browsers.

A selection also goes into **tmux's own paste buffer** (`/api/copybuffer`), which is a
different clipboard from the browser's: the browser's is the operator's own machine, tmux's
lives on the box and is what pastes between panes. tmux prints `copied N chars to tmux buffer`
in its status line, the way it does in a terminal on his laptop, and the dashboard shows a
small toast of its own — the status line is a row the iframe can cut off.

**Pasting an image** cannot be done by typing: an image is not text. The bytes go to
`/api/pasteimage`, which writes them under `<tmp>/zipper-pastes/` and returns the path, and the
*path* is what lands in the prompt — which is a thing Claude Code opens. Text paste is
untouched; ttyd already handles it.

The paste directory is capped at the last 40 files and lives in tmp: these are screenshots
dropped into a conversation, not vault content. Types are allowlisted (png/jpeg/gif/webp) and
the body is capped at 16MB.
