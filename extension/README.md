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
with the list Zipper already computes — `data.ranked()`, the same call behind
the dashboard's "What to work on" card, served at `GET /api/worklist`. Ticking a
row POSTs to `/api/done`, the same endpoint the dashboard's checkbox uses, so a
cross-off lands in the vault and not in a second store.

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

**Firefox** — release Firefox **cannot permanently install an unsigned
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
| **Hash-gated sending** | written 2026-09-15, **not yet watched in a browser.** The server half is fine and the logic is small, but nobody has confirmed that a second load is actually skipped or that the 30-minute heartbeat still arrives. Watch the background console for `skipped: 'unchanged'` before believing it |
| **The to-do panel** | written 2026-09-15, **never rendered.** `/api/worklist` returns real rows (verified with curl), but the DOM half is unverified: the `#right-side` anchor and the four `SUPERSEDED` selectors are written from how Canvas is known to build that sidebar, not from looking at this install. **Expect the first load to need the selectors corrected** — inspect the sidebar and fix the list at the top of `ui/canvas-todo.js`. Everything else is independent of those names |
| **Firefox** | **never loaded, in any form.** Not once, not temporarily. The manifest is written for it and the reasoning is sound, but no line of this has run in Gecko. Assume the first attempt finds something |
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
