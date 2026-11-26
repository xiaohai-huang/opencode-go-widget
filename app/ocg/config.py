"""Configuration and API-key storage for the OpenCode Go widget."""

import json
import os
import tempfile

CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
    "opencode-go-widget",
)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
TOKEN_PATH = os.path.join(CONFIG_DIR, "token")

DEFAULTS = {
    "poll_minutes": 5,
    "tint_icon": True,
    "show_card": True,
    "tray_indicator": True,
    "always_on_top": True,
    "collapsed": False,
    "card_x": None,
    "card_y": None,
    "plan_label": "",
    "console_url": "https://opencode.ai/auth",
}


def ensure_dir():
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)


class Config:
    def __init__(self, data=None):
        self.data = dict(DEFAULTS)
        if data:
            self.data.update({k: v for k, v in data.items() if k in DEFAULTS})
            self.data.update({k: v for k, v in data.items() if k not in DEFAULTS})

    # -- persistence ---------------------------------------------------------
    @classmethod
    def load(cls):
        ensure_dir()
        data = {}
        try:
            with open(CONFIG_PATH, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as err:
            print(f"opencode-go-widget: ignoring bad config: {err}")
        return cls(data)

    def save(self):
        ensure_dir()
        # Write atomically, then tighten permissions: the file can hold a token.
        fd, tmp = tempfile.mkstemp(dir=CONFIG_DIR, prefix=".config-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=2, sort_keys=True)
                fh.write("\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, CONFIG_PATH)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # -- helpers -------------------------------------------------------------
    def get(self, key, default=None):
        return self.data.get(key, DEFAULTS.get(key, default))

    def set(self, key, value):
        self.data[key] = value

    @property
    def token(self):
        """API key: explicit config value, then token file, then environment."""
        token = (self.data.get("token") or "").strip()
        if token:
            return token
        try:
            with open(TOKEN_PATH, encoding="utf-8") as fh:
                token = fh.read().strip()
            if token:
                return token
        except OSError:
            pass
        return (os.environ.get("OPENCODE_GO_API_KEY") or "").strip()

    def set_token(self, token):
        token = (token or "").strip()
        self.data.pop("token", None)
        ensure_dir()
        if token:
            fd = os.open(TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(token + "\n")
            os.chmod(TOKEN_PATH, 0o600)
        else:
            try:
                os.unlink(TOKEN_PATH)
            except OSError:
                pass
        self.save()
