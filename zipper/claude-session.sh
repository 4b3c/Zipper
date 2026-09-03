#!/bin/bash
# Launched inside ttyd at startup — the terminal must appear at once, not after
# the GitHub fetch. Waits for the refresh to drop this run's queue into the
# ready-file, then starts Claude primed with it.
# The code no longer lives inside the vault, so the session has to be told
# where the notes are rather than inferring it from its own location.
cd "${ZIPPER_VAULT:-$(dirname "${BASH_SOURCE[0]}")/..}" || exit 1

# Zipper.app inherits Finder's minimal PATH, which has neither ~/.local/bin
# (claude) nor /opt/homebrew/bin. Same trap that stopped the app launching.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
CLAUDE="$(command -v claude)"
if [ -z "$CLAUDE" ]; then
  printf '\033[31mclaude not found on PATH.\033[0m Dropping to a shell in the vault.\n\n'
  exec bash -l
fi

READY="${1:-}"
if [ -n "$READY" ]; then
  printf '\033[2mZipper: waiting for this run to finish fetching…\033[0m\n'
  for _ in $(seq 1 240); do [ -f "$READY" ] && break; sleep 0.5; done
  if [ -s "$READY" ]; then
    printf '\033[2mqueue ready — starting Claude with it\033[0m\n\n'
    exec "$CLAUDE" "$(cat "$READY")"
  fi
  printf '\033[2mnothing to consume — blank session\033[0m\n\n'
fi
exec "$CLAUDE"
