# OpenCode Go Widget

A usage gauge for [OpenCode Go](https://opencode.ai/docs/go/) on the Ubuntu
desktop. It has two halves that work together: a **GNOME Shell extension** for
the top bar, and a **GTK app** that owns the API key, polling and the desktop
card.

![Desktop card](docs/card.png)

![Panel dropdown](docs/extension-popup.png)

## Features

- **Panel ring gauge** filled with your highest limit. It stays monochrome below
  70%, turns amber at 70%, red at 90%, and shows the weekly limit as
  `N% used` next to it.
- **Dropdown** with one row per limit in real columns, a refresh button that
  spins while a request is in flight, and a **Show desktop widget** switch.
- **Desktop card** with bars, reset countdowns, and a spinning refresh control.
  Minimize/expand without the title bar moving, drag it anywhere, and it floats
  above other windows without ever taking keyboard focus.
- **Settings** for the poll interval, icon tint, card stacking, autostart and
  the API key. The app version is shown at the bottom.
- **Tray-icon fallback** when the extension is not available.

The API key never leaves the machine: requests go straight to
`https://opencode.ai/inference/go/v1/usage`.

## Requirements

Ubuntu with GNOME Shell **50 or 51**. The .deb installs the Python/GTK
dependencies (`python3-gi`, `python3-gi-cairo`, `python3-cairo`, GTK 3 and
Ayatana AppIndicator).

## Install

Download `opencode-go-widget_1.0.4_all.deb` from the releases page and run:

```sh
sudo apt install ./opencode-go-widget_1.0.4_all.deb
```

Then **log out and back in** once. GNOME Shell only scans extension
directories at session start, so this cannot be skipped. Launch **OpenCode Go
Widget**; at startup it enables the bundled extension automatically. Until
then it uses the tray-icon fallback.

Open Settings, paste your key, press **Test connection**, then **Save**.
Autostart is off by default; turn on **Start automatically at login** in
Settings.

## Uninstall

```sh
sudo apt remove opencode-go-widget
```

Your settings and API key in `~/.config/opencode-go-widget` are kept. Turn off
autostart in Settings before removing, or delete
`~/.config/autostart/opencode-go-widget.desktop`. Log out and back in to drop
the panel item.

## Layout

| Path | Purpose |
| --- | --- |
| `app/` | GTK app: card, polling, D-Bus service, settings |
| `extension/` | GNOME Shell extension: panel ring + weekly label + dropdown |
| `debian/` | Debian packaging (`opencode-go-widget_1.0.4_all.deb`) |
| `packaging/build-deb.sh` | builds the .deb into `dist/` |
| `assets/` | application icon |
| `docs/` | screenshots |

## How it works

- The Python process is the single owner of the API key, polling, response
  parsing and the Cairo-rendered ring icons. The extension is pure UI and talks
  to it over the session bus (`org.opencode_go.Widget`: `GetUsage`, `Refresh`,
  `OpenSettings`, `SetCardVisible`, signal `UsageChanged`). Payloads are also
  mirrored to `~/.cache/opencode-go-widget/status.json` so the extension can
  render the last known numbers if the app is not running.
- The extension claims `org.opencode_go.ShellUI` while it is active; the app
  watches for that name and retires its own appindicator so there is only ever
  one panel item.
- The popup uses `St` actors with widths measured by the renderer itself
  (`get_preferred_width`), which is why its columns line up: an appindicator
  DBusMenu can only carry text, and space-padding there drifts between rows.
- Editing extension JS **requires a re-login**: GNOME 51 declares
  `org.gnome.Shell.Extensions.ReloadExtension` but does not implement it, and
  disable/enable reuses the cached ES module.

## Development

Run from a checkout:

```sh
app/bin/opencode-go-widget --demo          # sample numbers
app/bin/opencode-go-widget --once          # print usage, exit
```

Build the Ubuntu/Debian package:

```sh
sudo apt install debhelper dpkg-dev fakeroot
./packaging/build-deb.sh                   # -> dist/opencode-go-widget_1.0.4_all.deb
```

## License

No license has been chosen yet. Add one before publishing publicly. The
packaged `debian/copyright` is a placeholder that says so; replace it when you
pick a license.
