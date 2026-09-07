#!/usr/bin/env python3
"""Dashboard server for the vault.

Design notes, 2026-09-01:
  * every page load serves the LAST CACHED state instantly -- never a spinner
  * a refresh of the external sources is kicked off in the background
  * the page shows, per source, how old its data is, so staleness is visible
    rather than silent. Every bug this vault has produced was stale data
    presented as current.

No framework: stdlib only, so the VPS needs nothing but python3.
    python3 -m zipper.serve --port 8800

**This file is the entry point, not the server.** The server lives in
`zipper/web/`, one module per concern, because this file reached 2,788 lines
and no part of it could be read without loading all of it:

    web/base.py     shared imports, the PATH repair, STATE, source freshness
    web/data.py     what the panels are made of -- days, tasks, ranking, flags
    web/feed.py     the queue, the SSE bus, and the refresh
    web/conv.py     conversations as the page sees them; the terminal card
    web/css.py      the stylesheet, as a literal
    web/js.py       the browser code, as literals
    web/render.py   data to markup
    web/http.py     routing, the JSON API, and main()

They import in that order and the dependencies run one way, so any one of them
can be read on its own.

Everything is re-exported here, because `from . import serve` and
`python3 -m zipper.serve --mark <key>` are used by the CLI, by `runqueue.py`
and by the reply-forwarding hook. Moving code between modules must not change
what those see.
"""
from .web.base import *                                        # noqa: F401,F403
from .web.data import *                                        # noqa: F401,F403
from .web.feed import *                                        # noqa: F401,F403
from .web.conv import *                                        # noqa: F401,F403
from .web.render import *                                      # noqa: F401,F403
from .web.http import *                                        # noqa: F401,F403

# `import *` skips underscore names, and some of these are load-bearing outside
# their own module: FEED_JSON and FEED_MAX are read by runqueue.py, and the
# rest are what the page is built from.
from .web.base import STATE, LOCK, HERE, freshness, ago
from .web.conv import TTYD, PASTE_DIR, _queue_prompt
from .web.feed import (FEED, FEED_JSON, FEED_MAX, FEED_LOCK, SUBS, SUBS_LOCK,
                       feed_load, feed_rows, feed_mark, feed_mark_all,
                       feed_save, feed_prune, note_rows, publish,
                       snapshot_data, emit_diff, do_refresh)
from .web.css import CSS
from .web.js import JS, TICKJS
from .web.http import Handler, SRV, main

if __name__ == '__main__':
    import sys
    sys.exit(main() or 0)
