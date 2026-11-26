#!/bin/sh
# Install the OpenCode Go usage widget for the current user.
#
#   ./install.sh
#
# Layout afterwards:
#   ~/.local/share/opencode-go-widget          (GTK app: card + data service)
#   ~/.local/share/gnome-shell/extensions/ocg-usage@local   (panel + popup)
#   ~/.config/autostart/opencode-go-widget.desktop
#   ~/.local/share/applications/opencode-go-widget.desktop
set -eu

SRC=$(cd "$(dirname "$0")" && pwd)
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
CONF="${XDG_CONFIG_HOME:-$HOME/.config}"
APPDIR="$DATA/opencode-go-widget"
EXTDIR="$DATA/gnome-shell/extensions/ocg-usage@local"
UUID="ocg-usage@local"

say() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# -- dependencies -----------------------------------------------------------
if ! python3 - <<'PY' >/dev/null 2>&1
import gi
import cairo
gi.require_version('Gtk', '3.0')
gi.require_version('AyatanaAppIndicator3', '0.1')
from gi.repository import Gtk, Gdk, GdkPixbuf, Pango, AyatanaAppIndicator3
PY
then
    say "Missing Python/GTK dependencies."
    say "On Ubuntu/Debian:"
    say "  sudo apt install python3-gi python3-cairo gir1.2-gtk-3.0 \\"
    say "      gir1.2-ayatanaappindicator3-0.1 gir1.2-notify-0.7"
    die "install those packages and re-run this script"
fi

# The shell module APIs used here were verified on GNOME 50/51.
if command -v gnome-shell >/dev/null 2>&1; then
    shell_ver=$(gnome-shell --version 2>/dev/null | awk '{print $3}' | cut -d. -f1 || true)
    case "${shell_ver:-}" in
        50|51) : ;;
        "") say "warning: could not detect the GNOME Shell version" ;;
        *) say "warning: this extension is tested on GNOME Shell 50/51, found $shell_ver" ;;
    esac
fi

# -- files ------------------------------------------------------------------
mkdir -p "$APPDIR" "$EXTDIR" "$CONF/autostart" "$DATA/applications"
cp -a "$SRC/app/." "$APPDIR/"
cp -a "$SRC/extension/." "$EXTDIR/"
chmod +x "$APPDIR/bin/opencode-go-widget"

# Desktop entries must carry an absolute path, so generate them here instead
# of copying the author's copy.
desktop() {
    cat > "$1" <<EOF
[Desktop Entry]
Type=Application
Name=OpenCode Go Usage
Comment=Panel ring gauge and desktop card for OpenCode Go usage
Exec="$APPDIR/bin/opencode-go-widget"
Icon=dialog-information
Terminal=false
Categories=Utility;
Keywords=opencode;usage;quota;tokens;
StartupNotify=false
$2
EOF
    chmod 644 "$1"
}
desktop "$DATA/applications/opencode-go-widget.desktop" ""
desktop "$CONF/autostart/opencode-go-widget.desktop" "X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=5"

# -- enable the shell extension --------------------------------------------
if command -v gnome-extensions >/dev/null 2>&1; then
    gnome-extensions enable "$UUID" >/dev/null 2>&1 || \
        say "note: enable the extension after logging in with: gnome-extensions enable $UUID"
else
    say "note: install gnome-shell-extensions-common for the gnome-extensions command"
fi

cat <<EOF

Installed.

  app:        $APPDIR
  extension:  $EXTDIR
  autostart:  $CONF/autostart/opencode-go-widget.desktop

Next:
  1. Log out and back in. A newly installed GNOME Shell extension is only
     loaded by a fresh shell session; running the app before that shows the
     old tray-icon fallback instead of the panel item.
  2. Click the ring in the top bar -> Settings... -> paste YOUR OpenCode Go
     API key (https://opencode.ai/auth) -> Test connection -> Save.
     The key is stored at ~/.config/opencode-go-widget/token with mode 0600.

To run it without logging out:
  $APPDIR/bin/opencode-go-widget
EOF
