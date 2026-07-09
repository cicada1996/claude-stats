#!/bin/bash
# Build/refresh the "Claude Stats" desktop app (with icon) from this folder's code.
set -e
SRC="$(cd "$(dirname "$0")" && pwd)"
APP="/Applications/Claude Stats.app"
[ -w /Applications ] || APP="$HOME/Applications/Claude Stats.app"
mkdir -p "$HOME/Applications"
RES="$APP/Contents/Resources"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$RES"

# icon — generate if missing, then bundle it
[ -f "$SRC/AppIcon.icns" ] || /usr/bin/python3 "$SRC/make_icon.py"
cp "$SRC/AppIcon.icns" "$RES/AppIcon.icns"

# self-contained copy of the runtime
cp "$SRC/app.py" "$SRC/server.py" "$SRC/taskman.html" "$RES/"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Claude Stats</string>
  <key>CFBundleDisplayName</key><string>Claude Stats</string>
  <key>CFBundleIdentifier</key><string>local.claude-stats</string>
  <key>CFBundleExecutable</key><string>claude-stats</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1.2</string>
  <key>CFBundleShortVersionString</key><string>1.2</string>
  <key>LSMinimumSystemVersion</key><string>10.14</string>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST

cat > "$APP/Contents/MacOS/claude-stats" <<'SH'
#!/bin/bash
cd "$(cd "$(dirname "$0")/../Resources" && pwd)"
exec /usr/bin/python3 app.py >/tmp/claude-stats.log 2>&1
SH
chmod +x "$APP/Contents/MacOS/claude-stats"
touch "$APP"   # nudge the icon cache
echo "Installed: $APP"
