#!/usr/bin/env bash
# Build dist/Scripto.app — a transparent launcher bundle (no PyInstaller).
# The bundle is a readable shell script that runs `uv run scripto` from this
# repo; `uv run` installs/syncs dependencies automatically on first launch.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
APP="$REPO/dist/Scripto.app"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>Scripto</string>
    <key>CFBundleDisplayName</key><string>Scripto</string>
    <key>CFBundleIdentifier</key><string>local.scripto.launcher</string>
    <key>CFBundleVersion</key><string>0.1.0</string>
    <key>CFBundleShortVersionString</key><string>0.1.0</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleExecutable</key><string>scripto-launcher</string>
    <key>CFBundleIconFile</key><string>AppIcon</string>
    <key>LSMinimumSystemVersion</key><string>12.0</string>
    <key>NSHighResolutionCapable</key><true/>
    <!-- The launcher execs `uv run scripto`, and `uv run` execs python, so
         the Qt GUI process *is* this bundle's process: the Dock entry below
         carries Scripto's name and icon with no runtime branding. -->
</dict>
</plist>
PLIST

# Launcher: embeds the repo path at build time; PATH is extended because
# GUI-launched apps do not inherit the shell PATH (uv/ffmpeg live in
# /opt/homebrew/bin or ~/.local/bin).
cat > "$APP/Contents/MacOS/scripto-launcher" <<SH
#!/bin/bash
REPO="$REPO"
LOG_DIR="\$HOME/Library/Application Support/Scripto/logs"
mkdir -p "\$LOG_DIR"
export PATH="/opt/homebrew/bin:/usr/local/bin:\$HOME/.local/bin:\$PATH"

if ! command -v uv >/dev/null 2>&1; then
  osascript -e 'display dialog "Scripto needs uv to run.\n\nInstall it in Terminal:\n  brew install uv\n\nThen open Scripto again." buttons {"OK"} default button 1 with title "Scripto"'
  exit 1
fi
if [ ! -d "\$REPO" ]; then
  osascript -e 'display dialog "Scripto repo not found at:\n$REPO\n\nRebuild the app with launchers/macos/build_app.sh after moving the folder." buttons {"OK"} default button 1 with title "Scripto"'
  exit 1
fi

cd "\$REPO"
exec uv run scripto >> "\$LOG_DIR/launcher.log" 2>&1
SH
chmod +x "$APP/Contents/MacOS/scripto-launcher"

# Icon: generated with the stdlib (replace the iconset to customize).
ICONSET="$(mktemp -d)/AppIcon.iconset"
python3 "$HERE/make_icon.py" "$ICONSET" >/dev/null
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"

plutil -lint "$APP/Contents/Info.plist" >/dev/null
echo "Built: $APP"
echo "Double-click it, or drag it into /Applications."
