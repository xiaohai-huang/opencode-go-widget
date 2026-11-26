# OpenCode Go usage widget — install

A usage gauge for [OpenCode Go](https://opencode.ai/docs/go/) on the Ubuntu
desktop: a ring in the top bar with a weekly `% used` label, a dropdown with the
three limits, and a floating desktop card.

## Requirements

- Ubuntu with **GNOME Shell 50 or 51** (the extension is tested there; other
  versions may work but are untested).
- `python3-gi`, `python3-cairo`, `gir1.2-gtk-3.0`,
  `gir1.2-ayatanaappindicator3-0.1`. Notifications additionally use
  `gir1.2-notify-0.7`; everything works without it.

On Debian/Ubuntu:

```sh
sudo apt install python3-gi python3-cairo gir1.2-gtk-3.0 \
    gir1.2-ayatanaappindicator3-0.1 gir1.2-notify-0.7
```

## Install

```sh
tar xf opencode-go-widget-1.0.0.tar.gz
cd opencode-go-widget-1.0.0
./install.sh
```

Then **log out and log back in**. GNOME Shell only loads newly installed
extensions in a fresh session.

After logging in, click the ring in the top bar → **Settings…**, paste your own
OpenCode Go API key from <https://opencode.ai/auth>, press **Test connection**
and **Save**.

## Notes for friends

- **Bring your own key.** No key ships in this package; each person adds their
  own. It is stored in `~/.config/opencode-go-widget/token` with mode `0600`.
  Never paste that file, `config.json`, or a screenshot of the raw API response
  into a chat — the raw response view can contain request metadata.
- The app and the extension are two halves of the same thing. The panel item and
  dropdown are the extension; the card, polling, notifications and the API key
  live in the Python process. Install both.
- If the extension is disabled or missing, the app falls back to a classic tray
  icon with a plain-text menu.
- Autostart is on after installing. Turn it off in Settings → **Start
  automatically at login**; that simply removes the XDG autostart entry.
- Uninstall with `./uninstall.sh` (add `--keep-key` to keep the token).

## What gets installed

| Path | Purpose |
| --- | --- |
| `~/.local/share/opencode-go-widget/` | the app (GTK card, polling, D-Bus service) |
| `~/.local/share/gnome-shell/extensions/ocg-usage@local/` | panel ring + dropdown |
| `~/.config/autostart/opencode-go-widget.desktop` | starts the app at login |
| `~/.local/share/applications/opencode-go-widget.desktop` | app-grid entry |
| `~/.config/opencode-go-widget/` | your settings and API key (created on first save) |
| `~/.cache/opencode-go-widget/` | rendered icons and a status snapshot |
