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
options.html/.js       where Zipper lives, granted as a runtime permission
```

Adding a site is one file in `collectors/` plus a `content_scripts` entry. The
reporter does not change.

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
