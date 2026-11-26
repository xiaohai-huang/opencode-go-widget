# OpenCode Go usage widget

A small Ubuntu desktop widget for [OpenCode Go](https://opencode.ai/docs/go/) usage:

- **Panel ring gauge** in the top bar, drawn by a small GNOME Shell extension. The ring
  fills with your *highest* of the three limits, so one glance answers "how close am I
  to a wall?". It stays monochrome below 70%, turns amber at 70%, red at 90%. Clicking
  it opens a dropdown with one row per limit, aligned in real columns, a refresh
  button that spins while a request is in flight, and a **Show desktop widget**
  switch that shows or hides the card.
- **Desktop card** showing the 5-hour rolling, weekly and monthly meters with
  percentages, bars, and reset countdowns. It floats above other windows by default
  (toggle in Settings), never takes keyboard focus, remembers where you drag it, and
  dims itself when data is stale.
- **Minimize / expand** — the header button (or the right-click menu, or a click
  anywhere on the minimized bar) collapses the card to its title bar and brings it
  back. The minimized state persists across restarts. There is deliberately no
  hover-to-expand: pointer crossing events never reach the toplevel (every part of the
  card is a child `GdkWindow`) and Wayland has no pointer query, so it proved
  unreliable — click is the contract.
- **Animated refresh** — the footer's refresh icon spins while a request is in
  flight, whether you clicked it or the poll timer fired.
- **Autostart switch** — Settings offers "Start automatically at login"; it
  creates or removes the XDG autostart entry, so the app runs at login only if
  you asked it to.
- **Tray menu fallback** with the same numbers, each row carrying its own ring icon,
  plus refresh, settings and quit. Only used when the extension is disabled.
- Optional `notify-send`-style notification once per window when a meter crosses 90%.

Data comes from `GET https://opencode.ai/inference/go/v1/usage` with your Go API key.

## Setup

1. Get an API key: <https://opencode.ai/auth> → subscribe to Go → copy the key.
2. Launch the widget:

   ```sh
   ~/.local/share/opencode-go-widget/bin/opencode-go-widget
   ```

3. Click the ring in the top bar → **Settings** (or right-click the desktop card →
   **Settings**) → paste the key → **Test connection** → **Save**.

The key is stored at `~/.config/opencode-go-widget/token` with mode `0600`.
Precedence is: `token` in `config.json`, then the token file, then the
`OPENCODE_GO_API_KEY` environment variable. Setting a key in the UI always writes
the token file and clears the config copy.

The widget autostarts with the session (`~/.config/autostart/opencode-go-widget.desktop`)
and is also in the application grid as "OpenCode Go Usage".

## Commands

```sh
bin/opencode-go-widget              # run the widget (single instance)
bin/opencode-go-widget --once       # print usage to stdout, exit
bin/opencode-go-widget --demo       # sample numbers, useful before adding a key
bin/opencode-go-widget --no-card    # panel icon only
bin/opencode-go-widget --screenshot out.png   # capture the card and exit
```

Test the client without a real key by pointing it at a stub:

```sh
OPENCODE_GO_USAGE_URL=http://127.0.0.1:8731/usage OPENCODE_GO_API_KEY=test bin/opencode-go-widget --once
```

## Files

| Path | Purpose |
| --- | --- |
| `~/.local/share/opencode-go-widget/main.py` | entry point / CLI |
| `ocg/usage.py` | API client + forgiving response parser |
| `ocg/card.py` | desktop card window |
| `ocg/app.py` | polling, indicator, settings dialog |
| `ocg/service.py` | D-Bus service feeding the Shell extension |
| `ocg/icon.py` | Cairo ring-gauge rendering |
| `style.css` | card styling |
| `~/.config/opencode-go-widget/config.json` | poll interval, toggles, card position |
| `~/.cache/opencode-go-widget/` | rendered icon PNGs, `status.json`, instance lock |
| `~/.local/share/gnome-shell/extensions/ocg-usage@local/` | GNOME Shell extension (panel ring + popup) |

## Implementation notes

- **The popup is a GNOME Shell extension, not the tray menu.** An Ayatana
  appindicator exports a DBusMenu, and the shell renders only those labels — no custom
  widgets — so columns there can only be faked with space characters, and the shell's
  space advance does not match Pango's. The extension lays out `St` actors with widths
  measured by the renderer itself, which is exact. The Python process stays the single
  owner of the API key, polling and parsing, and exposes data on
  `org.opencode_go.Widget`; the extension is pure UI and falls back to
  `~/.cache/opencode-go-widget/status.json` when the app is not running. While the
  extension is active it owns `org.opencode_go.ShellUI`, and the Python app retires
  its appindicator.
- **Editing the extension needs a re-login.** GNOME 51 declares
  `org.gnome.Shell.Extensions.ReloadExtension` but does not implement it, and
  disabling/re-enabling an extension reuses the cached JS module, so the running shell
  keeps executing the old code until it restarts.
- **Why is the tray menu text-only?** Ayatana appindicators export a DBusMenu; GNOME
  renders the exported labels, not arbitrary GTK widgets. The card carries the visual
  design; the menu carries the numbers.
- **Why X11?** Wayland forbids clients placing windows, so the launcher sets
  `GDK_BACKEND=x11` when Xwayland is available. That is what lets the card remember
  its position, be dragged, and float above (or drop below) other windows.
- **The parser is deliberately loose**: field names are discovered by shape
  (`percent`/`utilization`/`used+limit`/`remaining+limit`, ISO or epoch resets), so a
  server-side field rename shows up as `unknown` instead of a crash. Settings → Test
  shows the raw JSON for pinning down any new fields.

## Uninstall

```sh
gnome-extensions disable ocg-usage@local
rm -rf ~/.local/share/gnome-shell/extensions/ocg-usage@local
rm -rf ~/.local/share/opencode-go-widget ~/.config/opencode-go-widget ~/.cache/opencode-go-widget
rm -f ~/.config/autostart/opencode-go-widget.desktop ~/.local/share/applications/opencode-go-widget.desktop
```
