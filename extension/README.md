# Zipper Collector

A browser extension that reads what only a logged-in browser can see and hands
it to Zipper.

It exists because of one asymmetry. `canvas_session` is a cookie ASU rotates on
its own schedule — twice inside a day, more than once — so a copy pasted into
`.env` starts dying the moment it is taken. The *same* session sitting in a
browser never dies, because the browser renews it through SSO without being
asked. Nothing is wrong with `zipper/canvas.py`; it calls the right endpoint and
it works. Only the credential's lifetime fails. Moving the request into the
browser deletes lifetime as a concept.

## What it is not

It does not decide anything. A collector fetches JSON and posts it; every
judgement — what a submission means, which note it lands on, whether a status is
lying — happens in the backend, where the vault is. The browser is a **place to
read from**, not a second brain. Keep it that way: the moment a collector starts
interpreting, there are two systems that both think and they will disagree.

## Layout

```
manifest.json          both browsers, one file
background.js          the reporter. The only part that knows Zipper exists
collectors/canvas.js   one site. Fetches, posts, concludes nothing
ui/canvas-todo.js      draws Zipper's work list over Canvas' own
options.html/.js       where Zipper lives, granted as a runtime permission
```

Adding a site is one file in `collectors/` plus a `content_scripts` entry. The
reporter does not change.

`ui/` is the other direction: a collector reads the page for Zipper, a `ui/`
script draws Zipper into the page. Same rule applies — it renders a list it did
not rank and writes through an endpoint it does not own.

## When a reading is sent

A collector runs on every page load, and a page load is not news. What actually
goes out is decided in `background.js`, by hash:

| trigger | sent? |
|---|---|
| page load, reading differs from the last one Zipper acknowledged | **yes** |
| page load, reading identical | no |
| the 30-minute interval in an open tab | **yes, always** |

The hash is SHA-256 over a canonical serialization — keys sorted, so an upstream
reshuffle is not mistaken for a change, with a small `VOLATILE` denylist dropped
because `new_activity` flips when he merely *looks* at an item.

**The heartbeat ignores the hash on purpose.** Unchanged-and-read-just-now and
unchanged-and-read-this-morning are different facts, and `canvas.json`'s
`fetched` stamp can only tell them apart if something reports on a cadence. The
hash removes pointless traffic; it must not also freeze the clock.

The stored hash means *Zipper has this*, not *we tried* — written only after the
POST is acknowledged, so a failed send is offered again on the next load instead
of being skipped until the data happens to change. A 60-second floor sits under
the change-driven path as a backstop for two tabs loading together; the
heartbeat is never suppressed.

## The Canvas to-do panel

`ui/canvas-todo.js` replaces the dashboard sidebar's *To Do* and *Coming Up*
with **this Monday–Sunday week's Canvas work**, served at `GET /api/worklist`.
Ticking a row POSTs to `/api/done`, the same endpoint the dashboard's checkbox
uses, so a cross-off lands in the vault and not in a second store.

It is `data.week_worklist()`, built on the `week_canvas()` behind the
dashboard's week card — **not** `ranked()`, which is the dashboard's question:
everything pressing, coursework and `Tasks/` lines together, cut at ten. Inside
Canvas the surroundings have already answered half the question. He is looking
at a course tool, about this week, and a task about emailing three coffee shops
does not belong in a sidebar he opened to see assignments. Work due *before*
Monday and still unfinished leads the list, marked `still open`: it is this
week's problem whatever its own due date says.

Submitted and crossed-off rows stay — the vault's rule, since a row that
vanished would be indistinguishable from the cross-off having failed — but they
live behind a **Done** tab rather than in the list. Sinking them was not enough:
`week_worklist` sinks finished work *within* a day, so a Monday assignment
handed in on Monday still outranked an open one due Sunday, and a real week put
ten struck-through Sprint 0 rows above the two things he actually had to do.
The default view is what is left; what is finished is one click away.

Above the tabs, a bar for **how much of the week is behind him** — the one thing
a list of remaining work cannot show. Grade badges go on the course cards from
`/api/v1/courses?include[]=total_scores`; `--%` where Canvas has graded nothing
yet, because `0%` would be a different and wrong claim. That one never touches
Zipper: it is a number Canvas already computed, printed back onto Canvas' own
card. Sending it to the vault would be a different feature — a course score is
not a conclusion, and the vault holds conclusions.

That is the whole constraint. Canvas' native list is bad in a specific way — it
is the gradebook's ungraded columns, so it carries closed assignments and work
submitted on PrairieLearn — but **a second ranking computed in the browser
would be worse**, because two surfaces would then disagree about what is most
pressing on exactly the days it mattered. The browser draws; Zipper decides.

- Rendered in a **shadow root**. Canvas ships broad `!important` CSS and this
  panel sits inside its sidebar; an open DOM would be restyled remotely and
  silently.
- The native widgets are **hidden, not removed**, and only *after* a successful
  fetch. If Zipper is unreachable the panel says so and Canvas' own list is left
  exactly where it was — an empty box where his work used to be is the one
  failure worth engineering against.
- Re-mounted from a coalesced `MutationObserver`: the sidebar renders
  client-side, arrives after `document_idle`, and React reinstates hidden
  widgets on re-render.
- The page can reach exactly two paths, listed in `ALLOWED` in `background.js`.

## Install

**Chrome / Arc** — `chrome://extensions`, Developer mode on, *Load unpacked*,
choose this directory. Permanent, free, done.

**Firefox / Zen** — release Firefox **cannot permanently install an unsigned
extension**, and unlike older advice there is no `xpinstall.signatures.required`
override in release builds. Two real options:

- `about:debugging#/runtime/this-firefox` → *Load Temporary Add-on* → pick
  `manifest.json`. Fine for testing; **gone on restart.**
- Sign it through AMO as **unlisted / self-distributed** — free, interactive,
  not held up by review, never publicly listed. `web-ext sign --channel=unlisted`
  with an API key gives a `.xpi` that installs like any add-on.

Then open the extension's options and enter the dashboard's address — the
MagicDNS name rather than the tailnet IP, so it survives the address changing:

```
http://srv1441333.tail0dcbff.ts.net:8800
```

(Port 8800, not 4199. `zipper-web.service` binds `127.0.0.1:8800` and the enabled
nginx site proxies the tailnet address to it. The `4199`/`4200` blocks in
`sites-available/zipper` are a superseded config, not enabled, with nothing
listening behind them.)

Saving asks permission for that one origin; a
personal tailnet address does not belong in a manifest in a public repo, which
is why it is requested at runtime instead.

### Four traps that are not this extension's code

Getting it running in Zen on Fedora on 2026-09-17 cost four failures, none of
them in anything here. Each looked like a bug in the extension and none was.

- **A Flatpak browser cannot read the unpacked directory.** Picking
  `manifest.json` goes through the XDG portal, which grants access to *that file
  and nothing else*, so the manifest parses and every sibling comes back
  **empty**. The symptoms are a blank options page, `view-source` showing
  nothing, Quirks Mode (an empty document has no doctype) and the background
  module failing to load — one cause wearing four hats. Load from a directory
  the sandbox can see, `flatpak override --user --filesystem=...`, or install a
  signed `.xpi`, which is a single file and sidesteps the sandbox entirely.
- **HTTPS-Only Mode silently upgrades the endpoint.** Zen enables it by default.
  A `http://…:8800` address is rewritten to `https://`, the plain-HTTP server
  never completes the handshake, and `fetch` reports a generic CORS failure with
  `Status code: (null)`. A normal tab offers a "continue to HTTP site" prompt;
  an extension's `fetch` gets no prompt and just fails. **Read the scheme in the
  error message, not the one you typed** — that is the whole tell.
- The fix is a real certificate, not an exception: Tailscale issues one for the
  MagicDNS name, so the dashboard is served over TLS and the upgrade succeeds
  instead of failing. It runs on **port 9443** because **nginx already owns
  `0.0.0.0:443`** on that box for the public sites; `tailscaled` cannot bind it,
  the request falls through to nginx, and it answers with a public certificate
  that does not match the tailnet name.
- **`curl -I` proves nothing against this server.** It sends `HEAD`, which the
  handler does not implement, so a healthy box answers `501`. Use `-i`.

The general lesson is the one the *Two bugs worth not repeating* note already
makes about browsers: every one of these was invisible to every check that was
not the real browser on the real machine.

## Why Canvas is a content script

It must run *in the page*. `canvas_session` is a SameSite cookie, so a request
from the extension's background context is cross-site and the cookie is left
behind — Canvas then serves the SSO login page **at status 200**, and a caller
that trusts the status code parses a login form as an empty planner and reports
that nothing is due. From a content script the request is same-origin and the
session rides along, exactly as it does for the `/bookmarklet` this replaces.

The POST *out* is the mirror image and has to happen in the background: from the
page it would be a cross-origin request and CORS would refuse it; from the
background, host permissions apply and the browser does not interpose.

## What has actually been tried

Being explicit, because everything below the first line is untested and it
should not take a debugging session to find that out.

| | state |
|---|---|
| **Chrome / Arc, Canvas** | **working end to end**, verified 2026-09-08: 110 planner items with live submitted flags, `source: "extension"` in `canvas.json` |
| **Firefox / Zen, Canvas** | **working end to end**, verified 2026-09-17 on Fedora: same 110 items, `source: "extension"`. Loaded as a temporary add-on |
| **Hash-gated sending** | written 2026-09-15, **not yet watched in a browser.** The server half is fine and the logic is small, but nobody has confirmed that a second load is actually skipped or that the 30-minute heartbeat still arrives. Watch the background console for `skipped: 'unchanged'` before believing it |
| **The to-do panel** | **rendering**, Chrome 2026-09-15 and Zen 2026-09-17. Mounts into `#right-side`, hides the 2 native widgets, draws the week |
| **Tabs, the week bar, grade badges** | **rendering, verified 2026-09-17** — first sighting in any browser. `.ic-DashboardCard` and the `a[href*="/courses/"]` inside it were guesses from how Canvas builds its cards, and they were right |
| **Ticking through to `/api/done`** | **working both ways, verified 2026-09-17.** A tick and an untick each wrote `Inbox/overrides.json` and the store returned to its prior four entries, so the round trip closes and leaves nothing behind |
| **The unreachable-Zipper path** | still unexercised. Stop `zipper-web` and load Canvas to see it |
| **Hash-gated sending, watched** | still unwatched. A second load *appears* to skip — one `fetched` stamp survived several reloads — but a silent failure looks identical from the server. Only `skipped: 'unchanged'` in the background console tells them apart |

Two bugs worth not repeating, both invisible to every check that is not a
browser. `node --check` passes each content script in isolation, but an
extension gets **one isolated world per frame**, so a second file declaring
`const api` throws `already been declared` at line 1 and never runs at all —
hence the closure around each. And `all: initial` in a shadow root, which is
what walls Canvas' CSS out, also resets `display` to `inline` and collapses the
panel.
| **Signing / permanent install** | **not done.** Everything above was a temporary add-on, gone on restart. `web-ext sign --channel=unlisted` is still ahead |
| **Any site other than Canvas** | **nothing exists.** `collectors/` has one file. Onshape is an intention, not code. The "one collector per site" shape is a claim the second collector will test, and the reporter may well need changing when it arrives |

## The honest limitation

It only runs while a Canvas tab is open. Nothing here can make the data fresher
than his browsing. That is why the payload is stamped with when it was read —
`canvas.json` records `source: "extension"` and a `fetched` time, so a stale
reading can *say* it is stale instead of quietly implying currency. Compare the
failure it replaces: an expired `CANVAS_SESSION` left the submitted flags simply
wrong, with nothing on the page admitting it.

## Cross-browser notes

- One manifest declares **both** background forms — `service_worker` (Chrome)
  and `scripts` (Firefox event page). Each browser reads the key it understands;
  Chrome 121+ / Firefox 121+.
- `const api = globalThis.browser ?? globalThis.chrome;` covers the namespace.
- `browser_specific_settings.gecko` is required by Firefox and ignored by Chrome.
- **Chrome warns `'background.scripts' requires manifest version of 2 or lower`
  on load. That is expected and harmless** — it is Chrome telling you it does not
  understand Firefox's key, having already found and used `service_worker`. The
  warning is the price of one manifest for two browsers. If the noise ever
  matters, the fix is a build step emitting a per-browser manifest, not deleting
  the key: without `scripts` the extension has no background at all in Firefox.
- **Written for Chrome's service worker**, which is killed aggressively and keeps
  no globals. Hence no module-level state in `background.js` — everything that
  must outlive a wake-up is in `storage`. Firefox's event page does not need
  this, but code written Firefox-first breaks on Chrome, so the stricter target
  sets the rules.
