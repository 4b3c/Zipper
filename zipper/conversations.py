"""zipper.conversations

Many Claude conversations at once, one per Discord thread.

A Discord thread is a conversation. Everything else here follows from that:
its tmux session, its Claude session id, whether it is alive, and when it was
last spoken to. A message in a thread reaches the instance holding it; a
message in the channel starts a new one.

**Two conversations can edit the vault at the same time, and nothing stops
them.** Deliberate, 2026-09-06: parallel instances are cheap to run and the
locking to make them safe is not worth writing for one person who knows what
he has running. The failure it invites is real, though -- two sessions editing
one note, or committing over each other, produce conflicts and lost edits that
neither instance can see. If that starts happening, this is where the lock
goes; until then, don't work the same project in two threads at once.

**This file is the front door.** The implementation was split on 2026-09-07,
at 857 lines, into three modules whose dependencies run one way:

    convcore.py    identity, the registry, tmux naming, start/paste/deliver
    ttyd.py        one ttyd per conversation, and the ports they live on
    convstate.py   liveness, titles, listing, closing, the sweep and reaper

Import this module, not those: `conversations.listing()` and friends are used
from the dashboard, the bot, the CLI and the reply hook, and where a function
happens to live is not their business.
"""
from .convcore import *                                        # noqa: F401,F403
from .ttyd import *                                            # noqa: F401,F403
from .convstate import *                                       # noqa: F401,F403

# `import *` skips underscore names. These are internal but shared: the
# dashboard reads panes, and the tests reach for the port helpers.
from .convcore import (CONV_JSON, CLAUDE_PROJECTS, IDLE_NOTICE, NS,
                       _pane, _project_dir, _tmux, _wait_ready)
from .ttyd import TTYD_BASE, TTYD_SPAN, PROCS, _port_open, _pick_port
from .convstate import _ago, _TITLES, _MSGTIME, _SEEN
