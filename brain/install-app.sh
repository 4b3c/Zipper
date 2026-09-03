#!/bin/bash
# Builds ~/Desktop/Brain.app — double-click to fetch, serve and open the dashboard.
# Regenerate any time; the bundle is disposable, this script is the source.
set -euo pipefail
VAULT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="${1:-$HOME/Desktop/Brain.app}"
# Finder launches apps with a minimal PATH, where python3 resolves to Apple's
# /usr/bin/python3 (3.9) rather than the interpreter this vault is tested on.
# Resolve it now and bake in the absolute path.
PY_BIN="$(command -v python3)"
[ -x "$PY_BIN" ] || { echo "python3 not found" >&2; exit 1; }
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Brain</string>
  <key>CFBundleDisplayName</key><string>Brain</string>
  <key>CFBundleIdentifier</key><string>com.example.brain</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>Brain</string>
  <key>LSUIElement</key><true/>
</dict></plist>
PLIST

cat > "$APP/Contents/MacOS/Brain" <<LAUNCH
#!/bin/bash
# Finder gives an app PATH=/usr/bin:/bin:/usr/sbin:/sbin, which has neither
# ~/.local/bin (claude) nor /opt/homebrew/bin (ttyd, tmux, gh). serve.py repairs
# this for itself too; setting it here keeps the whole process tree honest.
export PATH="\$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:\$PATH"
# One instance only: a second double-click just reopens the tab.
cd "$VAULT"
PORT=8800
if /usr/bin/curl -s -o /dev/null --max-time 1 "http://127.0.0.1:\$PORT/"; then
  exec /usr/bin/open "http://127.0.0.1:\$PORT/"
fi
exec "$PY_BIN" Scripts/serve.py --port "\$PORT" --open \\
     >> "\$HOME/Library/Logs/brain-dashboard.log" 2>&1
LAUNCH

chmod +x "$APP/Contents/MacOS/Brain"
echo "built $APP"
echo "vault:  $VAULT"
echo "python: $PY_BIN"
