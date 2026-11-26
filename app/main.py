#!/usr/bin/env python3
"""OpenCode Go Widget: panel ring gauge + desktop card."""

import argparse
import os
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from ocg import __version__  # noqa: E402


def prefer_x11():
    """Wayland forbids client-side window placement, so the card lives on Xwayland."""
    if os.environ.get("GDK_BACKEND"):
        return
    if os.environ.get("DISPLAY") and os.path.exists("/tmp/.X11-unix/X0"):
        os.environ["GDK_BACKEND"] = "x11"


def demo_snapshot():
    from ocg import usage

    now = time.time()
    return usage.Snapshot(
        plan="Go Plus",
        fetched_at=now,
        meters=[
            usage.Meter("rolling", usage.DEFAULT_LABELS["rolling"], 41.0, reset_at=now + 8040),
            usage.Meter("weekly", "Weekly", 63.0, reset_at=now + 3 * 86400 + 5 * 3600),
            usage.Meter("monthly", "Monthly", 28.0, reset_at=now + 18 * 86400),
        ],
    )


def run_once(args):
    from ocg import usage
    from ocg.config import Config

    config = Config.load()
    token = (args.token or config.token).strip()
    if not token:
        print(
            "No API key found.\n\n"
            "Create a key at https://opencode.ai/auth, then either:\n"
            "  * launch the widget and paste it in Settings, or\n"
            "  * echo '<key>' > ~/.config/opencode-go-widget/token && chmod 600 ~/.config/opencode-go-widget/token\n"
            "  * or export OPENCODE_GO_API_KEY=<key>",
            file=sys.stderr,
        )
        return 2
    try:
        snapshot = usage.fetch(token)
    except usage.UsageError as err:
        print(f"OpenCode Go: {err}", file=sys.stderr)
        return 1
    print(usage.describe(snapshot))
    return 0


def run_uninstall():
    if os.geteuid() == 0:
        print("Run --uninstall as your desktop user, not with sudo.", file=sys.stderr)
        return 2
    from ocg import uninstall

    uninstall.stop_app()
    uninstall.cleanup_user_state()
    print("Removed the panel extension, autostart entry, cache, settings and API key.")
    print("\nRemove the package with:\n  sudo apt remove opencode-go-widget")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="opencode-go-widget", description="OpenCode Go Widget")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--uninstall", action="store_true",
                        help="remove all per-user state before removing the package")
    parser.add_argument("--once", action="store_true", help="print usage and exit (no GUI)")
    parser.add_argument("--demo", action="store_true", help="run with sample numbers")
    parser.add_argument("--no-card", action="store_true", help="start with the desktop card hidden")
    parser.add_argument("--token", help="API key for --once (prefer the token file or env)")
    parser.add_argument("--screenshot", metavar="PATH", help="capture the card to PATH and exit")
    args = parser.parse_args(argv)

    if args.uninstall:
        return run_uninstall()
    if args.once:
        return run_once(args)

    prefer_x11()

    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GLib, Gtk

    # The process is "python3 main.py"; without this the shell labels the
    # window "Main.py" and cannot match it to the desktop entry.
    GLib.set_prgname("opencode-go-widget")
    GLib.set_application_name("OpenCode Go Widget")
    Gdk.set_program_class("opencode-go-widget")
    Gtk.Window.set_default_icon_name("opencode-go-widget")

    from ocg.app import WidgetApp
    from ocg.config import Config, ensure_dir

    ensure_dir()
    config = Config.load()

    # Single instance: two widgets would fight over the card's saved position.
    import fcntl

    lock_path = os.path.join(
        os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
        "opencode-go-widget",
    )
    os.makedirs(lock_path, mode=0o700, exist_ok=True)
    lock_file = open(os.path.join(lock_path, "instance.lock"), "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("OpenCode Go widget is already running.", file=sys.stderr)
        return 0

    demo = demo_snapshot() if args.demo else None
    app = WidgetApp(config=config, demo=demo)
    if args.no_card:
        app.hide_card()

    if args.screenshot:
        def grab():
            window = app.card.get_window()
            if window is None:
                return False
            width, height = window.get_width(), window.get_height()
            pixbuf = Gdk.pixbuf_get_from_window(window, 0, 0, width, height)
            if pixbuf is not None:
                pixbuf.savev(args.screenshot, "png", [], [])
                print(f"wrote {args.screenshot} ({width}x{height})")
            app.quit()
            return False

        GLib.timeout_add(1500, grab)

    try:
        Gtk.main()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
