"""Remove the per-user state that a package manager leaves behind."""

import os
import shutil
import time

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio  # noqa: E402

from . import autostart  # noqa: E402
from .config import CONFIG_DIR  # noqa: E402

UUID = "ocg-usage@local"
BUS_NAME = "org.opencode_go.Widget"
OBJECT_PATH = "/org/opencode_go/Widget"
CACHE_DIR = os.path.join(
    os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
    "opencode-go-widget",
)


def stop_app():
    """Ask a running widget to quit, then give it a moment to release its lock."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call_sync(
            BUS_NAME, OBJECT_PATH, BUS_NAME, "Quit",
            None, None, Gio.DBusCallFlags.NONE, 1000, None)
    except Exception:
        return
    time.sleep(0.75)


def disable_extension():
    """Unload the panel extension and drop its GNOME Shell setting entries.

    Changing the setting is enough: GNOME Shell watches it and unloads the
    extension. Calling DisableExtension would race with the shell's own write
    to disabled-extensions and leave a stale UUID behind.
    """
    try:
        settings = Gio.Settings.new("org.gnome.shell")
        for key in ("enabled-extensions", "disabled-extensions"):
            values = settings.get_strv(key)
            filtered = [value for value in values if value != UUID]
            if filtered != values:
                settings.set_strv(key, filtered)
    except Exception:
        pass


def cleanup_user_state():
    """Disable the extension and remove all per-user state."""
    disable_extension()

    # Older per-user installs put the extension here; the package now uses the
    # system-wide copy.
    data_home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    shutil.rmtree(
        os.path.join(data_home, "gnome-shell", "extensions", UUID),
        ignore_errors=True)

    autostart.set_enabled(False)
    shutil.rmtree(CACHE_DIR, ignore_errors=True)
    shutil.rmtree(CONFIG_DIR, ignore_errors=True)
