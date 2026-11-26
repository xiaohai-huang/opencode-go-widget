"""The desktop card: a small always-available, keep-below window."""

import math
import os
import tempfile
import time

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
try:
    gi.require_foreign("cairo")  # DrawingArea hands us a cairo.Context
except Exception:  # pragma: no cover - pygobject built without cairo
    pass
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from . import usage  # noqa: E402

WARN_AT = 70.0
DANGER_AT = 90.0
ICON_PX = 16


def install_css():
    provider = Gtk.CssProvider()
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "style.css")
    try:
        provider.load_from_path(path)
    except Exception as err:  # keep going without styling rather than not starting
        print(f"opencode-go-widget: could not load stylesheet: {err}")
        return
    screen = Gdk.Screen.get_default()
    Gtk.StyleContext.add_provider_for_screen(
        screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_USER
    )


class MeterRow(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.name = Gtk.Label(xalign=0.0)
        self.name.get_style_context().add_class("ocg-name")
        self.pct = Gtk.Label(xalign=1.0)
        self.pct.get_style_context().add_class("ocg-pct")
        top.pack_start(self.name, True, True, 0)
        top.pack_start(self.pct, False, False, 0)

        self.bar = Gtk.ProgressBar()
        self.bar.get_style_context().add_class("ocg-bar")
        self.bar.set_show_text(False)

        self.reset = Gtk.Label(xalign=0.0)
        self.reset.get_style_context().add_class("ocg-reset")
        self.reset.set_ellipsize(3)  # Pango.EllipsizeMode.END

        self.pack_start(top, False, False, 0)
        self.pack_start(self.bar, False, False, 0)
        self.pack_start(self.reset, False, False, 0)

    def update(self, meter, now=None):
        context = self.bar.get_style_context()
        for name in ("ocg-warn", "ocg-danger"):
            context.remove_class(name)
        if meter.percent >= DANGER_AT:
            context.add_class("ocg-danger")
        elif meter.percent >= WARN_AT:
            context.add_class("ocg-warn")

        self.name.set_text(meter.label)
        self.pct.set_text(f"{meter.percent:.0f}%")
        self.bar.set_fraction(max(0.0, min(1.0, meter.percent / 100.0)))
        line = usage.reset_line(meter, now)
        self.reset.set_text(line or "—")
        reset_context = self.reset.get_style_context()
        if meter.percent >= DANGER_AT:
            reset_context.add_class("warn")
        else:
            reset_context.remove_class("warn")


class ClickBox(Gtk.EventBox):
    """A windowed, hand-handled click target.

    GtkButton is windowless in GTK3. When its child owns an X window (a
    DrawingArea does) the press lands on the enclosing header instead and is
    treated as a drag. Giving the control its own window with an explicit event
    mask makes delivery unambiguous, independent of GTK's picking.
    """

    def __init__(self, on_click, tooltip=None, size=ICON_PX + 6):
        super().__init__()
        self.set_visible_window(True)
        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self.get_style_context().add_class("ocg-click")
        self.set_size_request(size, size)
        self._on_click = on_click
        self._pressed = False
        self._hover = False
        if tooltip:
            self.set_tooltip_text(tooltip)
        self.connect("button-press-event", self._press)
        self.connect("button-release-event", self._release)
        self.connect("enter-notify-event", self._enter)
        self.connect("leave-notify-event", self._leave)

    def _press(self, widget, event):
        if event.button == 1:
            self._pressed = True
            self.get_style_context().add_class("ocg-pressed")
        return True

    def _release(self, widget, event):
        if event.button == 1 and self._pressed:
            self._pressed = False
            self.get_style_context().remove_class("ocg-pressed")
            self._on_click()
        return True

    # -- hover ---------------------------------------------------------------
    # GTK only sets :hover for the control's own GdkWindow, but the drawn
    # icon sits in its own child window and covers almost the whole control.
    # Crossing events from the child are therefore tracked too, and the same
    # background is applied with a class.
    #
    # Crossing details are not reliable enough to decide "still inside": the
    # control is nested in the header's window, so moving onto the surrounding
    # header reports ANCESTOR just like moving back onto the control itself.
    # Every crossing schedules a positional check instead.
    def _enter(self, _widget, _event):
        self._set_hover(True)
        GLib.idle_add(self._sync_hover)
        return False

    def _leave(self, _widget, _event):
        GLib.idle_add(self._sync_hover)
        return False

    def _sync_hover(self):
        win = self.get_window()
        if win is None:
            return GLib.SOURCE_REMOVE
        try:
            pos = self.get_pointer()
            # PyGObject hands back a Gdk.Rectangle, not a tuple.
            x, y = pos.x, pos.y
        except Exception:
            return GLib.SOURCE_REMOVE
        alloc = self.get_allocation()
        self._set_hover(0 <= x < alloc.width and 0 <= y < alloc.height)
        return GLib.SOURCE_REMOVE

    def _set_hover(self, hover):
        if hover == self._hover:
            return
        self._hover = hover
        ctx = self.get_style_context()
        if hover:
            ctx.add_class("ocg-hover")
        else:
            ctx.remove_class("ocg-hover")

    def _track_hover(self, widget):
        """Watch crossings on the child windows too (DrawingAreas own one)."""
        if widget.get_has_window():
            widget.add_events(
                Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
            widget.connect("enter-notify-event", self._enter)
            widget.connect("leave-notify-event", self._leave)
        if isinstance(widget, Gtk.Container):
            for grandchild in widget.get_children():
                self._track_hover(grandchild)

    def set_child(self, child):
        old = self.get_child()
        if old is not None:
            self.remove(old)
        for edge in ("top", "bottom", "start", "end"):
            getattr(child, f"set_margin_{edge}")(3)
        self.add(child)
        self._track_hover(child)
        self.show_all()


class GlyphIcon(Gtk.DrawingArea):
    """Small hand-drawn glyph so it shares the gear's optical centre.

    Theme icons keep their own internal padding (window-minimize-symbolic draws
    its bar below centre), which makes it sit lower than a 16px cog.
    """

    def __init__(self, kind, size=ICON_PX, color=(1.0, 1.0, 1.0, 0.72)):
        super().__init__()
        self.kind = kind
        self.color = color
        self.set_size_request(size, size)
        # A drawing area gets its own X window. Without an explicit mask X
        # delivers clicks to the enclosing header instead, so the button never
        # sees them and the header treats the click as a drag.
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK)
        self.connect("draw", self._draw)

    def _draw(self, widget, cr):
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        cx, cy = width / 2.0, height / 2.0
        cr.set_line_width(1.7)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.set_source_rgba(*self.color)
        if self.kind == "minus":
            cr.move_to(cx - 5.0, cy)
            cr.line_to(cx + 5.0, cy)
        elif self.kind == "chevron-down":
            cr.move_to(cx - 3.5, cy - 2.0)
            cr.line_to(cx, cy + 1.8)
            cr.line_to(cx + 3.5, cy - 2.0)
        elif self.kind == "chevron-up":
            cr.move_to(cx - 3.5, cy + 2.0)
            cr.line_to(cx, cy - 1.8)
            cr.line_to(cx + 3.5, cy + 2.0)
        cr.stroke()
        return False


class RefreshButton(ClickBox):
    """Refresh control that spins its icon while a fetch is in flight."""

    ICON = "view-refresh-symbolic"
    STEP_DEGREES = 12.0

    def __init__(self, on_click):
        super().__init__(on_click=on_click, tooltip="Refresh now")
        self._angle = 0.0
        self._timer = None
        self._surface = self._load_icon()

        self.area = Gtk.DrawingArea()
        self.area.set_size_request(ICON_PX, ICON_PX)
        self.area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK)
        self.area.connect("draw", self._on_draw)
        if self._surface is None:
            # No cairo/pixbuf path available: fall back to a static icon rather
            # than an invisible control.
            self.set_child(Gtk.Image.new_from_icon_name(self.ICON, Gtk.IconSize.MENU))
        else:
            self.set_child(self.area)

    def _load_icon(self):
        # Pre-render once: symbolic icons are theme-coloured pixbufs, and we
        # rotate the resulting surface instead of asking the theme per frame.
        try:
            theme = Gtk.IconTheme.get_default()
            info = theme.lookup_icon(self.ICON, ICON_PX * 2, 0)
            if info is None:
                return None
            if info.is_symbolic():
                fg = Gdk.RGBA()
                fg.parse("rgba(255,255,255,0.72)")
                pixbuf = info.load_symbolic(fg, None, None, None)[0]
            else:
                pixbuf = info.load_icon()
            if pixbuf is None:
                return None
            # save_to_buffer is not available in this GdkPixbuf build, so do a
            # short-lived file round-trip; this runs once per process.
            path = os.path.join(tempfile.gettempdir(), f"ocg-spin-{os.getpid()}.png")
            try:
                pixbuf.savev(path, "png", [], [])
                return cairo.ImageSurface.create_from_png(path)
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass
        except Exception as err:
            print(f"opencode-go-widget: refresh icon unavailable: {err}")
            return None

    # -- animation -----------------------------------------------------------
    def set_busy(self, busy):
        if busy and self._timer is None:
            self._timer = GLib.timeout_add(33, self._tick)
            self.set_tooltip_text("Refreshing…")
        elif not busy and self._timer is not None:
            GLib.source_remove(self._timer)
            self._timer = None
            self._angle = 0.0
            self.area.queue_draw()
            self.set_tooltip_text("Refresh now")

    @property
    def busy(self):
        return self._timer is not None

    def _tick(self):
        self._angle = (self._angle + self.STEP_DEGREES) % 360.0
        self.area.queue_draw()
        return True

    def _on_draw(self, widget, cr):
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        if self._surface is None:
            return False
        sw, sh = self._surface.get_width(), self._surface.get_height()
        cr.save()
        cr.translate(width / 2.0, height / 2.0)
        cr.rotate(math.radians(self._angle))
        # Scale the source pixels down into the logical icon box.
        cr.scale(ICON_PX / float(sw), ICON_PX / float(sh))
        cr.set_source_surface(self._surface, -sw / 2.0, -sh / 2.0)
        cr.paint()
        cr.restore()
        return False


class CardWindow(Gtk.Window):
    def __init__(self, config, on_refresh, on_settings, on_hide, on_quit):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_title("OpenCode Go Widget")
        self.config = config
        self.on_refresh = on_refresh
        self.on_settings = on_settings
        self.on_hide = on_hide
        self.on_quit = on_quit
        self._drag = None
        self._last_good = None
        self._interval = max(1, int(config.get("poll_minutes") or 5))
        self.collapsed = False

        self.set_decorated(False)
        self.set_resizable(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.set_app_paintable(True)
        # Never steal keyboard focus from whatever the user is typing in.
        self.set_accept_focus(False)
        # Without this GTK asks the WM to activate the window on map, which
        # mutter answers with _NET_WM_STATE_DEMANDS_ATTENTION.
        self.set_focus_on_map(False)
        self.apply_stacking(self.config.get("always_on_top"))
        self.get_style_context().add_class("ocg-card")

        screen = self.get_screen()
        if screen.is_composited():
            visual = screen.get_rgba_visual()
            if visual is not None:
                self.set_visual(visual)

        # The card surface must NOT have its own GdkWindow: a window between the
        # pointer and the toplevel stops GTK from picking windowless children
        # (ordinary GtkButtons), so they would never receive clicks.
        self.surface = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.surface.get_style_context().add_class("ocg-surface")
        for edge in ("top", "bottom", "left", "right"):
            getattr(self.surface, f"set_margin_{edge}")(12)
        self.add(self.surface)
        self.set_size_request(322, -1)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        # Same top margin as set_collapsed() sets: the header sits at the same
        # height whether the card starts expanded or is expanded later.
        content.set_margin_top(10)
        content.set_margin_bottom(12)
        content.set_margin_start(16)
        content.set_margin_end(16)
        self.content = content
        self.surface.pack_start(content, False, False, 0)

        # -- header (also the drag handle) ----------------------------------
        header = Gtk.EventBox()
        header.set_visible_window(True)
        header.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.POINTER_MOTION_MASK
                          | Gdk.EventMask.BUTTON_RELEASE_MASK)
        header.connect("button-press-event", self._on_header_press)
        header.connect("motion-notify-event", self._on_motion)
        header.connect("button-release-event", self._on_release)
        header_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="OpenCode Go", xalign=0.0)
        title.get_style_context().add_class("ocg-title")
        self.badge = Gtk.Label(xalign=0.0)
        self.badge.get_style_context().add_class("ocg-badge")
        self.badge.set_no_show_all(True)
        self.min_button = ClickBox(lambda: self.set_collapsed(not self.collapsed), tooltip="Minimize")
        self._set_min_icon(False)
        header_row.pack_start(title, False, False, 0)
        header_row.pack_start(self.badge, False, False, 0)
        header_row.pack_end(self.min_button, False, False, 0)
        header.add(header_row)
        self.header = header
        content.pack_start(header, False, False, 0)

        # Everything below the header collapses into the revealer, so
        # "minimizing" leaves the title bar as the click/drag target.
        self.revealer = Gtk.Revealer()
        self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.revealer.set_transition_duration(160)
        self.revealer.set_reveal_child(True)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.revealer.add(body)
        content.pack_start(self.revealer, False, False, 0)

        body.pack_start(self._separator(), False, False, 0)

        # -- meters ---------------------------------------------------------
        self.rows = {}
        self.rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        for key in usage.CANONICAL:
            row = MeterRow()
            self.rows[key] = row
            self.rows_box.pack_start(row, False, False, 0)
        body.pack_start(self.rows_box, False, False, 0)

        # -- error / empty state --------------------------------------------
        self.error_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.error_label = Gtk.Label(xalign=0.0)
        self.error_label.set_line_wrap(True)
        self.error_label.set_max_width_chars(38)
        self.error_label.get_style_context().add_class("ocg-error")
        self.error_button = ClickBox(lambda: self.on_settings(), size=-1)
        self.error_button.get_style_context().add_class("ocg-textbox")
        self.error_button.set_halign(Gtk.Align.START)
        self.error_button_label = Gtk.Label(label="Add API key")
        self.error_button_label.get_style_context().add_class("ocg-textbtn-label")
        self.error_button.set_child(self.error_button_label)
        self.error_box.pack_start(self.error_label, False, False, 0)
        self.error_box.pack_start(self.error_button, False, False, 0)
        self.error_box.set_no_show_all(True)
        body.pack_start(self.error_box, False, False, 0)

        body.pack_start(self._separator(), False, False, 0)

        # -- footer ---------------------------------------------------------
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.updated = Gtk.Label(xalign=0.0)
        self.updated.get_style_context().add_class("ocg-muted")
        self.refresh_button = RefreshButton(self.on_refresh)
        footer.pack_start(self.updated, True, True, 0)
        footer.pack_end(self.refresh_button, False, False, 0)
        body.pack_start(footer, False, False, 0)

        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
        )
        self.connect("button-press-event", self._on_window_press)
        self.connect("motion-notify-event", self._on_motion)
        self.connect("button-release-event", self._on_release)
        self._build_context_menu()

        self.show_all()
        self.badge.hide()
        self.error_box.hide()

        # Pointer-crossing events never reach the toplevel here (every part of
        # the card is a child GdkWindow), and Wayland offers no pointer query,
        # so there is deliberately no hover behaviour.
        GLib.idle_add(self._place)
        if self.config.get("collapsed"):
            self.set_collapsed(True, animate=False, remember=False)

    # -- construction helpers ----------------------------------------------
    @staticmethod
    def _separator():
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.get_style_context().add_class("ocg-sep")
        return sep

    def _build_context_menu(self):
        self.menu = Gtk.Menu()
        self.menu_collapse = Gtk.MenuItem(label="Minimize")
        self.menu_collapse.connect("activate", lambda *_: self.set_collapsed(not self.collapsed))
        self.menu.append(self.menu_collapse)
        for label, callback in (
            ("Refresh now", lambda *_: self.on_refresh()),
            ("Settings", lambda *_: self.on_settings()),
            ("Hide card", lambda *_: self.on_hide()),
            ("Quit", lambda *_: self.on_quit()),
        ):
            if label == "Quit":
                self.menu.append(Gtk.SeparatorMenuItem())
            item = Gtk.MenuItem(label=label)
            item.connect("activate", callback)
            self.menu.append(item)
        self.menu.show_all()

    # -- placement / dragging ----------------------------------------------
    def _clear_urgency(self):
        # Mutter applies focus-stealing prevention *after* the map request, and
        # GTK only sends an urgency change when its cached value differs — so
        # toggle it to force the client message, then clear it again.
        def do_clear():
            self.set_urgency_hint(True)
            self.set_urgency_hint(False)
            return False

        self.set_urgency_hint(False)
        GLib.timeout_add(1500, do_clear)

    def apply_stacking(self, on_top):
        """On top by default: a usage HUD is useless under a full-screen editor."""
        self.set_keep_below(not on_top)
        self.set_keep_above(bool(on_top))
        if on_top and self.get_visible():
            self.present()
            self._clear_urgency()

    def show_card(self):
        self.show_all()
        self.badge.hide()
        self.error_box.hide()
        self.present()
        self._clear_urgency()

    # -- minimize -----------------------------------------------------------
    def set_collapsed(self, collapsed, animate=True, remember=True):
        """Minimize to the title bar; click the bar or the button to expand."""
        collapsed = bool(collapsed)
        self.collapsed = collapsed
        duration = self.revealer.get_transition_duration()
        if not animate:
            self.revealer.set_transition_duration(0)
        self.revealer.set_reveal_child(not collapsed)
        if not animate:
            self.revealer.set_transition_duration(duration)

        self._set_min_icon(collapsed)
        self.menu_collapse.set_label("Expand" if collapsed else "Minimize")
        # The header's offset from the window top must not change with state,
        # and in the minimized bar the title row has to sit exactly in the
        # middle: 10px above and below the header, so only the space *below*
        # the body changes between states.
        self.content.set_margin_top(10)
        self.content.set_margin_bottom(10 if collapsed else 12)
        self.content.set_spacing(0 if collapsed else 10)

        if remember:
            self.config.set("collapsed", collapsed)
            self.config.save()

    def _set_min_icon(self, collapsed):
        self.min_button.set_child(GlyphIcon("chevron-down" if collapsed else "minus"))
        self.min_button.set_tooltip_text("Expand" if collapsed else "Minimize")

    def set_busy(self, busy):
        self.refresh_button.set_busy(busy)

    def _place(self):
        x = self.config.get("card_x")
        y = self.config.get("card_y")
        if x is None or y is None:
            display = Gdk.Display.get_default()
            monitor = display.get_primary_monitor() or display.get_monitor(0)
            geometry = monitor.get_geometry()
            x = geometry.x + geometry.width - 298 - 30
            y = geometry.y + 44
        self.move(int(x), int(y))
        return False

    def _on_header_press(self, widget, event):
        # Only presses on the header's own window are drags/clicks. Events from
        # child widgets (gear, minimize) propagate here afterwards and must not
        # start a drag.
        if event.window is not widget.get_window():
            return False
        if event.button == 3:
            self.menu.popup_at_pointer(event)
            return True
        if event.button == 1:
            wx, wy = self.get_position()
            self._drag = (event.x_root, event.y_root, wx, wy)
            self._drag_moved = False
        return False

    def _on_motion(self, widget, event):
        if not self._drag:
            return False
        # Safety net: if the release happened somewhere we never saw, drop the
        # drag rather than tracking an idle pointer.
        if not (event.state & Gdk.ModifierType.BUTTON1_MASK):
            self._drag = None
            return False
        origin_x, origin_y, win_x, win_y = self._drag
        dx = event.x_root - origin_x
        dy = event.y_root - origin_y
        if abs(dx) > 3 or abs(dy) > 3:
            self._drag_moved = True
        self.move(int(win_x + dx), int(win_y + dy))
        return True

    def _on_release(self, widget, event):
        started_on_header = self._drag is not None
        moved = getattr(self, "_drag_moved", False)
        if self._drag:
            if moved:
                x, y = self.get_position()
                self.config.set("card_x", x)
                self.config.set("card_y", y)
                self.config.save()
            self._drag = None
        # A click (not a drag) on the minimized bar expands it, so the whole bar
        # is a target in addition to the chevron button. Only clicks that began
        # on the header count — a release propagated from a button must not
        # undo that button's own action.
        if started_on_header and event.button == 1 and not moved and self.collapsed:
            self.set_collapsed(False)
        return False

    def _on_window_press(self, widget, event):
        # Presses on the card's own window (margins, body, labels) drag the card
        # too; the header handler also drags, so guard against re-entry. Events
        # that started on a child's own GdkWindow (the buttons) must not arm the
        # drag: otherwise their release would be seen as a click on the title
        # bar and undo the button's action.
        if event.button == 3:
            self.menu.popup_at_pointer(event)
            return True
        if event.button == 1 and self._drag is None and event.window is widget.get_window():
            wx, wy = self.get_position()
            self._drag = (event.x_root, event.y_root, wx, wy)
            self._drag_moved = False
        return False

    # -- data ---------------------------------------------------------------
    def set_interval(self, minutes):
        self._interval = max(1, int(minutes or 5))

    def show_error(self, message, snapshot=None):
        if snapshot is None:
            self.rows_box.hide()
            # no_show_all blocks show_all(), so show the pieces explicitly.
            self.error_box.show()
            self.error_label.show()
            self.error_button.show()
            self.error_label.set_text(message)
            self.error_button_label.set_text("Add API key" if "key" in message.lower() else "Open settings")
            self.updated.set_text("not connected")
        else:
            self.show_snapshot(snapshot, error=message)

    def show_snapshot(self, snapshot, error=None, now=None):
        now = time.time() if now is None else now
        self._last_good = snapshot.fetched_at
        self.rows_box.show_all()
        self.error_box.hide()

        if snapshot.plan:
            self.badge.set_text(snapshot.plan)
            self.badge.show()
        else:
            self.badge.hide()

        present = set()
        for meter in snapshot.meters:
            row = self.rows.get(meter.key)
            if row is None:
                continue
            row.update(meter, now)
            row.set_visible(True)
            present.add(meter.key)
        for key, row in self.rows.items():
            if key not in present:
                row.set_visible(False)

        stale = now - snapshot.fetched_at > max(60, self._interval * 60 * 3)
        self.set_opacity(0.78 if stale else 1.0)
        if error:
            self.updated.set_text(error)
        else:
            clock = time.strftime("%H:%M", time.localtime(snapshot.fetched_at))
            self.updated.set_text(f"Updated {clock}" + (" · stale" if stale else ""))

    def refresh_resets(self, now=None):
        """Tick the countdowns without touching the numbers."""
        if not self._last_good:
            return
        # Countdowns are derived from reset_at inside MeterRow, so a cheap
        # re-render of the cached snapshot keeps them honest between polls.
        if getattr(self, "_snapshot", None) is not None:
            self.show_snapshot(self._snapshot, now=now)

    def remember(self, snapshot):
        self._snapshot = snapshot
