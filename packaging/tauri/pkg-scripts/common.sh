#!/bin/sh
# Shared by the installer scripts. Only identified toolbox bundles are handled.
APP_NAME="电力工具箱.app"
APP_ID="com.electricity.toolbox"
CANONICAL="/Applications/$APP_NAME"
CONSOLE_USER=$(/usr/bin/stat -f '%Su' /dev/console)
case "$CONSOLE_USER" in root|loginwindow|'') exit 0 ;; esac
USER_HOME=$(/usr/bin/dscl . -read "/Users/$CONSOLE_USER" NFSHomeDirectory | /usr/bin/sed 's/^NFSHomeDirectory: //')
MARKER="$USER_HOME/Library/Application Support/$APP_ID/update-source.txt"
plist_value() { /usr/libexec/PlistBuddy -c "Print :$2" "$1/Contents/Info.plist" 2>/dev/null; }
identified() { [ -d "$1" ] && [ ! -L "$1" ] && [ "$(plist_value "$1" CFBundleIdentifier)" = "$APP_ID" ]; }
candidates() {
  printf '%s\n' "$CANONICAL" "$USER_HOME/Desktop/$APP_NAME" "$USER_HOME/Downloads/$APP_NAME" "$USER_HOME/Applications/$APP_NAME"
  if [ -f "$MARKER" ] && [ ! -L "$MARKER" ]; then /usr/bin/head -n 1 "$MARKER"; fi
}
# Reject mounted volumes, canonical app, parent symlinks outside the home, and
# malformed paths. Never traverse arbitrary folders looking for applications.
retirable() {
  [ "$1" != "$CANONICAL" ] || return 1
  case "$1" in "$USER_HOME/"*.app) ;; *) return 1 ;; esac
  parent=$(CDPATH= cd -- "$(/usr/bin/dirname "$1")" && pwd -P) || return 1
  case "$parent/" in "$USER_HOME/"*) ;; *) return 1 ;; esac
  identified "$1"
}
