"""Ring-gauge icon rendering for the panel indicator."""

import io
import math
import os
import time

import cairo
import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio  # noqa: E402

CACHE_DIR = os.path.join(
    os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
    "opencode-go-widget",
)

ACCENT = "#3584e4"
WARN = "#e5a50a"
DANGER = "#e01b24"
DARK_FG = "#ffffff"
LIGHT_FG = "#2e3436"

WARN_AT = 70.0
DANGER_AT = 90.0


def is_dark_theme():
    try:
        settings = Gio.Settings.new("org.gnome.desktop.interface")
        scheme = settings.get_string("color-scheme")
        if scheme == "prefer-dark":
            return True
        if scheme == "prefer-light":
            return False
        if "dark" in (settings.get_string("gtk-theme") or "").lower():
            return True
    except Exception:
        pass
    try:
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
        settings = Gtk.Settings.get_default()
        return bool(settings.get_property("gtk-application-prefer-dark-theme"))
    except Exception:
        return True


def hex_to_rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def level_color(percent):
    if percent >= DANGER_AT:
        return DANGER
    if percent >= WARN_AT:
        return WARN
    return ACCENT


def icon_color(percent, tinted=True, dark=None):
    if tinted:
        return level_color(percent)
    dark = is_dark_theme() if dark is None else dark
    return DARK_FG if dark else LIGHT_FG


def render(size, percent=None, color="#ffffff", track_alpha=0.22, dim=False):
    """Draw a ring gauge. percent=None renders a hollow 'no data' ring."""
    supersample = 4
    big = size * supersample
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, big, big)
    cr = cairo.Context(surface)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)

    stroke = big * 0.11
    center = big / 2.0
    radius = center - stroke * 0.75
    red, green, blue = hex_to_rgb(color)

    # Track
    cr.set_line_width(stroke)
    cr.new_path()
    cr.arc(center, center, radius, 0, math.tau)
    cr.set_source_rgba(red, green, blue, track_alpha if not dim else track_alpha * 0.6)
    cr.stroke()

    if percent is not None and percent > 0.05:
        fraction = max(0.0, min(1.0, percent / 100.0))
        cr.set_line_width(stroke)
        cr.set_source_rgba(red, green, blue, 0.45 if dim else 1.0)
        cr.new_path()
        start = -math.pi / 2
        cr.arc(center, center, radius, start, start + fraction * math.tau)
        cr.stroke()
    elif percent is None:
        # Hollow ring: a small centre dot marks "no data".
        cr.set_source_rgba(red, green, blue, 0.45)
        cr.arc(center, center, big * 0.055, 0, math.tau)
        cr.fill()

    surface.flush()
    # Round-trip through PNG: Cairo stores premultiplied BGRA on little-endian
    # machines, so handing its bytes straight to GdkPixbuf would swap R and B.
    blob = io.BytesIO()
    surface.write_to_png(blob)
    loader = GdkPixbuf.PixbufLoader.new_with_type("png")
    loader.write(blob.getvalue())
    loader.close()
    return loader.get_pixbuf().scale_simple(size, size, GdkPixbuf.InterpType.HYPER)


def ensure_cache_dir():
    os.makedirs(CACHE_DIR, mode=0o700, exist_ok=True)
    return CACHE_DIR


def build_icon(percent, tinted=True, connected=True, tag=None):
    """Render the panel icon and return its path (the shell wants a file/name)."""
    ensure_cache_dir()
    color = icon_color(percent or 0.0, tinted) if connected else icon_color(0, False)
    pixbuf = render(64, None if not connected else percent, color)
    stem = f"ocg-{'x' if not connected else int(round(percent or 0))}-{color.lstrip('#')}"
    if tag:
        stem += f"-{tag}"
    path = os.path.join(CACHE_DIR, stem + ".png")
    pixbuf.savev(path, "png", [], [])
    return path


def preview_sheet(path, values=(0, 25, 50, 63, 75, 94, 100)):
    """Debug helper: write a horizontal strip of rings at several levels."""
    size = 64
    gap = 8
    width = len(values) * (size + gap) + gap
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, size + gap * 2)
    cr = cairo.Context(surface)
    cr.set_source_rgb(0.13, 0.13, 0.14)
    cr.paint()
    tmp = os.path.join(CACHE_DIR, ".preview-tmp.png")
    ensure_cache_dir()
    for index, value in enumerate(values):
        pixbuf = render(size, value, level_color(value))
        pixbuf.savev(tmp, "png", [], [])
        ring = cairo.ImageSurface.create_from_png(tmp)
        x = gap + index * (size + gap)
        cr.set_source_surface(ring, x, gap)
        cr.paint()
    try:
        os.unlink(tmp)
    except OSError:
        pass
    surface.write_to_png(path)
    return path


if __name__ == "__main__":
    out = "/tmp/ocg-icons.png"
    preview_sheet(out)
    print(f"wrote {out} at {time.strftime('%H:%M:%S')}")
