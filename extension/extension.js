import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Clutter from 'gi://Clutter';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const BUS_NAME = 'org.opencode_go.Widget';
const OBJECT_PATH = '/org/opencode_go/Widget';

const INTERFACE_XML = `
<node>
  <interface name="org.opencode_go.Widget">
    <method name="GetUsage"><arg type="s" direction="out"/></method>
    <method name="Refresh"/>
    <method name="OpenSettings"/>
    <method name="ShowCard"/>
    <method name="SetCardVisible"><arg type="b" direction="in"/></method>
    <method name="Quit"/>
    <signal name="UsageChanged"><arg type="s"/></signal>
  </interface>
</node>`;

const Proxy = Gio.DBusProxy.makeProxyWrapper(INTERFACE_XML);

const COLUMN_GAP = 14;

/* A row is [ring][name][percent][countdown]. Each text cell is a fixed-width
 * St.BoxLayout with a fixed start: left-aligned cells just pack the label,
 * right-aligned ones use a leading x_expand spacer. Nothing depends on
 * St.Label's own alignment behaviour, which fights the parent layout. */
const UsageRow = GObject.registerClass(
class UsageRow extends St.BoxLayout {
    _init() {
        super._init({
            style_class: 'ocg-row',
            x_expand: true,
        });

        this.ringIcon = new St.Icon({
            style_class: 'popup-menu-icon',
            y_align: Clutter.ActorAlign.CENTER,
        });
        this.nameLabel = new St.Label({style_class: 'ocg-row-name', y_align: Clutter.ActorAlign.CENTER});
        this.percentLabel = new St.Label({style_class: 'ocg-row-value', y_align: Clutter.ActorAlign.CENTER});
        this.clockLabel = new St.Label({style_class: 'ocg-row-clock', y_align: Clutter.ActorAlign.CENTER});

        this.nameCell = this._cell(this.nameLabel, Clutter.ActorAlign.START);
        this.percentCell = this._cell(this.percentLabel, Clutter.ActorAlign.END);
        this.clockCell = this._cell(this.clockLabel, Clutter.ActorAlign.START);

        this.add_child(this.ringIcon);
        this.add_child(this._spacer(COLUMN_GAP));
        this.add_child(this.nameCell);
        this.add_child(this._spacer(COLUMN_GAP));
        this.add_child(this.percentCell);
        this.add_child(this._spacer(COLUMN_GAP));
        this.add_child(this.clockCell);
    }

    _cell(child, align) {
        const cell = new St.BoxLayout();
        if (align === Clutter.ActorAlign.END)
            cell.add_child(new St.Widget({x_expand: true}));
        cell.add_child(child);
        return cell;
    }

    _spacer(width) {
        const spacer = new St.Widget();
        spacer.set_width(width);
        spacer.set_height(1);
        return spacer;
    }

    update(meter) {
        if (meter.icon)
            this.ringIcon.gicon = Gio.icon_new_for_string(meter.icon);
        this.nameLabel.text = meter.label ?? '';
        this.percentLabel.text = `${Math.round(meter.percent)}%`;
        this.clockLabel.text = meter.clock ?? '';
    }
});

/* Toggling must not dismiss the popup: hide/show the card, then keep looking
 * at the switch the way the quick-settings tiles do. */
const CardToggleItem = GObject.registerClass(
class CardToggleItem extends PopupMenu.PopupSwitchMenuItem {
    activate() {
        if (this._switch.mapped)
            this.toggle();
    }
});

const UsageButton = GObject.registerClass(
class UsageButton extends PanelMenu.Button {
    _init() {
        super._init(0.5, 'OpenCode Go', false);
        this._payload = null;
        this._rows = [];
        this._meterKeys = '';
        this._busy = false;
        this._busyTimeout = 0;
        this._spinId = 0;
        this._updatingToggle = false;

        // PanelMenu.Button only lays out a single child, so the ring and the
        // weekly text share one box.
        const panelBox = new St.BoxLayout({style_class: 'ocg-panel-box'});

        this._icon = new St.Icon({
            style_class: 'system-status-icon ocg-panel-icon',
            gicon: Gio.icon_new_for_string('dialog-information-symbolic'),
        });

        // Weekly usage, spelled out next to the ring.
        this._weeklyLabel = new St.Label({
            style_class: 'ocg-panel-label',
            y_align: Clutter.ActorAlign.CENTER,
        });

        panelBox.add_child(this._icon);
        panelBox.add_child(this._weeklyLabel);
        this.add_child(panelBox);

        this._proxy = new Proxy(Gio.DBus.session, BUS_NAME, OBJECT_PATH,
            (proxy, error) => {
                if (error)
                    log(`opencode-go: proxy error: ${error.message}`);
                else
                    this._refreshFromService();
            });
        this._signalId = this._proxy.connectSignal('UsageChanged',
            (_proxy, _sender, [json]) => this._onPayload(json));

        this.menu.connect('open-state-changed', (_menu, open) => {
            if (!open)
                return;
            this._refreshFromService();
            if (GLib.getenv('OCG_DEVKIT'))
                GLib.timeout_add(GLib.PRIORITY_DEFAULT, 600, () => {
                    this._logLayout();
                    return GLib.SOURCE_REMOVE;
                });
        });

        this._buildMenu(null);
        this._applyState(null);

        // Test hook: open the popup automatically in a dev shell so its layout
        // can be captured. Removed for the installed build.
        if (GLib.getenv('OCG_DEVKIT')) {
            GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 5, () => {
                this.menu.open();
                return GLib.SOURCE_REMOVE;
            });
            GLib.timeout_add(GLib.PRIORITY_DEFAULT, 5600, () => {
                this._setBusy(true);
                return GLib.SOURCE_REMOVE;
            });
            GLib.timeout_add(GLib.PRIORITY_DEFAULT, 6100, () => {
                log(`ocg-usage: spin angle=${Math.round(this._refreshIcon.rotation_angle_z)} ` +
                    `button=${this._refreshButton.width}x${this._refreshButton.height} ` +
                    `toggle_w=${this._toggle.width}`);
                this._setBusy(false);
                return GLib.SOURCE_REMOVE;
            });
            GLib.timeout_add(GLib.PRIORITY_DEFAULT, 6300, () => {
                (async () => {
                    try {
                        const Shell = (await import('gi://Shell')).default;
                        const file = Gio.File.new_for_path('/tmp/ocg-ext-final.png');
                        const stream = file.replace(null, false, Gio.FileCreateFlags.NONE, null);
                        await new Shell.Screenshot().screenshot(false, stream);
                        stream.close(null);
                        log('ocg-usage: screenshot saved');
                    } catch (e) {
                        log(`ocg-usage: screenshot failed: ${e}`);
                    }
                })();
                return GLib.SOURCE_REMOVE;
            });
        }
    }

    // -- data ----------------------------------------------------------------
    _refreshFromService() {
        this._proxy.GetUsageRemote((result, error) => {
            if (!error) {
                this._onPayload(result[0]);
                return;
            }
            const fromFile = this._readStatusFile();
            if (fromFile)
                this._onPayload(fromFile);
            else
                this._onPayload(JSON.stringify({
                    meters: [],
                    error: 'Widget service is not running',
                }));
        });
    }

    _readStatusFile() {
        try {
            const path = GLib.build_filenamev([GLib.get_user_cache_dir(),
                'opencode-go-widget', 'status.json']);
            const [ok, bytes] = Gio.File.new_for_path(path).load_contents(null);
            return ok ? new TextDecoder().decode(bytes) : null;
        } catch (e) {
            return null;
        }
    }

    _onPayload(json) {
        let payload;
        try {
            payload = JSON.parse(json);
        } catch (e) {
            log(`opencode-go: bad payload: ${e}`);
            return;
        }
        this._payload = payload;
        if (payload.icon)
            this._icon.gicon = Gio.icon_new_for_string(payload.icon);

        const keys = (payload.meters ?? []).map(m => m.key).join(',');
        if (keys !== this._meterKeys)
            this._buildMenu(payload);
        this._applyState(payload);
    }

    // -- menu ----------------------------------------------------------------
    _buildMenu(payload) {
        this.menu.removeAll();
        this._rows = [];
        this._meterKeys = (payload?.meters ?? []).map(m => m.key).join(',');

        const header = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
        const headerBox = new St.BoxLayout({x_expand: true});

        this._refreshIcon = new St.Icon({
            icon_name: 'view-refresh-symbolic',
            style_class: 'popup-menu-icon',
        });
        this._refreshIcon.set_pivot_point(0.5, 0.5);

        this._refreshButton = new St.Button({
            style_class: 'icon-button flat ocg-refresh',
            child: this._refreshIcon,
            can_focus: true,
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._refreshButton.connect('clicked', () => {
            this._setBusy(true);
            this._proxy.RefreshRemote();
        });

        headerBox.add_child(new St.Label({
            text: 'OpenCode Go',
            style_class: 'ocg-title',
            y_align: Clutter.ActorAlign.CENTER,
        }));
        headerBox.add_child(new St.Widget({x_expand: true}));
        headerBox.add_child(this._refreshButton);
        header.add_child(headerBox);
        this.menu.addMenuItem(header);

        this._errorItem = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
        this._errorLabel = new St.Label({style_class: 'ocg-error', x_expand: true});
        this._errorItem.add_child(this._errorLabel);
        this._errorItem.visible = false;
        this.menu.addMenuItem(this._errorItem);

        for (const meter of payload?.meters ?? []) {
            const item = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
            const row = new UsageRow();
            row.update(meter);
            item.add_child(row);
            this.menu.addMenuItem(item);
            this._rows.push(row);
        }

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        this._toggle = new CardToggleItem('Show desktop widget', false);
        // Mirror the quick-settings row: icon, label, switch.
        this._addIcon(this._toggle, 'video-display-symbolic');
        this._toggle.connect('toggled', (_item, state) => {
            if (this._updatingToggle)
                return;
            this._proxy.SetCardVisibleRemote(state);
        });
        this.menu.addMenuItem(this._toggle);

        this.menu.addMenuItem(this._action('Settings', 'preferences-system-symbolic',
            () => this._proxy.OpenSettingsRemote()));

        this.menu.addMenuItem(this._action('Quit', 'system-shutdown-symbolic',
            () => this._proxy.QuitRemote()));
    }

    /* PopupMenuItem has no icon slot; insert one after the (hidden) ornament,
     * the way the shell's own rows do. */
    _addIcon(item, iconName) {
        item.insert_child_at_index(new St.Icon({
            icon_name: iconName,
            style_class: 'popup-menu-icon ocg-menu-icon',
            y_align: Clutter.ActorAlign.CENTER,
        }), 1);
    }

    _action(label, iconName, callback) {
        const item = new PopupMenu.PopupMenuItem(label);
        this._addIcon(item, iconName);
        item.connect('activate', callback);
        return item;
    }

    _applyState(payload) {
        const meters = payload?.meters ?? [];

        this._errorLabel.text = payload?.error ?? '';
        this._errorItem.visible = Boolean(payload?.error);

        const weekly = meters.find(meter => meter.key === 'weekly');
        this._weeklyLabel.text = weekly ? `${Math.round(weekly.percent)}% used` : '';
        this._weeklyLabel.visible = Boolean(weekly);

        for (let i = 0; i < this._rows.length && i < meters.length; i++)
            this._rows[i].update(meters[i]);

        this._updatingToggle = true;
        this._toggle.setToggleState(Boolean(payload?.card_visible));
        this._updatingToggle = false;

        this._setBusy(Boolean(payload?.fetching));

        // Widths can only be measured once the actors are in the stage tree.
        GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            this._alignColumns();
            return GLib.SOURCE_REMOVE;
        });
    }

    _setBusy(busy) {
        if (this._busy === busy)
            return;
        this._busy = busy;
        if (busy) {
            this._startSpin();
            this._clearBusyTimeout();
            this._busyTimeout = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 20, () => {
                this._busyTimeout = 0;
                this._setBusy(false);
                return GLib.SOURCE_REMOVE;
            });
        } else {
            this._stopSpin();
            this._clearBusyTimeout();
        }
    }

    /* A plain GLib timer, like the desktop card's: Clutter timelines are driven
     * by the frame clock, which may not tick for an idle popup. */
    _startSpin() {
        if (this._spinId)
            return;
        this._angle = 0;
        this._spinId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 33, () => {
            this._angle = (this._angle + 30) % 360;
            this._refreshIcon.rotation_angle_z = this._angle;
            return GLib.SOURCE_CONTINUE;
        });
    }

    _stopSpin() {
        if (this._spinId) {
            GLib.source_remove(this._spinId);
            this._spinId = 0;
        }
        this._angle = 0;
        this._refreshIcon.rotation_angle_z = 0;
    }

    _clearBusyTimeout() {
        if (this._busyTimeout) {
            GLib.source_remove(this._busyTimeout);
            this._busyTimeout = 0;
        }
    }

    _alignColumns() {
        if (!this._rows.length)
            return;

        const natural = label => label.get_preferred_width(-1)[1];
        const nameWidth = Math.max(...this._rows.map(r => natural(r.nameLabel)));
        const percentWidth = Math.max(...this._rows.map(r => natural(r.percentLabel)));
        const clockWidth = Math.max(...this._rows.map(r => natural(r.clockLabel)));

        // Every row gets the same fixed cell widths; separators are spacer
        // actors, so nothing here depends on padding tricks.
        for (const row of this._rows) {
            row.nameCell.set_width(nameWidth);
            row.percentCell.set_width(percentWidth);
            row.clockCell.set_width(clockWidth);
        }

        if (GLib.getenv('OCG_DEVKIT'))
            log(`ocg-usage: columns name=${nameWidth} pct=${percentWidth} ` +
                `clock=${clockWidth} rows=${this._rows.length}`);
    }

    _logLayout() {
        for (const row of this._rows) {
            const [nx] = row.nameLabel.get_transformed_position();
            const [px] = row.percentLabel.get_transformed_position();
            const [cx] = row.clockLabel.get_transformed_position();
            log(`ocg-usage: layout name=${Math.round(nx)}/${row.nameCell.width} ` +
                `pct=${Math.round(px)}/${row.percentCell.width} "${row.percentLabel.text}" ` +
                `clock=${Math.round(cx)}/${row.clockCell.width} "${row.clockLabel.text}" ` +
                `busy=${this._busy} toggle=${this._toggle.state}`);
        }
    }

    destroy() {
        this._clearBusyTimeout();
        this._stopSpin();
        if (this._signalId) {
            this._proxy.disconnectSignal(this._signalId);
            this._signalId = null;
        }
        super.destroy();
    }
});

export default class OpenCodeGoUsageExtension extends Extension {
    enable() {
        log('opencode-go: extension enabled');
        // Tells the Python service to stop showing its own tray icon.
        this._nameOwner = Gio.bus_own_name(
            Gio.BusType.SESSION, 'org.opencode_go.ShellUI',
            Gio.BusNameOwnerFlags.NONE, null, null, null);
        this._button = new UsageButton();
        Main.panel.addToStatusArea('ocg-usage', this._button);
        this._retryId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 60, () => {
            if (!this._button?._payload?.meters?.length)
                this._button?._refreshFromService();
            return GLib.SOURCE_CONTINUE;
        });
    }

    disable() {
        if (this._retryId) {
            GLib.source_remove(this._retryId);
            this._retryId = null;
        }
        this._button?.destroy();
        this._button = null;
        if (this._nameOwner) {
            Gio.bus_unown_name(this._nameOwner);
            this._nameOwner = null;
        }
    }
}
