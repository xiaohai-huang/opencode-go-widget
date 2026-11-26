"""Application wiring: polling, panel indicator, settings dialog."""

import json
import os
import threading
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import __version__, autostart, icon, service, usage  # noqa: E402
from .card import CardWindow, install_css  # noqa: E402
from .config import Config  # noqa: E402

EXTENSION_UUID = "ocg-usage@local"


def friendly_error(err):
    if isinstance(err, usage.AuthError):
        if "no api key" in str(err).lower():
            return "No API key configured"
        return "API key rejected — check Settings"
    if isinstance(err, usage.NetworkError):
        return "Can't reach opencode.ai — retrying"
    if isinstance(err, usage.ServerError):
        if err.status == 429:
            return "Rate limited by the API — retrying"
        if err.status:
            return f"API error (HTTP {err.status})"
        return str(err)
    if isinstance(err, usage.ParseError):
        return "Unexpected API response — see Settings → Test"
    return str(err) or "Unknown error"


class WidgetApp:
    def __init__(self, config=None, demo=None):
        install_css()
        self.config = config or Config.load()
        # Older builds wrote an autostart entry GNOME could not parse; fix it
        # in place so the app actually starts at login.
        autostart.ensure_current()
        self.snapshot = demo
        self.error = None
        self.fetching = False
        self.next_poll = 0.0
        self._failures = 0

        self.card = CardWindow(
            self.config,
            on_refresh=self.refresh,
            on_settings=self.open_settings,
            on_hide=self.hide_card,
            on_quit=self.quit,
        )
        self.card.connect("delete-event", self._on_delete)
        self.card.set_interval(self.config.get("poll_minutes"))

        # D-Bus surface for the GNOME Shell extension (data + actions).
        self.service = service.WidgetService(
            on_refresh=self.refresh,
            on_settings=self.open_settings,
            on_card=self.show_card,
            on_card_visible=self.set_card_visible,
            on_quit=self.quit,
        )
        if not self.service.start():
            print("opencode-go-widget: D-Bus service unavailable")

        # GNOME only discovers extensions at session start. Once the packaged
        # extension is known, enable it without making the user run a command.
        extension_enabled = self._ensure_extension_enabled()

        self.menu = None
        self.indicator = None
        # The shell extension draws the panel item when present; don't duplicate.
        if self.config.get("tray_indicator", True):
            if extension_enabled:
                # Enabling is asynchronous; only fall back if it did not load.
                GLib.timeout_add(1500, self._tray_fallback_if_needed)
            elif not self._extension_present():
                self._create_indicator()

        if not self.config.get("show_card"):
            self.card.hide()

        if demo is not None:
            self.card.remember(demo)
            self.card.show_snapshot(demo)
            self._update_indicator()
            self._publish()
        else:
            # Show the previous session's numbers while the first request is in
            # flight; at login the network often is not up yet.
            cached = self._cached_snapshot()
            if cached is not None:
                self.snapshot = cached
                self.card.remember(cached)
                self.card.show_snapshot(cached)
            self._update_indicator()
            self._publish()
            GLib.idle_add(self.refresh)

        # A login-time poll can beat NetworkManager; retry the moment the
        # network comes back instead of waiting out the retry interval.
        self._network = Gio.NetworkMonitor.get_default()
        self._network.connect("network-changed", self._on_network_changed)

        # Retries are checked on this tick; keep it short enough that the
        # few-seconds startup retry is not rounded up to a minute.
        GLib.timeout_add_seconds(5, self._tick)

    # -- polling -------------------------------------------------------------
    def refresh(self):
        if self.fetching:
            return False
        token = self.config.token
        if not token:
            self._on_result(None, usage.AuthError("no API key configured"))
            return False
        self.fetching = True
        self.card.set_busy(True)
        self._update_indicator()
        self._publish()
        threading.Thread(target=self._work, args=(token,), daemon=True).start()
        return False

    def _work(self, token):
        try:
            snapshot = usage.fetch(token)
            GLib.idle_add(self._on_result, snapshot, None)
        except usage.UsageError as err:
            GLib.idle_add(self._on_result, None, err)
        except Exception as err:  # never let the poller die silently
            GLib.idle_add(self._on_result, None, usage.UsageError(f"unexpected error: {err}"))

    def _on_result(self, snapshot, error):
        self.fetching = False
        self.card.set_busy(False)
        interval = max(1, int(self.config.get("poll_minutes") or 5)) * 60
        if snapshot is not None:
            self._failures = 0
            self.snapshot = snapshot
            self.error = None
            self.card.remember(snapshot)
            self.card.show_snapshot(snapshot)
            self.next_poll = time.time() + interval
        else:
            self.error = error
            self._failures += 1
            if isinstance(error, usage.NetworkError):
                # Boots and resumes often race the network stack: retry in a
                # few seconds, then back off up to a minute.
                retry = min(60, 5 * 2 ** min(self._failures - 1, 4))
            elif isinstance(error, usage.ServerError) and error.status == 429:
                retry = 60
            else:
                retry = interval
            self.next_poll = time.time() + retry
            if self.snapshot is not None:
                self.card.show_snapshot(self.snapshot, error=friendly_error(error))
            else:
                self.card.show_error(friendly_error(error))
        self._update_indicator()
        self._publish()
        return False

    def _on_network_changed(self, _monitor, available):
        if not available or self.fetching:
            return
        if self.snapshot is not None and self.error is None:
            return
        self.next_poll = 0
        self.refresh()

    def _cached_snapshot(self):
        """Load the last payload if it is recent enough to still be true.

        Deliberately ignores old caches: a 5-hour meter from yesterday would
        show a bogus number on the panel until the first fetch lands.
        """
        try:
            path = os.path.join(icon.CACHE_DIR, "status.json")
            with open(path, encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError):
            return None
        snapshot = service.WidgetService.snapshot_from_payload(payload)
        if snapshot is None:
            return None
        age = time.time() - snapshot.fetched_at
        limit = max(600, int(self.config.get("poll_minutes") or 5) * 180)
        return snapshot if 0 <= age <= limit else None

    def _tick(self):
        # If the shell extension shows up later, retire our tray icon.
        if self.indicator is not None and self._extension_present():
            try:
                self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.PASSIVE)
            except Exception:
                pass
            self.indicator = None
        if not self.fetching and time.time() >= self.next_poll:
            self.refresh()
        elif self.snapshot is not None:
            self.card.refresh_resets()
        return True

    # -- indicator -----------------------------------------------------------
    def _create_indicator(self):
        self.indicator = AyatanaAppIndicator3.Indicator.new(
            "opencode-go-widget",
            "dialog-information-symbolic",
            AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
        self.indicator.set_title("OpenCode Go")

    def _tray_fallback_if_needed(self):
        if self.indicator is None and not self._extension_present():
            self._create_indicator()
            self._update_indicator()
        return False

    @staticmethod
    def _extension_present():
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            reply = bus.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus",
                "org.freedesktop.DBus", "NameHasOwner",
                GLib.Variant("(s)", ("org.opencode_go.ShellUI",)),
                None, Gio.DBusCallFlags.NONE, 500, None)
            return bool(reply.unpack()[0])
        except Exception:
            return False

    @staticmethod
    def _extension_installed():
        data_home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        for base in (data_home, "/usr/share"):
            path = os.path.join(base, "gnome-shell", "extensions",
                                EXTENSION_UUID, "metadata.json")
            if os.path.isfile(path):
                return True
        return False

    def _ensure_extension_enabled(self):
        """Enable the packaged extension unless the user turned it off.

        GNOME only scans extension directories at session start, so this works
        from the first app start after login. If the extension is not known to
        the running shell yet, EnableExtension returns false and nothing
        changes; the tray icon covers the current session.
        """
        if not self._extension_installed():
            return False
        try:
            settings = Gio.Settings.new("org.gnome.shell")
            if EXTENSION_UUID in settings.get_strv("disabled-extensions"):
                return False
            if EXTENSION_UUID in settings.get_strv("enabled-extensions"):
                return True
        except Exception:
            return False
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            reply = bus.call_sync(
                "org.gnome.Shell.Extensions", "/org/gnome/Shell/Extensions",
                "org.gnome.Shell.Extensions", "EnableExtension",
                GLib.Variant("(s)", (EXTENSION_UUID,)),
                GLib.VariantType("(b)"), Gio.DBusCallFlags.NONE, 3000, None)
            return bool(reply.unpack()[0])
        except Exception:
            return False

    def show_card(self):
        self.config.set("show_card", True)
        self.config.save()
        self.card.show_card()
        self._publish()

    def set_card_visible(self, visible):
        if visible:
            self.show_card()
        else:
            self.hide_card()

    def _publish(self):
        """Push the current state to the D-Bus service used by the extension."""
        connected = self.snapshot is not None
        percent = self.snapshot.worst if connected else 0.0
        tinted = bool(self.config.get("tint_icon"))
        panel_icon = icon.build_icon(percent, tinted, connected)
        meter_icons = {}
        if self.snapshot is not None:
            for meter in self.snapshot.meters:
                meter_icons[meter.key] = icon.build_icon(
                    meter.percent, tinted, True, tag=meter.key)
        payload = service.WidgetService.build_payload(
            self.snapshot, self.error, panel_icon, meter_icons, friendly_error,
            card_visible=self.card.get_visible(), fetching=self.fetching)
        self.service.set_payload(payload)
        # Mirror to a file so the shell extension can also work without D-Bus
        # (and so a nested/dev shell can render real data).
        try:
            path = os.path.join(icon.CACHE_DIR, "status.json")
            os.makedirs(icon.CACHE_DIR, mode=0o700, exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp, path)
        except OSError as err:
            print(f"opencode-go-widget: could not write status file: {err}")

    def _update_indicator(self):
        if self.indicator is None:
            return
        connected = self.snapshot is not None
        percent = self.snapshot.worst if connected else 0.0
        path = icon.build_icon(percent, bool(self.config.get("tint_icon")), connected)
        self.indicator.set_icon_full(path, "OpenCode Go Widget")
        if self.fetching and not connected:
            title = "OpenCode Go · checking…"
        elif connected:
            title = f"OpenCode Go · {percent:.0f}% used"
        elif self.error is not None:
            title = f"OpenCode Go · {friendly_error(self.error)}"
        else:
            title = "OpenCode Go"
        self.indicator.set_title(title)
        self._rebuild_menu()

    def _menu_rows(self, now):
        """Plain text rows: the appindicator cannot host real columns, so this
        is only the fallback used when the GNOME Shell extension is absent."""
        return [
            f"{m.label}  {int(round(m.percent))}%  {usage.countdown_text(m, now)}"
            for m in self.snapshot.meters
        ]

    def _rebuild_menu(self):
        if self.indicator is None:
            # The Shell extension owns the panel item; no tray menu to build.
            return
        menu = Gtk.Menu()
        header = "OpenCode Go"
        if self.snapshot is not None and self.snapshot.plan:
            header += f" · {self.snapshot.plan}"
        elif self.config.get("plan_label"):
            header += f" · {self.config.get('plan_label')}"

        header_item = Gtk.MenuItem(label=header)
        header_item.set_sensitive(False)
        menu.append(header_item)

        if self.snapshot is not None:
            now = time.time()
            tinted = bool(self.config.get("tint_icon"))
            for line, meter in zip(self._menu_rows(now), self.snapshot.meters):
                # GtkImageMenuItem (deprecated, but this is the shape the
                # appindicator DBusMenu exporter knows how to carry icons for).
                item = Gtk.ImageMenuItem(label=line)
                path = icon.build_icon(meter.percent, tinted, True, tag=meter.key)
                item.set_image(Gtk.Image.new_from_file(path))
                item.set_always_show_image(True)
                item.connect("activate", lambda *_: self._open_console())
                menu.append(item)
        else:
            item = Gtk.MenuItem(label=self.error and friendly_error(self.error) or "Loading…")
            item.set_sensitive(False)
            menu.append(item)

        menu.append(Gtk.SeparatorMenuItem())

        show_item = Gtk.CheckMenuItem(label="Show desktop widget")
        show_item.set_active(self.card.get_visible())
        show_item.connect("toggled", self._on_toggle_card)
        menu.append(show_item)

        for label, callback in (
            ("Refresh now", lambda *_: self.refresh()),
            ("Settings", lambda *_: self.open_settings()),
        ):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", callback)
            menu.append(item)

        menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda *_: self.quit())
        menu.append(quit_item)

        menu.show_all()
        self.menu = menu  # keep a reference: the indicator does not own it
        self.indicator.set_menu(menu)

    def _open_console(self):
        uri = self.config.get("console_url") or "https://opencode.ai/auth"
        Gtk.show_uri_on_window(self.card if self.card.get_visible() else None, uri, Gdk.CURRENT_TIME)

    def _on_toggle_card(self, item):
        self.set_card_visible(item.get_active())

    def hide_card(self):
        self.card.hide()
        self.config.set("show_card", False)
        self.config.save()
        self._publish()
        self._rebuild_menu()

    def _on_delete(self, *_args):
        self.hide_card()
        return True

    # -- settings ------------------------------------------------------------
    def open_settings(self):
        if getattr(self, "_settings_dialog", None) is not None:
            self._settings_dialog.present()
            return
        # Deliberately not transient_for the card: a child of a keep-below
        # window can inherit that stacking and open behind other windows.
        dialog = Gtk.Dialog(title="OpenCode Go Widget — Settings")
        dialog.set_default_size(420, -1)
        dialog.set_position(Gtk.WindowPosition.CENTER)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        save_button = dialog.add_button("Save", Gtk.ResponseType.OK)
        save_button.get_style_context().add_class("suggested-action")
        area = dialog.get_content_area()
        area.set_spacing(10)
        area.set_border_width(16)

        grid = Gtk.Grid(column_spacing=12, row_spacing=10)
        area.pack_start(grid, False, False, 0)

        key_label = Gtk.Label(label="API key", xalign=0.0)
        self.key_entry = Gtk.Entry()
        self.key_entry.set_visibility(False)
        self.key_entry.set_text(self.config.token)
        self.key_entry.set_placeholder_text("paste your OpenCode Go key")
        self.key_entry.set_hexpand(True)
        self.key_entry.get_style_context().add_class("ocg-key")
        reveal = Gtk.CheckButton(label="Show")
        reveal.connect("toggled", lambda b: self.key_entry.set_visibility(b.get_active()))
        grid.attach(key_label, 0, 0, 1, 1)
        grid.attach(self.key_entry, 1, 0, 1, 1)
        grid.attach(reveal, 2, 0, 1, 1)

        link = Gtk.LinkButton.new_with_label("https://opencode.ai/auth", "Get a key in the OpenCode Console")
        grid.attach(link, 1, 1, 2, 1)

        poll_label = Gtk.Label(label="Check every", xalign=0.0)
        self.poll_spin = Gtk.SpinButton.new_with_range(1, 60, 1)
        self.poll_spin.set_value(int(self.config.get("poll_minutes") or 5))
        poll_unit = Gtk.Label(label="minutes", xalign=0.0)
        grid.attach(poll_label, 0, 2, 1, 1)
        grid.attach(self.poll_spin, 1, 2, 1, 1)
        grid.attach(poll_unit, 2, 2, 1, 1)

        self.tint_check = Gtk.CheckButton(label="Tint the panel icon by usage (blue → amber → red)")
        self.tint_check.set_active(bool(self.config.get("tint_icon")))
        self.on_top_check = Gtk.CheckButton(label="Float the card above other windows")
        self.on_top_check.set_active(bool(self.config.get("always_on_top")))
        self.autostart_check = Gtk.CheckButton(label="Start automatically at login")
        self.autostart_check.set_active(autostart.is_enabled())
        area.pack_start(self.tint_check, False, False, 0)
        area.pack_start(self.on_top_check, False, False, 0)
        area.pack_start(self.autostart_check, False, False, 0)

        test_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        test_button = Gtk.Button(label="Test connection")
        self.test_label = Gtk.Label(xalign=0.0)
        self.test_label.set_line_wrap(True)
        test_row.pack_start(test_button, False, False, 0)
        test_row.pack_start(self.test_label, True, True, 0)
        area.pack_start(test_row, False, False, 0)

        expander = Gtk.Expander(label="Raw API response")
        scroller = Gtk.ScrolledWindow()
        scroller.set_min_content_height(140)
        self.raw_view = Gtk.TextView()
        self.raw_view.set_editable(False)
        self.raw_view.set_monospace(True)
        scroller.add(self.raw_view)
        expander.add(scroller)
        area.pack_start(expander, False, False, 0)

        version_label = Gtk.Label(label=f"Version {__version__}", xalign=1.0)
        version_label.get_style_context().add_class("dim-label")
        area.pack_start(version_label, False, False, 0)

        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        area.pack_start(separator, False, False, 6)
        uninstall_button = Gtk.Button(label="Uninstall…")
        uninstall_button.get_style_context().add_class("destructive-action")
        uninstall_button.set_halign(Gtk.Align.START)
        uninstall_button.connect("clicked", self._on_uninstall_clicked)
        area.pack_start(uninstall_button, False, False, 0)

        test_button.connect("clicked", self._on_test_clicked)
        self._settings_dialog = dialog
        dialog.show_all()
        try:
            response = dialog.run()
            if response == Gtk.ResponseType.OK:
                self._save_settings()
        finally:
            # Must always run: a stale dialog would swallow its own buttons.
            self._settings_dialog = None
            dialog.destroy()

    def _save_settings(self):
        self.config.set_token(self.key_entry.get_text())
        minutes = int(self.poll_spin.get_value())
        self.config.set("poll_minutes", minutes)
        self.card.set_interval(minutes)
        self.config.set("tint_icon", self.tint_check.get_active())
        self.config.set("always_on_top", self.on_top_check.get_active())
        self.config.save()
        self.card.apply_stacking(self.on_top_check.get_active())
        autostart.set_enabled(self.autostart_check.get_active())
        self.next_poll = 0
        self.refresh()
        self._rebuild_menu()

    def _on_test_clicked(self, _button):
        token = self.key_entry.get_text().strip()
        self.test_label.set_text("Checking…")
        self.test_label.get_style_context().remove_class("ocg-error")

        def work():
            try:
                snapshot = usage.fetch(token)
                text = usage.describe(snapshot)
                raw = json.dumps(snapshot.raw, indent=2)[:4000]
                GLib.idle_add(self._on_test_done, f"OK — {len(snapshot.meters)} meters", text, raw, False)
            except usage.UsageError as err:
                GLib.idle_add(self._on_test_done, friendly_error(err), str(err), "", True)
            except Exception as err:
                GLib.idle_add(self._on_test_done, f"failed: {err}", str(err), "", True)

        threading.Thread(target=work, daemon=True).start()

    def _on_test_done(self, status, detail, raw, is_error):
        self.test_label.set_text(status)
        if is_error:
            self.test_label.get_style_context().add_class("ocg-error")
        buffer = self.raw_view.get_buffer()
        buffer.set_text(raw or detail or "")
        return False

    def _on_uninstall_clicked(self, _button):
        dialog = Gtk.MessageDialog(
            transient_for=self._settings_dialog,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE,
            text="Uninstall OpenCode Go Widget?",
        )
        dialog.format_secondary_text(
            "This quits the widget, disables the panel extension, and removes "
            "the autostart entry, cache, settings and API key. It does not "
            "remove the installed package.\n\n"
            "    sudo apt remove opencode-go-widget"
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        action = dialog.add_button("Remove Everything", Gtk.ResponseType.OK)
        action.get_style_context().add_class("destructive-action")
        dialog.show_all()
        response = dialog.run()
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return

        from . import uninstall
        uninstall.cleanup_user_state()

        result = Gtk.MessageDialog(
            transient_for=self._settings_dialog,
            modal=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.CLOSE,
            text="All user data has been removed",
        )
        result.format_secondary_text(
            "Remove the application itself with:\n\n"
            "    sudo apt remove opencode-go-widget"
        )
        result.show_all()
        result.run()
        result.destroy()
        self.quit()

    # -- lifecycle -----------------------------------------------------------
    def quit(self):
        # Gtk.Dialog.run() spins a nested main loop; close it first or Quit is
        # swallowed while Settings is open.
        dialog = getattr(self, "_settings_dialog", None)
        if dialog is not None:
            dialog.response(Gtk.ResponseType.CANCEL)
        try:
            self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.PASSIVE)
        except Exception:
            pass
        Gtk.main_quit()
