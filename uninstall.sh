#!/bin/sh
# Remove the OpenCode Go usage widget, its extension and its settings.
# Pass --keep-key to leave ~/.config/opencode-go-widget alone.
set -eu

DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
CONF="${XDG_CONFIG_HOME:-$HOME/.config}"
APPDIR="$DATA/opencode-go-widget"
EXTDIR="$DATA/gnome-shell/extensions/ocg-usage@local"
UUID="ocg-usage@local"
KEEP_KEY=0

[ "${1:-}" = "--keep-key" ] && KEEP_KEY=1

if command -v gnome-extensions >/dev/null 2>&1; then
    gnome-extensions disable "$UUID" >/dev/null 2>&1 || true
fi
pkill -f "opencode-go-widget/main.py" >/dev/null 2>&1 || true

rm -rf "$APPDIR" "$EXTDIR"
rm -f "$CONF/autostart/opencode-go-widget.desktop" \
      "$DATA/applications/opencode-go-widget.desktop"

if [ "$KEEP_KEY" -eq 1 ]; then
    echo "Removed the widget and extension; kept ~/.config/opencode-go-widget."
else
    rm -rf "$CONF/opencode-go-widget" "$HOME/.cache/opencode-go-widget"
    echo "Removed the widget, extension, settings and stored API key."
fi
echo "Log out and back in to drop the panel item."
