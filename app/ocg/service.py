"""D-Bus service exposing usage data to the GNOME Shell extension.

The Python process stays the single owner of the API key, polling and parsing;
the extension is pure UI. Payloads carry pre-rendered icon paths so the panel
and popup look identical to the desktop card.
"""

import json
import time

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import usage  # noqa: E402

BUS_NAME = "org.opencode_go.Widget"
OBJECT_PATH = "/org/opencode_go/Widget"
INTERFACE = "org.opencode_go.Widget"

XML = """
<node>
  <interface name="org.opencode_go.Widget">
    <method name="GetUsage">
      <arg type="s" direction="out"/>
    </method>
    <method name="Refresh"/>
    <method name="OpenSettings"/>
    <method name="ShowCard"/>
    <method name="SetCardVisible">
      <arg type="b" direction="in"/>
    </method>
    <method name="Quit"/>
    <signal name="UsageChanged">
      <arg type="s"/>
    </signal>
  </interface>
</node>
"""


class WidgetService:
    def __init__(self, on_refresh, on_settings, on_card, on_card_visible=None,
                 on_quit=None):
        self._on_refresh = on_refresh
        self._on_settings = on_settings
        self._on_card = on_card
        self._on_card_visible = on_card_visible
        self._on_quit = on_quit
        self._payload = {"meters": [], "error": "starting"}
        self._connection = None
        self._registration = None

    # -- payload -------------------------------------------------------------
    def set_payload(self, payload):
        self._payload = payload
        if self._connection is not None:
            self._connection.emit_signal(
                None, OBJECT_PATH, INTERFACE, "UsageChanged",
                GLib.Variant("(s)", (json.dumps(payload),)),
            )

    @staticmethod
    def build_payload(snapshot, error, icon_path, meter_icons, label_for_error,
                      card_visible=True, fetching=False):
        now = time.time()
        payload = {
            "fetched_at": snapshot.fetched_at if snapshot else None,
            "error": label_for_error(error) if error else None,
            "plan": snapshot.plan if snapshot else None,
            "icon": icon_path,
            "card_visible": bool(card_visible),
            "fetching": bool(fetching),
            "meters": [],
        }
        if snapshot is not None:
            for meter in snapshot.meters:
                payload["meters"].append({
                    "key": meter.key,
                    "label": meter.label,
                    "percent": round(meter.percent, 1),
                    "clock": usage.countdown_text(meter, now),
                    "when": usage.absolute_text(meter),
                    "reset_at": meter.reset_at,
                    "resets_in_s": meter.resets_in_s,
                    "icon": meter_icons.get(meter.key),
                })
        return payload

    @staticmethod
    def snapshot_from_payload(payload):
        """Rebuild a Snapshot from a payload mirrored to status.json.

        Only the fields the payload carries are restored; used/limit are not
        needed by the panel or the popup.
        """
        if not isinstance(payload, dict):
            return None
        meters = []
        for item in payload.get("meters") or []:
            if not isinstance(item, dict):
                continue
            try:
                percent = float(item.get("percent") or 0.0)
                reset_at = item.get("reset_at")
                resets_in_s = item.get("resets_in_s")
                meters.append(usage.Meter(
                    key=item.get("key") or "unknown",
                    label=item.get("label") or "",
                    percent=percent,
                    reset_at=float(reset_at) if reset_at is not None else None,
                    resets_in_s=float(resets_in_s) if resets_in_s is not None else None,
                ))
            except (TypeError, ValueError):
                continue
        if not meters:
            return None
        try:
            fetched_at = float(payload.get("fetched_at") or 0.0)
        except (TypeError, ValueError):
            return None
        return usage.Snapshot(
            meters=meters,
            plan=payload.get("plan"),
            fetched_at=fetched_at,
        )

    # -- D-Bus ---------------------------------------------------------------
    def start(self):
        try:
            self._connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception as err:
            print(f"opencode-go-widget: no session bus: {err}")
            return False

        node = Gio.DBusNodeInfo.new_for_xml(XML)
        iface = node.interfaces[0]
        self._registration = self._connection.register_object(
            OBJECT_PATH, iface, self._on_call, None, None
        )
        Gio.bus_own_name_on_connection(
            self._connection, BUS_NAME, Gio.BusNameOwnerFlags.NONE, None, None
        )
        return True

    def _on_call(self, connection, sender, path, interface, method, parameters, invocation):
        if method == "GetUsage":
            invocation.return_value(GLib.Variant("(s)", (json.dumps(self._payload),)))
        elif method == "Refresh":
            invocation.return_value(None)
            GLib.idle_add(lambda: (self._on_refresh(), False)[1])
        elif method == "OpenSettings":
            invocation.return_value(None)
            GLib.idle_add(lambda: (self._on_settings(), False)[1])
        elif method == "ShowCard":
            invocation.return_value(None)
            GLib.idle_add(lambda: (self._on_card(), False)[1])
        elif method == "SetCardVisible":
            invocation.return_value(None)
            visible = bool(parameters.unpack()[0])
            if self._on_card_visible is not None:
                GLib.idle_add(lambda: (self._on_card_visible(visible), False)[1])
        elif method == "Quit":
            invocation.return_value(None)
            if self._on_quit is not None:
                GLib.idle_add(lambda: (self._on_quit(), False)[1])
        else:
            invocation.return_dbus_error(
                "org.freedesktop.DBus.Error.UnknownMethod", f"no {method}")
