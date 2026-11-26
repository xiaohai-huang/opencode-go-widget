"""Login autostart entry management.

The source of truth is the XDG autostart file itself: the checkbox in Settings
reads whether it exists, and the app writes or removes it on save. That keeps
the switch honest even if the file is added or removed by the installer, a
desktop tool, or by hand.
"""

import os

NAME = "opencode-go-widget.desktop"


def config_dir():
    return os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")


def app_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def launcher_path():
    return os.path.join(app_dir(), "bin", "opencode-go-widget")


def autostart_path():
    return os.path.join(config_dir(), "autostart", NAME)


def is_enabled():
    return os.path.isfile(autostart_path())


def _entry():
    # The Exec path must be absolute: a session-manager launch has no useful
    # working directory, and the app's launcher resolves its own location.
    return f"""[Desktop Entry]
Type=Application
Name=OpenCode Go Widget
Comment=Panel ring gauge and desktop card for OpenCode Go usage
Exec="{launcher_path()}"
Icon=opencode-go-widget
Terminal=false
Categories=Utility;
StartupNotify=false
StartupWMClass=opencode-go-widget
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=5
"""


def set_enabled(enabled):
    """Create or remove the autostart entry. Returns True on success."""
    path = autostart_path()
    if not enabled:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError as err:
            print(f"opencode-go-widget: could not remove autostart entry: {err}")
            return False
        return True

    try:
        os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(_entry())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
        return True
    except OSError as err:
        print(f"opencode-go-widget: could not write autostart entry: {err}")
        return False
