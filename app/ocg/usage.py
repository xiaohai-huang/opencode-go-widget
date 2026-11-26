"""OpenCode Go usage API client and a deliberately forgiving response parser.

The public endpoint is documented only loosely ("returns meters for rolling,
weekly and monthly limits"), so instead of hard-coding a schema we scan the
JSON for meter-shaped objects and classify them by name. That way a field
rename on the server side degrades to "unknown meter" rather than a crash.
"""

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import __version__

# Overridable so the widget can be pointed at a local stub while testing.
API_URL = os.environ.get(
    "OPENCODE_GO_USAGE_URL", "https://opencode.ai/inference/go/v1/usage"
)
USER_AGENT = f"opencode-go-widget/{__version__} (GNOME desktop widget)"

CANONICAL = ("rolling", "weekly", "monthly")
DEFAULT_LABELS = {"rolling": "5-hour", "weekly": "Weekly", "monthly": "Monthly"}


class UsageError(Exception):
    kind = "error"


class AuthError(UsageError):
    kind = "auth"


class NetworkError(UsageError):
    kind = "network"


class ServerError(UsageError):
    kind = "http"

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class ParseError(UsageError):
    kind = "parse"


@dataclass
class Meter:
    key: str
    label: str
    percent: float
    reset_at: float | None = None      # epoch seconds
    resets_in_s: float | None = None   # server-provided countdown, if any
    used: float | None = None
    limit: float | None = None
    remaining: float | None = None
    source_name: str = ""

    def seconds_left(self, now=None):
        now = time.time() if now is None else now
        if self.reset_at is not None:
            return max(0.0, self.reset_at - now)
        if self.resets_in_s is not None:
            return max(0.0, self.resets_in_s)
        return None


@dataclass
class Snapshot:
    meters: list = field(default_factory=list)
    plan: str | None = None
    fetched_at: float = field(default_factory=time.time)
    raw: dict | None = None

    def by_key(self, key):
        for meter in self.meters:
            if meter.key == key:
                return meter
        return None

    @property
    def worst(self):
        return max((m.percent for m in self.meters), default=0.0)


# --------------------------------------------------------------------------
# field classification
# --------------------------------------------------------------------------
_PCT_KEYS = (
    "percent", "percentage", "used_percent", "usage_percent", "percent_used",
    "used_pct", "used_percentage",
)
_RATIO_KEYS = ("ratio", "used_ratio", "usage_ratio", "fraction", "utilization", "utilisation")
_USED_KEYS = (
    "used", "usage", "consumed", "spent", "used_amount", "used_tokens",
    "tokens_used", "used_requests", "requests_used", "used_units", "used_usd",
    "amount_used", "cost_used", "spend",
)
_LIMIT_KEYS = (
    "limit", "max", "total", "allowance", "quota", "cap", "included",
    "limit_amount", "max_amount", "total_units", "limit_usd", "budget",
    "included_units", "entitlement",
)
_REMAIN_KEYS = (
    "remaining", "left", "available", "balance", "remaining_amount",
    "remaining_units", "remaining_percent", "remaining_pct",
)
_RESET_AT_KEYS = (
    "resets_at", "reset_at", "next_reset", "next_reset_at",
    "window_resets_at", "period_end", "period_ends_at", "ends_at", "expiry",
    "expires_at", "expiry_at", "renewal", "renews_at", "renewal_at",
    "reset_time", "reset", "resets", "end",
)
_RESET_IN_KEYS = (
    "reset_in", "resets_in", "reset_in_seconds", "resets_in_seconds",
    "seconds_until_reset", "ttl", "remaining_seconds", "time_left",
    "seconds_left", "reset_after",
)
_NAME_KEYS = ("name", "label", "window", "period", "type", "id", "scope", "bucket", "interval")
_PLAN_KEYS = ("plan", "plan_name", "tier", "subscription", "subscription_plan", "product", "sku")


_CAMEL = re.compile(r"(?<=[a-z0-9])([A-Z])")


def _norm(key):
    """Normalise casing/separators: resetsAt, resets-at, RESETS_AT → resets_at."""
    text = _CAMEL.sub(r"_\1", str(key))
    return text.lower().replace("-", "_").replace(" ", "_")


def _pick(mapping, keys):
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _number(value):
    """Best-effort numeric coercion; returns None when it isn't a number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "").rstrip("%")
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _parse_time(value):
    """Return (epoch_seconds, seconds_from_now) for the many reset shapes."""
    if value is None or isinstance(value, bool):
        return None, None
    if isinstance(value, (int, float)):
        return _epoch_from_number(float(value)), None
    if not isinstance(value, str):
        return None, None
    text = value.strip()
    if not text:
        return None, None
    number = _number(text)
    if number is not None and not any(c in text for c in "-:TtZz+ "):
        return _epoch_from_number(number), None
    iso = text.replace("Z", "+00:00").replace("z", "+00:00")
    for candidate in (iso, iso.replace(" ", "T", 1)):
        try:
            dt = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp(), None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc).timestamp(), None
        except ValueError:
            continue
    return None, None


def _epoch_from_number(value):
    if value > 1e11:      # milliseconds
        return value / 1000.0
    if value > 1e9:       # seconds since epoch
        return value
    if value > 1e6:       # microseconds
        return value / 1e6
    return None           # probably a duration, not an instant


def _classify(*texts):
    blob = " ".join(t for t in texts if t).lower()
    if not blob:
        return None
    if any(tok in blob for tok in ("rolling", "5h", "5_h", "5hour", "5-hour", "five_hour", "five hour", "short", "hourly")):
        return "rolling"
    if any(tok in blob for tok in ("week", "7d", "7_d", "seven")):
        return "weekly"
    if any(tok in blob for tok in ("month", "30d", "30_d", "billing", "renew", "subscription")):
        return "monthly"
    return None


def _looks_like_meter(mapping):
    keys = {_norm(k) for k in mapping}
    has_pct = bool(keys & set(_PCT_KEYS + _RATIO_KEYS + _REMAIN_KEYS))
    has_pair = bool(keys & set(_USED_KEYS)) and bool(keys & set(_LIMIT_KEYS))
    return has_pct or has_pair


def _walk(node, path=()):
    yield path, node
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk(value, path + (str(key),))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, path + (str(index),))


def _meter_from(mapping, path):
    keys = {_norm(k): v for k, v in mapping.items()}
    name = " ".join(
        str(v) for k, v in mapping.items()
        if _norm(k) in _NAME_KEYS and isinstance(v, str)
    )
    path_text = " ".join(p for p in path if not p.isdigit())
    key = _classify(name) or _classify(path_text)

    percent = None
    for cand in _PCT_KEYS:
        if cand in keys:
            percent = _number(keys[cand])
            if percent is not None:
                break
    if percent is None:
        for cand in _RATIO_KEYS:
            if cand in keys:
                ratio = _number(keys[cand])
                if ratio is not None:
                    percent = ratio * 100.0 if ratio <= 1.001 else ratio
                break

    used = limit = remaining = None
    remaining_key = None
    for cand in _USED_KEYS:
        if cand in keys:
            used = _number(keys[cand])
            break
    for cand in _LIMIT_KEYS:
        if cand in keys:
            limit = _number(keys[cand])
            break
    for cand in _REMAIN_KEYS:
        if cand in keys:
            remaining = _number(keys[cand])
            remaining_key = cand
            break

    if percent is None and used is not None and limit:
        percent = used / limit * 100.0
    if percent is None and remaining is not None:
        if remaining_key and ("percent" in remaining_key or "pct" in remaining_key):
            percent = 100.0 - remaining
        elif limit:
            percent = (limit - remaining) / limit * 100.0

    reset_at = resets_in = None
    for cand in _RESET_AT_KEYS:
        if cand in keys:
            reset_at, _ = _parse_time(keys[cand])
            if reset_at is not None:
                break
    for cand in _RESET_IN_KEYS:
        if cand in keys:
            value = _number(keys[cand])
            if value is not None:
                resets_in = value if value < 1e6 else None
            break

    if percent is None and (reset_at is None and resets_in is None):
        return None
    if percent is None:
        return None

    return Meter(
        key=key or "unknown",
        label="",
        percent=max(0.0, min(100.0, percent)),
        reset_at=reset_at,
        resets_in_s=resets_in,
        used=used,
        limit=limit,
        remaining=remaining,
        source_name=name or path_text,
    )


def parse(payload):
    """Turn an arbitrary usage payload into a Snapshot. Raises ParseError."""
    if isinstance(payload, (str, bytes)):
        try:
            payload = json.loads(payload)
        except ValueError as err:
            raise ParseError(f"response is not JSON: {err}") from err
    if not isinstance(payload, (dict, list)):
        raise ParseError("response is not a JSON object")

    meters, seen = [], set()
    for path, node in _walk(payload):
        if not isinstance(node, dict) or id(node) in seen:
            continue
        if not _looks_like_meter(node):
            continue
        meter = _meter_from(node, path)
        if meter is not None:
            seen.add(id(node))
            meters.append(meter)

    if not meters:
        top = list(payload)[:8] if isinstance(payload, dict) else "[list]"
        raise ParseError(f"no usage meters found (top-level keys: {top})")

    # Deduplicate on (key, rounded percent) keeping the richest record.
    deduped = {}
    for meter in meters:
        signature = (meter.key, round(meter.percent, 3))
        rich = deduped.get(signature)
        if rich is None or (meter.used is not None and rich.used is None):
            deduped[signature] = meter
    meters = list(deduped.values())

    unnamed = [m for m in meters if m.key == "unknown"]
    if unnamed and len(unnamed) == len(meters) and len(meters) <= len(CANONICAL):
        for meter, key in zip(unnamed, CANONICAL):
            meter.key = key
    elif unnamed and len(meters) == 3:
        for meter, key in zip(meters, CANONICAL):
            if meter.key == "unknown":
                meter.key = key

    order = {key: i for i, key in enumerate(CANONICAL)}
    meters.sort(key=lambda m: (order.get(m.key, 99), m.label))
    for meter in meters:
        meter.label = DEFAULT_LABELS.get(meter.key) or _title(meter.source_name) or "Usage"

    plan = _find_plan(payload)
    return Snapshot(meters=meters, plan=plan, raw=payload)


def _title(text):
    text = (text or "").replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else ""


def _find_plan(payload):
    for _, node in _walk(payload):
        if not isinstance(node, dict):
            continue
        for key, value in node.items():
            if _norm(key) in _PLAN_KEYS and isinstance(value, str):
                value = value.strip()
                if 0 < len(value) <= 40:
                    return value
    return None


# --------------------------------------------------------------------------
# transport
# --------------------------------------------------------------------------
def fetch(token, url=API_URL, timeout=15):
    if not token:
        raise AuthError("no API key configured")
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as err:
        if err.code in (401, 403):
            raise AuthError(f"API key rejected (HTTP {err.code})") from err
        if err.code == 429:
            raise ServerError("rate limited by the API (HTTP 429)", 429) from err
        raise ServerError(f"API returned HTTP {err.code}", err.code) from err
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        reason = getattr(err, "reason", err)
        raise NetworkError(f"cannot reach the API: {reason}") from err

    try:
        payload = json.loads(body.decode("utf-8", "replace"))
    except ValueError as err:
        raise ParseError(f"response is not JSON: {err}") from err
    snapshot = parse(payload)
    snapshot.fetched_at = time.time()
    return snapshot


def fetch_usage(token, **kwargs):
    return fetch(token, **kwargs)


# --------------------------------------------------------------------------
# formatting helpers (shared by card, tray and CLI)
# --------------------------------------------------------------------------
def fmt_duration(seconds):
    if seconds is None:
        return ""
    seconds = int(max(0, seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


def _is_monthly(meter):
    return meter.key == "monthly"


def countdown_text(meter, now=None):
    left = meter.seconds_left(now)
    if left is None:
        return ""
    return fmt_duration(left)


def absolute_text(meter):
    if meter.reset_at is None:
        return ""
    local = datetime.fromtimestamp(meter.reset_at).astimezone()
    if _is_monthly(meter):
        return f"renews {local.day} {local:%b}"
    return f"resets {local:%a %H:%M}"


def reset_line(meter, now=None):
    absolute = absolute_text(meter)
    left = countdown_text(meter, now)
    if absolute and left:
        return f"{absolute} · in {left}"
    if absolute:
        return absolute
    if left:
        return f"resets in {left}"
    return ""


def bar_text(percent, width=10):
    filled = int(round(max(0.0, min(100.0, percent)) / 100.0 * width))
    return "█" * filled + "░" * (width - filled)


def describe(snapshot):
    """Plain-text rendering used by --once and debugging."""
    lines = ["OpenCode Go" + (f" · {snapshot.plan}" if snapshot.plan else "")]
    for meter in snapshot.meters:
        lines.append(
            f"  {meter.label:<16} {meter.percent:5.1f}%  "
            f"{bar_text(meter.percent, 10)}  {reset_line(meter)}"
        )
    return "\n".join(lines)
