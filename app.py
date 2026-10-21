#!/usr/bin/env python3
"""
NASCAR Fan Rewards check-in REMINDER service (v2).

Sends push notifications (ntfy) when a race is actually live so you can press
Check In yourself. It never logs into nascar.com and never clicks anything.

Modes (ALERT_MODE):
  schedule  alert at scheduled start + FIRST_ALERT_MIN (v1 behaviour)
  live      alert only when NASCAR's live feed shows a race under way
  auto      live detection first; scheduled alert only as a safety net
            (default). If the live feed is unreachable, behaves like "schedule".

Also serves, on HTTP_PORT: /status for a Stream Deck key, /icon.png, and a dashboard at / (status, this week's
races and daily games with 'points earned' buttons, the last notifications, and a settings editor that rewrites
the mounted .env and restarts the app).

Usage:
  python app.py            run the service
  python app.py --list     upcoming races it will alert for
  python app.py --probe    check the live feed and show how it is interpreted
  python app.py --polling  show whether quiet hours currently block polling
  python app.py --icon-test [secs]  cycle /status through green/yellow/red/hot/offline (default 20 s each)
  python app.py --preview  send the weekend preview now (--preview-print just shows it)
  python app.py --daily [day]  send the daily dashboard reminder now (--daily-print just shows it)
  python app.py --test     send a test notification
"""
import contextlib
import hmac
import io
import json
import logging
import os
import random
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


# Series names. The second series is now the O'Reilly Auto Parts Series (it used to be called Xfinity), so the old
# name is still accepted anywhere a series is typed in (.env, races.json, saved state) and means O'Reilly.
SERIES_LABELS = {"cup": "Cup", "oreilly": "Oreilly", "truck": "Truck"}          # internal, class/URL safe
SERIES_DISPLAY = {"Cup": "Cup", "Oreilly": "O'Reilly", "Truck": "Truck"}        # what people read
SERIES_ALIASES = {"cup": "cup", "truck": "truck", "trucks": "truck", "xfinity": "oreilly", "oreilly": "oreilly",
                  "o'reilly": "oreilly", "o\u2019reilly": "oreilly", "o'reilly auto parts": "oreilly"}


def canon_series_key(x):
    """'cup' / 'oreilly' / 'truck' for any accepted spelling, else None."""
    return SERIES_ALIASES.get(str(x).strip().lower())


def canon_label(x):
    k = canon_series_key(x)
    return SERIES_LABELS[k] if k else str(x)


def series_name(label):
    return SERIES_DISPLAY.get(label, label)


def get_tz(name, default):
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(default)


# ------------------------------------------------- settings schema + .env file ----
# Every setting the dashboard can edit. "default" must match the default used further down.
# type: text | secret | int | float | bool | time | select | multi | tz
_DAYS_OPT = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _s(key, label, group, typ, default, help_, **kw):
    d = {"key": key, "label": label, "group": group, "type": typ, "default": default, "help": help_}
    d.update(kw)
    return d


SETTINGS = [
    _s("TZ", "Time zone", "Essentials", "tz", "UTC",
       "Your time zone (IANA name, e.g. America/New_York). Used for every time shown and for the reminder times.",
       required=True),
    _s("NTFY_TOPIC", "ntfy topic", "Essentials", "secret", "",
       "Long random name your phone subscribes to. Anyone who knows it can read your alerts.", required=True),
    _s("ALERT_MODE", "Alert mode", "Alerts and timing", "select", "auto",
       "auto = live detection first, scheduled time as a safety net. live = live detection only. "
       "schedule = alert at the scheduled time.", options=["auto", "live", "schedule"]),
    _s("SERIES", "Series to alert for", "Alerts and timing", "multi", "cup,oreilly,truck",
       "Only these series get alerts and appear on the dashboard.", options=["cup", "oreilly", "truck"],
       option_labels={"cup": "Cup", "oreilly": "O'Reilly", "truck": "Truck"}, aliases={"xfinity": "oreilly"},
       allow_empty=False),
    _s("FIRST_ALERT_MIN", "Schedule mode: minutes after start", "Alerts and timing", "int", 10,
       "Used by schedule mode and when the live feed is down.", min=0, max=240),
    _s("SCHED_FALLBACK_MIN", "'May not have started' wait (min)", "Alerts and timing", "int", 90,
       "Auto mode: minutes past the scheduled time before you are told the race may be delayed.", min=0, max=600),
    _s("HOT_GRACE_MIN", "Hot track grace (min)", "Alerts and timing", "int", 45,
       "A hot track holds the delay notice back this long before it counts as a delay.", min=0, max=600),
    _s("NUDGE_MIN", "Follow-up spacing (min)", "Alerts and timing", "int", 45,
       "Minutes between the first alert and each follow-up.", min=1, max=600),
    _s("NUDGES", "Follow-ups", "Alerts and timing", "int", 1,
       "How many follow-up alerts after the first one (0 = none).", min=0, max=10),
    _s("FALLBACK_LIVE_HOURS", "Stream Deck fallback (hours)", "Alerts and timing", "int", 2,
       "If NASCAR's live feed is unavailable, the key stays green this long after the scheduled alert.",
       min=1, max=24),
    _s("EXCLUDE_NAMES", "Ignore races named", "Alerts and timing", "text", "arca",
       "Comma-separated words; live races whose name contains one are ignored."),

    _s("QUIET_DAYS", "No polling on", "Quiet hours", "multi", "mon,tue,wed,thu",
       "No requests to nascar.com on these days (overrides below still apply).", options=_DAYS_OPT,
       allow_empty=True),
    _s("QUIET_START_HOUR", "Quiet from (hour)", "Quiet hours", "int", 22, "Hour of day (0-23) in the quiet-hours time zone.",
       min=0, max=23),
    _s("QUIET_END_HOUR", "Quiet until (hour)", "Quiet hours", "int", 6, "Hour of day (0-23).", min=0, max=23),
    _s("QUIET_OVERRIDE", "Still poll for live/scheduled/postponed races", "Quiet hours", "bool", 1,
       "Recommended on. Off means strictly no polling during quiet hours."),
    _s("QUIET_TZ", "Quiet hours time zone", "Quiet hours", "tz", "America/New_York",
       "Time zone the quiet days and hours are evaluated in."),

    _s("PREVIEW", "Weekend preview", "Weekend preview", "bool", 1, "Send a weekly list of the weekend's races."),
    _s("PREVIEW_DAY", "Preview day", "Weekend preview", "select", "thu", "Day of the week.", options=_DAYS_OPT),
    _s("PREVIEW_TIME", "Preview time", "Weekend preview", "time", "18:00", "Your local time (24-hour)."),

    _s("DAILY_REMINDER", "Daily reminder", "Daily dashboard reminder", "bool", 1,
       "One notification a day to earn dashboard game points."),
    _s("DAILY_REMINDER_TIME", "Reminder time", "Daily dashboard reminder", "time", "22:00", "Your local time (24-hour)."),
    _s("DAILY_ONLY_CONFIGURED", "Only on days with a game", "Daily dashboard reminder", "bool", 0,
       "Skip the reminder on days that have no game named below."),
    _s("DASHBOARD_URL", "Dashboard link", "Daily dashboard reminder", "text", "",
       "Page the reminder's button opens (blank = the leaderboard link below)."),
    _s("GAME_MON", "Monday game", "Daily dashboard reminder", "text", "", "Leave blank for no named game."),
    _s("GAME_TUE", "Tuesday game", "Daily dashboard reminder", "text", "", ""),
    _s("GAME_WED", "Wednesday game", "Daily dashboard reminder", "text", "", ""),
    _s("GAME_THU", "Thursday game", "Daily dashboard reminder", "text", "", ""),
    _s("GAME_FRI", "Friday game", "Daily dashboard reminder", "text", "", ""),
    _s("GAME_SAT", "Saturday game", "Daily dashboard reminder", "text", "", ""),
    _s("GAME_SUN", "Sunday game", "Daily dashboard reminder", "text", "", ""),

    _s("CHECKIN_URL", "Leaderboard link", "Notification buttons", "text", "https://www.nascar.com/leaderboard",
       "Page the 'Open website' button opens."),
    _s("APP_URL", "Open-app link", "Notification buttons", "text", "",
       "Link that opens the NASCAR app (e.g. shortcuts://run-shortcut?name=NASCAR on iPhone). Blank = none."),
    _s("APP_INTENT", "Open-app Android intent", "Notification buttons", "text", "",
       "Android: name of the broadcast a MacroDroid/Tasker macro listens for. Used when the link above is blank."),
    _s("APP_LABEL", "Open-app button text", "Notification buttons", "text", "Open NASCAR app", ""),
    _s("WEB_LABEL", "Website button text", "Notification buttons", "text", "Open website", ""),
    _s("ACK_TOKEN", "Button token", "Notification buttons", "secret", "",
       "Secret that lets the notification's 'Checked in' / 'Points earned' buttons reach this server."),
    _s("ACK_BASE_URL", "Button base address", "Notification buttons", "text", "",
       "Address your phone can reach this server at, e.g. http://100.x.y.z:8099 (Tailscale)."),

    _s("DASHBOARD_PASSWORD", "Dashboard password", "Dashboard", "secret", "",
       "Required to edit settings. Blank = the dashboard is view-only. Sent over plain HTTP, so keep it on your "
       "home network or Tailscale."),

    _s("NTFY_URL", "ntfy server", "Advanced", "text", "https://ntfy.sh", ""),
    _s("NTFY_TOKEN", "ntfy access token", "Advanced", "secret", "", "Only for a protected ntfy topic."),
    _s("LIVE_POLL_SEC", "Live feed poll (seconds)", "Advanced", "int", 60, "", min=5, max=600),
    _s("END_GRACE_MIN", "Race-over grace (min)", "Advanced", "float", 5, "Minutes not-live before a race counts as over.",
       min=0, max=120),
    _s("REFRESH_MIN", "Schedule refresh (min)", "Advanced", "int", 60, "", min=5, max=1440),
    _s("WINDOW_HOURS", "Alert window (hours)", "Advanced", "int", 5, "How long after a scheduled start alerts can still go out.",
       min=1, max=24),
    _s("PRE_RACE_HOURS", "Poll before a race (hours)", "Advanced", "int", 2, "", min=0, max=12),
    _s("PENDING_DAYS", "Watch postponed races (days)", "Advanced", "int", 4, "", min=0, max=14),
    _s("EMPTY_RETRY_SEC", "Schedule retry (seconds)", "Advanced", "int", 300, "", min=30, max=3600),
    _s("EMPTY_RETRY_QUIET_SEC", "Schedule retry, quiet hours (seconds)", "Advanced", "int", 900, "", min=60, max=86400),
    _s("FEED_URL", "Schedule feed URL", "Advanced", "text", "https://cf.nascar.com/cacher/{year}/race_list_basic.json",
       "Keep {year} where the year goes."),
    _s("LIVE_FEED_URL", "Live feed URL", "Advanced", "text", "https://cf.nascar.com/live/feeds/live-feed.json", ""),
    _s("FEED_TZ", "Schedule feed time zone", "Advanced", "tz", "America/New_York", ""),
    _s("HTTP_PORT", "Dashboard / status port", "Advanced", "int", 8099,
       "Needs the container to be recreated to take effect.", min=1, max=65535, recreate=True),
    _s("ICON_URL", "Unraid icon URL", "Advanced", "text", "",
       "Read by docker compose, not the app: needs the container to be recreated to take effect.", recreate=True),
]
SETTINGS_BY_KEY = {x["key"]: x for x in SETTINGS}
SETTING_GROUPS = []
for _x in SETTINGS:
    if _x["group"] not in SETTING_GROUPS:
        SETTING_GROUPS.append(_x["group"])

CONFIG_FILE = os.environ.get("CONFIG_FILE", "/config/.env")


def parse_env_text(text):
    """Parse .env text into a dict. Supports KEY=VALUE, optional quotes, trailing ' # comment' on bare values."""
    out = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[7:].strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            q, val = val[0], val[1:-1]
            if q == '"':
                val = val.replace('\\"', '"').replace("\\\\", "\\")
        else:
            m = re.search(r"\s#", val)
            if m:
                val = val[:m.start()].rstrip()
        out[key] = val
    return out


def format_env_value(val):
    """Bare when safe, otherwise double-quoted (compatible with docker compose and parse_env_text)."""
    if re.fullmatch(r"[^\s#'\"$\\]*", val):
        return val
    return '"' + val.replace("\\", "\\\\").replace('"', '\\"') + '"'


def update_env_text(text, changes):
    """Apply {KEY: value} to .env text, keeping comments and layout. Empty value = turn the line off
    (commented out). A value equal to the default is only written if the key is required or already active."""
    lines = text.split("\n")
    active, commented = {}, {}
    for i, ln in enumerate(lines):
        st = ln.lstrip()
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", st)
        if m and not st.startswith("#"):
            active[m.group(1)] = i
            continue
        m2 = re.match(r"^#\s*([A-Z][A-Z0-9_]*)=", st)
        if m2:
            commented.setdefault(m2.group(1), i)
    appended = []
    for key, val in changes.items():
        spec = SETTINGS_BY_KEY.get(key, {})
        default = "" if spec.get("default") in (None, "") else str(spec["default"])
        keep = val != "" and (val != default or spec.get("required") or key in active)
        if key in active:
            lines[active[key]] = f"{key}={format_env_value(val)}" if val != "" else f"# {key}="
        elif keep and key in commented:
            lines[commented[key]] = f"{key}={format_env_value(val)}"
        elif keep:
            appended.append(f"{key}={format_env_value(val)}")
    if appended:
        while lines and lines[-1] == "":
            lines.pop()
        lines += ["", "# ---- added by the dashboard ----"] + appended
    out = "\n".join(lines)
    return out if out.endswith("\n") else out + "\n"


def apply_config_file():
    """Load CONFIG_FILE (the .env, bind-mounted into the container) over the environment so that the file is
    the single source of truth and a restart picks up dashboard edits. Returns True if the file was read."""
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            parsed = parse_env_text(f.read())
    except OSError:
        return False
    for k in SETTINGS_BY_KEY:
        if k not in parsed:
            os.environ.pop(k, None)
    os.environ.update(parsed)
    if hasattr(time, "tzset"):
        time.tzset()
    return True


CONFIG_LOADED = apply_config_file()


# ---------------------------------------------------------------- config ----
NTFY_URL = os.environ.get("NTFY_URL", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
NTFY_TOKEN = os.environ.get("NTFY_TOKEN", "").strip()
CHECKIN_URL = os.environ.get("CHECKIN_URL", "https://www.nascar.com/leaderboard")
# Optional second button that opens the NASCAR mobile app. Needs a link that opens the app
# (an app link, or e.g. shortcuts://run-shortcut?name=NASCAR on iPhone). Empty = no second button.
APP_URL = os.environ.get("APP_URL", "").strip()
# Android alternative: APP_INTENT makes the button send an Android broadcast that an automation app
# (MacroDroid/Tasker) turns into "launch NASCAR MOBILE". Used only when APP_URL is empty.
APP_INTENT = os.environ.get("APP_INTENT", "").strip()
APP_LABEL = os.environ.get("APP_LABEL", "Open NASCAR app").strip() or "Open NASCAR app"
WEB_LABEL = os.environ.get("WEB_LABEL", "Open website").strip() or "Open website"

LOCAL_TZ = get_tz(os.environ.get("TZ", "UTC"), "UTC")
FEED_TZ = get_tz(os.environ.get("FEED_TZ", "America/New_York"), "America/New_York")
FEED_URL = os.environ.get("FEED_URL", "https://cf.nascar.com/cacher/{year}/race_list_basic.json")
LIVE_FEED_URL = os.environ.get("LIVE_FEED_URL", "https://cf.nascar.com/live/feeds/live-feed.json")

MODE = os.environ.get("ALERT_MODE", "auto").strip().lower()
if MODE not in ("schedule", "live", "auto"):
    MODE = "auto"
FIRST_ALERT_MIN = env_int("FIRST_ALERT_MIN", 10)        # schedule mode: min after start
SCHED_FALLBACK_MIN = env_int("SCHED_FALLBACK_MIN", 90)  # auto mode: wait this long past the scheduled time
                                                        # (pre-race show, anthem, etc.) before "may not have started"
NUDGE_MIN = env_int("NUDGE_MIN", 45)
NUDGES = env_int("NUDGES", 1)
WINDOW_HOURS = env_int("WINDOW_HOURS", 5)
REFRESH_MIN = env_int("REFRESH_MIN", 60)                # how often to re-pull schedule
EMPTY_RETRY_SEC = env_int("EMPTY_RETRY_SEC", 300)               # retry when no schedule is loaded
EMPTY_RETRY_QUIET_SEC = env_int("EMPTY_RETRY_QUIET_SEC", 900)   # same, during quiet hours
HOT_GRACE_MIN = env_int("HOT_GRACE_MIN", 45)            # hot track this long without green => treat as a delay
LIVE_POLL_SEC = env_int("LIVE_POLL_SEC", 60)
END_GRACE_MIN = float(os.environ.get("END_GRACE_MIN", "5") or 5)  # not-live this long => race over
FALLBACK_LIVE_HOURS = env_int("FALLBACK_LIVE_HOURS", 2) # Stream Deck "live" if no feed
# Quiet hours: no requests to nascar.com at all (live feed or schedule) during these times.
QUIET_TZ = get_tz(os.environ.get("QUIET_TZ", "America/New_York"), "America/New_York")
QUIET_DAYS = {d.strip().lower()[:3] for d in os.environ.get("QUIET_DAYS", "mon,tue,wed,thu").split(",") if d.strip()}
QUIET_START_HOUR = env_int("QUIET_START_HOUR", 22)      # 10 pm in QUIET_TZ
QUIET_END_HOUR = env_int("QUIET_END_HOUR", 6)           # 6 am in QUIET_TZ
QUIET_OVERRIDE = env_int("QUIET_OVERRIDE", 1)           # 1 = still poll when a race is live/scheduled/postponed
PRE_RACE_HOURS = env_int("PRE_RACE_HOURS", 2)           # override window before a scheduled start
PENDING_DAYS = env_int("PENDING_DAYS", 4)               # watch for a postponed race this many days
# Live races whose name contains any of these (case-insensitive) are ignored, as a safety net for
# series that have no check-in (ARCA, etc.).
EXCLUDE_NAMES = [x.strip().lower() for x in os.environ.get("EXCLUDE_NAMES", "arca").split(",") if x.strip()]
ENABLED = {canon_series_key(s) for s in os.environ.get("SERIES", "cup,oreilly,truck").split(",")
           if canon_series_key(s)}

# Weekly "race weekend" preview notification (sent from the saved schedule; contacts nothing but ntfy)
PREVIEW = env_int("PREVIEW", 1)
_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
PREVIEW_DAY = _DAYS.index(os.environ.get("PREVIEW_DAY", "thu").strip().lower()[:3]) \
    if os.environ.get("PREVIEW_DAY", "thu").strip().lower()[:3] in _DAYS else 3
try:
    PREVIEW_TIME = tuple(int(x) for x in os.environ.get("PREVIEW_TIME", "18:00").split(":"))[:2]
except ValueError:
    PREVIEW_TIME = (18, 0)

# Daily "earn dashboard points" reminder, with an optional game name per weekday (GAME_MON ... GAME_SUN)
DAILY = env_int("DAILY_REMINDER", 1)
DAILY_ONLY_CONFIGURED = env_int("DAILY_ONLY_CONFIGURED", 0)   # 1 = only send on days that have a game set
try:
    DAILY_TIME = tuple(int(x) for x in os.environ.get("DAILY_REMINDER_TIME", "22:00").split(":"))[:2]
except ValueError:
    DAILY_TIME = (22, 0)
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "").strip() or CHECKIN_URL
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "").strip()
DAILY_GAMES = {d: os.environ.get(f"GAME_{d.upper()}", "").strip() for d in _DAYS}

DATA_DIR = os.environ.get("DATA_DIR", "/data")
HTTP_PORT = env_int("HTTP_PORT", env_int("ACK_PORT", 8099))   # 0 disables the HTTP server
ACK_BASE_URL = os.environ.get("ACK_BASE_URL", "").rstrip("/")
ACK_TOKEN = os.environ.get("ACK_TOKEN", "").strip()
ICON_PATH = os.environ.get("ICON_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png"))

SERIES_KEYS = (("series_1", "Cup"), ("series_2", "Oreilly"), ("series_3", "Truck"))
SERIES_BY_ID = {1: "Cup", 2: "Oreilly", 3: "Truck"}
LIVE_FLAGS = {1, 2, 3, 4}     # green, yellow, red, white (race under way)
END_FLAGS = {5, 9}            # checkered, cold track
# flag_state -> name reported as "state" on /status (white counts as green; checkered/cold = offline)
STATE_NAMES = {1: "green", 2: "yellow", 3: "red", 4: "green", 5: "offline", 8: "hot", 9: "offline"}
STATE_FRESH_SEC = max(3 * env_int("LIVE_POLL_SEC", 60), 180)   # how long a feed reading stays trustworthy
TEST_STATES = ["green", "yellow", "red", "hot", "offline"]

log = logging.getLogger("nascar-checkin")
STATE_LOCK = threading.Lock()
LIVE_LOCK = threading.Lock()
LIVE = {}                     # series label -> {source, since, last_seen/until, ...}
RT = {"live_ok_ts": 0.0, "live_error": "never polled", "poll_state": "starting",
      "flag": None, "flag_series": None, "flag_is_race": False, "schedule_error": "",
      "hot_since": None}


# ----------------------------------------------------------------- state ----
def state_path():
    return os.path.join(DATA_DIR, "state.json")


def load_state():
    s = {}
    try:
        with open(state_path()) as f:
            s = json.load(f)
    except Exception:
        pass
    for k, v in (("sent", {}), ("acked", []), ("events", {}), ("starts", {}), ("last_error_alert", 0), ("done", {})):
        s.setdefault(k, v)
    return migrate_series(s)


def migrate_series(s):
    """Saved state from before the Xfinity -> O'Reilly rename: carry the old entries over."""
    def fix(k):
        return k.replace("live:Xfinity:", "live:Oreilly:").replace(":Xfinity:", ":Oreilly:")
    for ev in s["events"].values():
        if ev.get("series") == "Xfinity":
            ev["series"] = "Oreilly"
    s["events"] = {fix(k): v for k, v in s["events"].items()}
    s["sent"] = {fix(k): v for k, v in s["sent"].items()}
    s["acked"] = [fix(k) for k in s["acked"]]
    s["done"] = {fix(k): v for k, v in s["done"].items()}
    return s


def save_state(state):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = state_path() + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, state_path())


# --------------------------------------------------------------- schedule ----
def parse_dt(value, default_tz):
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=default_tz)
    return dt.astimezone(timezone.utc)


def load_manual(path):
    with open(path) as f:
        raw = json.load(f)
    races = []
    for r in raw:
        start = parse_dt(r["start"], LOCAL_TZ)
        races.append({
            "id": r.get("id") or f'{canon_label(r["series"])}-{r.get("name", "race")}',
            "series": canon_label(r["series"]),
            "name": r.get("name", "Race"),
            "track": r.get("track", ""),
            "start": start,
        })
    return races


def parse_feed(data, year):
    races = []
    for key, label in SERIES_KEYS:
        for r in data.get(key, []) or []:
            ts = r.get("tunein_date") or r.get("date_scheduled") or r.get("race_date")
            if not ts:
                continue
            try:
                start = parse_dt(ts, FEED_TZ)
            except ValueError:
                continue
            races.append({
                "id": f'{year}-{key}-{r.get("race_id", ts)}',
                "series": label,
                "name": r.get("race_name") or r.get("track_name") or "Race",
                "track": r.get("track_name") or "",
                "start": start,
            })
    return races


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (nascar-checkin reminder)"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_feed(year):
    return fetch_json(FEED_URL.format(year=year))


# The schedule feed sometimes returns partial or empty data (seen: only one series, or zero races), so the
# schedule is merged into a persistent cache instead of being replaced.
def cache_path():
    return os.path.join(DATA_DIR, "schedule_cache.json")


def load_schedule_cache():
    try:
        with open(cache_path()) as f:
            return [{"id": r["id"], "series": canon_label(r["series"]), "name": r["name"], "track": r.get("track", ""),
                     "start": datetime.fromisoformat(r["start"])} for r in json.load(f)]
    except Exception:
        return []


def save_schedule_cache(races):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = cache_path() + ".tmp"
    with open(tmp, "w") as f:
        json.dump([{"id": r["id"], "series": r["series"], "name": r["name"], "track": r.get("track", ""),
                    "start": r["start"].isoformat()} for r in races], f)
    os.replace(tmp, cache_path())


def merge_races(known, new):
    """New data wins for races it contains; races it lacks are kept (dropping those over 30 days old)."""
    merged = {r["id"]: r for r in known}
    for r in new:
        merged[r["id"]] = r
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    return sorted((r for r in merged.values() if r["start"] > cutoff), key=lambda r: r["start"])


def load_races(known=None):
    """Returns (races, source). A races.json in DATA_DIR overrides the feed. A bad download
    (error, empty, or partial) never shrinks the schedule we already have."""
    manual = os.path.join(DATA_DIR, "races.json")
    if os.path.exists(manual):
        RT["schedule_error"] = ""
        return load_manual(manual), "races.json"
    base = known if known else load_schedule_cache()
    year = datetime.now(LOCAL_TZ).year
    try:
        data = fetch_feed(year)
    except Exception as e:
        RT["schedule_error"] = f"download failed: {str(e)[:150]}"
        log.warning("Schedule fetch failed: %s", e)
        return base, ("cache" if base else "none")
    new = parse_feed(data, year)
    if not new:
        keys = sorted(data.keys())[:8] if isinstance(data, dict) else type(data).__name__
        RT["schedule_error"] = f"downloaded but found no races (feed keys: {keys})"
        log.warning("Schedule feed had no races (keys: %s); keeping %d known races", keys, len(base))
        return base, ("cache (feed was empty)" if base else "none")
    merged = merge_races(base, new)
    if base and len(new) < len(merged):
        log.warning("Schedule feed was partial: %d races in feed, keeping %d known races", len(new), len(merged))
    save_schedule_cache(merged)
    RT["schedule_error"] = ""
    return merged, "feed"


# ------------------------------------------------------------------- ntfy ----
def ntfy_publish(payload):
    if not NTFY_TOPIC:
        log.error("NTFY_TOPIC is not set")
        return False
    payload = dict(payload, topic=NTFY_TOPIC)
    headers = {"Content-Type": "application/json"}
    if NTFY_TOKEN:
        headers["Authorization"] = "Bearer " + NTFY_TOKEN
    req = urllib.request.Request(NTFY_URL, data=json.dumps(payload).encode("utf-8"),
                                 headers=headers, method="POST")
    ok = False
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            ok = 200 <= resp.status < 300
    except Exception as e:
        log.warning("ntfy publish failed: %s", e)
    record_notification(payload, ok)
    return ok


HISTORY_LOCK = threading.Lock()


def history_path():
    return os.path.join(DATA_DIR, "history.jsonl")


def record_notification(payload, ok):
    """Append to the notification log (read by the dashboard). Never raises."""
    try:
        entry = {"ts": time.time(), "title": str(payload.get("title", ""))[:200],
                 "message": str(payload.get("message", ""))[:1000], "ok": bool(ok)}
        os.makedirs(DATA_DIR, exist_ok=True)
        with HISTORY_LOCK:
            path = history_path()
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
            if os.path.getsize(path) > 100_000:                       # keep it small
                with open(path, encoding="utf-8") as f:
                    tail = f.readlines()[-50:]
                with open(path, "w", encoding="utf-8") as f:
                    f.writelines(tail)
    except Exception:
        log.exception("could not record notification")


def recent_notifications(n=5):
    try:
        with open(history_path(), encoding="utf-8") as f:
            lines = f.readlines()[-max(n * 3, 20):]
    except OSError:
        return []
    out = []
    for ln in reversed(lines):
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
        if len(out) >= n:
            break
    return out


def fmt_local(dt):
    return dt.astimezone(LOCAL_TZ).strftime("%a %b %d, %I:%M %p").replace(" 0", " ")


# ------------------------------------------------------------- live feed ----
def to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def first_key(d, *keys):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def interpret_live(data):
    """Returns (healthy, entries). healthy = feed contains a recognisable flag state."""
    items = data if isinstance(data, list) else [data]
    entries, healthy = [], False
    for it in items:
        if not isinstance(it, dict):
            continue
        flag = to_int(first_key(it, "flag_state", "flag"))
        if flag is None:
            continue
        healthy = True
        sid = to_int(first_key(it, "series_id"))
        entries.append({
            "flag": flag,
            "run_type": to_int(first_key(it, "run_type")),
            "series_id": sid,
            # only Cup/O'Reilly/Truck have check-in; other series (ARCA etc.) are ignored
            "supported": sid in SERIES_BY_ID and SERIES_BY_ID[sid].lower() in ENABLED and not any(
                x in str(first_key(it, "run_name", "race_name") or "").lower() for x in EXCLUDE_NAMES),
            "label": SERIES_BY_ID.get(sid, "NASCAR" if sid is None else f"series {sid}"),
            "race_id": first_key(it, "race_id"),
            "name": first_key(it, "run_name", "race_name"),
            "lap": first_key(it, "lap_number"),
        })
    return healthy, entries


def entry_is_live(e):
    return e.get("supported", True) and e["flag"] in LIVE_FLAGS and e["run_type"] in (None, 3)   # run_type 3 = race


def live_healthy(now_ts):
    if not RT["live_ok_ts"]:
        return False
    if RT["poll_state"].startswith("quiet") and not RT["live_error"]:
        return True            # not polling right now; last poll was fine
    return now_ts - RT["live_ok_ts"] < 600


def poll_live(state, now_ts):
    try:
        healthy, entries = interpret_live(fetch_json(LIVE_FEED_URL))
    except Exception as e:
        RT["live_error"] = str(e)[:200]
        return
    if not healthy:
        RT["live_error"] = "feed has no flag_state field"
        return
    RT["live_ok_ts"], RT["live_error"] = now_ts, ""
    if entries:
        e0 = entries[0]
        if e0["flag"] != RT["flag"]:
            log.info("Feed flag %s -> %s (%s, series_id %s, run_type %s, lap %s, %r)%s", RT["flag"], e0["flag"],
                     e0["label"], e0["series_id"], e0["run_type"], e0.get("lap"), e0["name"],
                     "" if e0["supported"] else " - IGNORED (not a Cup/O'Reilly/Truck race)")
        RT["flag"], RT["flag_series"] = e0["flag"], e0["label"]
        RT["flag_is_race"] = e0["run_type"] in (None, 3) and e0["supported"]
        if e0["flag"] == 8 and RT["flag_is_race"]:
            if RT["hot_since"] is None:
                RT["hot_since"] = now_ts
        else:
            RT["hot_since"] = None
    for e in entries:
        label = e["label"]
        if entry_is_live(e):
            with LIVE_LOCK:
                cur = LIVE.get(label)
                new = not (cur and cur["source"] == "feed")
                if new:
                    LIVE[label] = {"source": "feed", "since": now_ts, "last_seen": now_ts,
                                   "race_id": e["race_id"], "name": e["name"]}
                else:
                    cur["last_seen"] = now_ts
            if new:
                log.info("%s race detected live (flag %s)", label, e["flag"])
                on_live_start(state, label, e, now_ts)
        elif e["flag"] in END_FLAGS:
            with LIVE_LOCK:
                cur = LIVE.get(label)
                if cur and cur["source"] == "feed":
                    del LIVE[label]
                    log.info("%s race over (flag %s)", label, e["flag"])


def on_live_start(state, label, e, now_ts):
    day = datetime.fromtimestamp(now_ts, LOCAL_TZ).date().isoformat()
    eid = f'live:{label}:{e["race_id"] if e["race_id"] is not None else day}'
    with STATE_LOCK:
        if eid in state["events"]:
            return
        state["events"][eid] = {"series": label, "name": e["name"] or f"{series_name(label)} race",
                                "first": now_ts, "source": "feed",
                                "ident": "" if e["race_id"] is None else str(e["race_id"])}
        save_state(state)


def expire_live(now_ts):
    with LIVE_LOCK:
        for label, cur in list(LIVE.items()):
            if cur["source"] == "feed" and now_ts - cur["last_seen"] > END_GRACE_MIN * 60:
                del LIVE[label]
                log.info("%s no longer live", label)
            elif cur["source"] == "schedule" and now_ts > cur["until"]:
                del LIVE[label]


def icon_test_path():
    return os.path.join(DATA_DIR, "icon_test.json")


def icon_test_state(now_ts):
    """While `--icon-test` runs, returns the state to show (cycles through TEST_STATES), else None."""
    try:
        with open(icon_test_path()) as f:
            t = json.load(f)
        start, step, until = float(t["start"]), float(t["step"]), float(t["until"])
    except Exception:
        return None
    if not (start <= now_ts < until):
        return None
    return TEST_STATES[min(int((now_ts - start) // step), len(TEST_STATES) - 1)]


def current_state(now_ts):
    """Track state name for /status: green/yellow/red/hot/offline."""
    t = icon_test_state(now_ts)
    if t:
        return t
    fresh = RT["live_ok_ts"] and now_ts - RT["live_ok_ts"] <= STATE_FRESH_SEC
    if fresh and RT["flag"] is not None and RT["flag_is_race"]:
        return STATE_NAMES.get(RT["flag"], "offline")
    with LIVE_LOCK:
        if any(v["source"] == "schedule" for v in LIVE.values()):
            return "green"            # no usable live feed: assume green while "live"
    return "offline"


def status_snapshot():
    now_ts = time.time()
    expire_live(now_ts)
    with LIVE_LOCK:
        live = set(LIVE)
    state = current_state(now_ts)
    testing = icon_test_state(now_ts) is not None
    if testing:
        live = {"Cup", "Oreilly", "Truck"} if state in ("green", "yellow", "red") else set()
    out = {"live": bool(live), "state": state}
    for s in ("Cup", "Oreilly", "Truck"):
        out[s.lower()] = s in live
    out["xfinity"] = out["oreilly"]          # old name, kept so existing Stream Deck rules keep working
    fresh = bool(RT["live_ok_ts"]) and now_ts - RT["live_ok_ts"] <= STATE_FRESH_SEC
    out.update({"flag": RT["flag"] if fresh else None, "series": RT["flag_series"] if fresh else None,
                "test": testing, "mode": MODE, "polling": RT["poll_state"],
                "live_feed_ok": bool(live_healthy(now_ts)),
                "updated": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    return out


# ------------------------------------------------------------ checklist (points earned) ----
DASH = {"races": []}          # the current schedule, shared with the HTTP handler


def race_ident(r):
    """Stable id for a race, matching NASCAR's race_id for feed races."""
    m = re.match(r"^\d{4}-series_\d+-(.+)$", r["id"])
    return m.group(1) if m else r["id"]


def race_key(r):
    return f"race:{r['series']}:{race_ident(r)}"


def resolve_race_key(series, ident, when_ts, races):
    """Find the schedule race an alert belongs to (by race id, else nearest start time within 18 h)."""
    for r in races:
        if r["series"] == series and ident and race_ident(r) == str(ident):
            return race_key(r)
    best = None
    for r in races:
        if r["series"] == series:
            d = abs(r["start"].timestamp() - when_ts)
            if d <= 18 * 3600 and (best is None or d < best[0]):
                best = (d, r)
    return race_key(best[1]) if best else None


def set_done(state, key, done):
    with STATE_LOCK:
        if done:
            state["done"][key] = time.time()
        else:
            state["done"].pop(key, None)
        cutoff = time.time() - 21 * 86400
        for k in [k for k, v in state["done"].items() if v < cutoff]:
            del state["done"][k]
        save_state(state)


def mark_event_done(state, eid):
    """The notification's 'Checked in' button also ticks the race off on the dashboard."""
    ev = state["events"].get(eid)
    if not ev:
        return
    key = resolve_race_key(ev["series"], ev.get("ident"), ev["first"], DASH["races"])
    if key:
        set_done(state, key, True)
        log.info("Checked off %s from the notification button", key)


# ------------------------------------------------------------ quiet hours ----
def quiet_parts(now):
    """(day_quiet, night_quiet) evaluated in QUIET_TZ."""
    t = now.astimezone(QUIET_TZ)
    day_quiet = t.strftime("%a").lower()[:3] in QUIET_DAYS
    h = t.hour
    if QUIET_START_HOUR > QUIET_END_HOUR:
        night_quiet = h >= QUIET_START_HOUR or h < QUIET_END_HOUR
    else:
        night_quiet = QUIET_START_HOUR <= h < QUIET_END_HOUR
    return day_quiet, night_quiet


def pending_postponed(races, state, now):
    """A race whose scheduled start passed recently but we never saw it run."""
    for r in races:
        if r["series"].lower() not in ENABLED:
            continue
        age = now - r["start"]
        if timedelta(hours=WINDOW_HOURS) < age <= timedelta(days=PENDING_DAYS):
            ts = r["start"].timestamp() - 3600
            resolved = any(e["source"] in ("feed", "schedule") and e["series"] == r["series"]
                           and e["first"] >= ts for e in state["events"].values())
            if not resolved:
                return True
    return False


def poll_decision(races, state, now):
    """Returns (poll: bool, reason: str). Decides if we may contact nascar.com right now."""
    day_q, night_q = quiet_parts(now)
    if not (day_q or night_q):
        return True, "active"
    label = "quiet-day" if day_q else "quiet-night"
    if not QUIET_OVERRIDE:
        return False, label
    with LIVE_LOCK:
        if any(v["source"] == "feed" for v in LIVE.values()):
            return True, "override: race live"
    for r in races:
        if r["series"].lower() in ENABLED and \
                r["start"] - timedelta(hours=PRE_RACE_HOURS) <= now < r["start"] + timedelta(hours=WINDOW_HOURS):
            return True, "override: race scheduled"
    if not night_q and pending_postponed(races, state, now):
        return True, "override: watching for postponed race"
    return False, label


# ---------------------------------------------------------------- alerts ----
def build_actions(eid=None, url=None, plain_label="Open leaderboard", daily_date=None):
    """Notification buttons (ntfy allows at most 3): website, NASCAR app (optional), Checked in (optional)."""
    actions = [{"action": "view", "label": WEB_LABEL if (APP_URL or APP_INTENT) else plain_label,
                "url": url or CHECKIN_URL, "clear": True}]
    if APP_URL:
        actions.append({"action": "view", "label": APP_LABEL, "url": APP_URL, "clear": True})
    elif APP_INTENT:
        actions.append({"action": "broadcast", "label": APP_LABEL, "intent": APP_INTENT,
                        "extras": {"app": "nascar"}, "clear": True})
    if eid and HTTP_PORT and ACK_BASE_URL and ACK_TOKEN:
        ack = f'{ACK_BASE_URL}/ack/{urllib.parse.quote(eid, safe="")}?t={ACK_TOKEN}'
        actions.append({"action": "http", "label": "Checked in", "url": ack,
                        "method": "POST", "clear": True})
    if daily_date and HTTP_PORT and ACK_BASE_URL and ACK_TOKEN:
        ack = f"{ACK_BASE_URL}/ackdaily/{daily_date}?t={ACK_TOKEN}"
        actions.append({"action": "http", "label": "Points earned", "url": ack, "method": "POST", "clear": True})
    return actions


def send_alert(eid, ev, stage):
    first = stage == 0
    series, name = series_name(ev["series"]), ev["name"]
    src = ev["source"]
    if src == "feed":
        title = f"{series} race is live - check in" if first else f"Still time: {series} check-in"
        body = f"{name} is under way. Open the leaderboard and press Check In."
    elif src == "fallback":
        title = f"{series} race may not have started"
        when = fmt_local(datetime.fromtimestamp(ev["sched"], timezone.utc))
        if ev.get("hot_min") is not None:
            body = (f"{name} was scheduled for {when}. The track has been hot for {ev['hot_min']} min "
                    "but there's no green flag yet (delay?). You'll get another alert when it goes green.")
        else:
            body = (f"{name} was scheduled for {when}, but NASCAR's live feed doesn't show it running "
                    "(delay?). You'll get another alert when it goes live.")
    else:
        title = f"{series} race is live - check in" if first else f"Still time: {series} check-in"
        body = (f'{name} (scheduled {fmt_local(datetime.fromtimestamp(ev["sched"], timezone.utc))}). '
                "Open the leaderboard and press Check In.")
    actions = build_actions(eid)
    return ntfy_publish({
        "title": title, "message": body,
        "priority": 4 if first else 3,
        "tags": ["checkered_flag"] if first and src != "fallback" else
                (["hourglass"] if src == "fallback" else ["alarm_clock"]),
        "click": CHECKIN_URL, "actions": actions,
    })


def process_events(state, now_ts):
    for eid, ev in list(state["events"].items()):
        with STATE_LOCK:
            if eid in state["acked"]:
                continue
            age = now_ts - ev["first"]
            sent = state["sent"].get(eid, 0)
        if age < 0 or age > WINDOW_HOURS * 3600:
            continue
        if ev["source"] == "feed":
            with LIVE_LOCK:
                still_live = ev["series"] in LIVE
            if not still_live and sent:
                continue
        max_stage = 0 if ev["source"] == "fallback" else NUDGES
        due = min(1 + int(age // (NUDGE_MIN * 60)), 1 + max_stage)
        if due > sent:
            if send_alert(eid, ev, due - 1):
                with STATE_LOCK:
                    state["sent"][eid] = due
                    save_state(state)
                log.info("Alert %d sent: %s", due, eid)
                if ev["source"] == "schedule" and sent == 0:
                    with LIVE_LOCK:      # no live feed: assume live for a while (Stream Deck)
                        LIVE.setdefault(ev["series"], {"source": "schedule", "since": now_ts,
                                                       "until": now_ts + FALLBACK_LIVE_HOURS * 3600})


def hot_minutes(series, now_ts):
    """Minutes the feed has continuously shown a HOT track for this series' race, else None.
    Hot is shown both before the green flag and while jet dryers work during a delay, so it only
    holds the "may not have started" notice back for HOT_GRACE_MIN."""
    if RT["hot_since"] is None or not RT["live_ok_ts"] or now_ts - RT["live_ok_ts"] > STATE_FRESH_SEC:
        return None
    if RT["flag_series"] not in (series, "NASCAR"):
        return None
    return (now_ts - RT["hot_since"]) / 60.0


def schedule_tick(races, state, now):
    if MODE == "live":
        return
    now_ts = now.timestamp()
    healthy = bool(live_healthy(now_ts)) and MODE == "auto"
    for r in races:
        if r["series"].lower() not in ENABLED:
            continue
        delay = SCHED_FALLBACK_MIN if healthy else FIRST_ALERT_MIN
        first = r["start"] + timedelta(minutes=max(delay, FIRST_ALERT_MIN))
        if not (first <= now < r["start"] + timedelta(hours=WINDOW_HOURS)):
            continue
        eid = r["id"]
        with STATE_LOCK:
            if eid in state["events"]:
                continue
            # a live-detected alert for this series since just before the scheduled start covers it
            covered = any(e["source"] == "feed" and e["series"] == r["series"]
                          and e["first"] >= r["start"].timestamp() - 3600
                          for e in state["events"].values())
        with LIVE_LOCK:
            covered = covered or (r["series"] in LIVE and LIVE[r["series"]]["source"] == "feed")
        if covered:
            continue
        hot = hot_minutes(r["series"], now_ts) if healthy else None
        if hot is not None and hot < HOT_GRACE_MIN:
            continue          # track just went hot: pre-race / restart is probably imminent, wait
        with STATE_LOCK:
            state["events"][eid] = {
                "series": r["series"], "name": r["name"], "first": first.timestamp(),
                "sched": r["start"].timestamp(), "source": "fallback" if healthy else "schedule",
                "ident": race_ident(r)}
            if hot is not None:
                state["events"][eid]["hot_min"] = int(hot)
            save_state(state)


def check_reschedules(races, state, now):
    starts = state["starts"]
    for r in races:
        if r["series"].lower() not in ENABLED:
            continue
        iso = r["start"].isoformat()
        old = starts.get(r["id"])
        if old and old != iso:
            try:
                od = datetime.fromisoformat(old)
                moved = abs((r["start"] - od).total_seconds()) >= 600
            except ValueError:
                moved = False
            if moved and r["start"] > now - timedelta(hours=WINDOW_HOURS):
                ntfy_publish({"title": f'{series_name(r["series"])} race moved',
                              "message": f'{r["name"]}: {fmt_local(od)} -> {fmt_local(r["start"])}',
                              "priority": 3, "tags": ["calendar"]})
                log.info("Reschedule: %s %s -> %s", r["name"], old, iso)
        starts[r["id"]] = iso
    with STATE_LOCK:
        save_state(state)


def prune(state, now_ts):
    cutoff = now_ts - 3 * 86400
    with STATE_LOCK:
        for eid, ev in list(state["events"].items()):
            if ev["first"] < cutoff:
                del state["events"][eid]
                state["sent"].pop(eid, None)


# ------------------------------------------------------------- weekend preview ----
# (title, intro) pairs; {series} becomes e.g. "Trucks and Cup". One is picked at random each week,
# never the same one twice in a row.
PREVIEW_VARIATIONS = [
    ("Start your engines", "Get ready to start your engines! We've got {series} this weekend:"),
    ("Race weekend ahead", "Race weekend is almost here. Here's what's on tap for {series}:"),
    ("Green flag countdown", "Green flag countdown: {series} this weekend."),
    ("Pit road is open", "Pit crews ready? Here's your {series} lineup for the weekend:"),
    ("Strap in", "Strap in! {series} racing this weekend:"),
    ("It's almost go time", "It's almost go time. Here comes {series}:"),
    ("Weekend forecast", "This weekend's forecast: 100% chance of {series} racing."),
    ("Fuel up", "Fuel up and settle in. {series} this weekend:"),
    ("Clear your schedule", "Clear your schedule: {series} this weekend!"),
    ("Drivers, to your cars", "Drivers, to your cars! {series} on deck this weekend:"),
]
SERIES_ORDER = ["Truck", "Oreilly", "Cup"]
SERIES_PLURAL = {"Truck": "Trucks", "Oreilly": "O'Reilly", "Cup": "Cup"}


def join_words(items):
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + (", and " if len(items) > 2 else " and ") + items[-1]


def fmt_when(dt):
    t = dt.astimezone(LOCAL_TZ)
    return t.strftime("%a %I:%M %p %Z").replace(" 0", " ")


def weekend_window(now):
    """From now until the start of the next Monday (local time)."""
    local = now.astimezone(LOCAL_TZ)
    days = (7 - local.weekday()) % 7 or 7
    end = (local + timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    return now, end


def build_preview(races, now, last_idx=None, rng=random):
    """Returns (title, body, idx) or None (offseason: nothing to say)."""
    start, end = weekend_window(now)
    enabled = [r for r in races if r["series"].lower() in ENABLED]
    week = sorted((r for r in enabled if start <= r["start"] < end), key=lambda r: r["start"])
    if not week:
        upcoming = sorted((r for r in enabled if r["start"] >= now), key=lambda r: r["start"])
        if not upcoming or upcoming[0]["start"] > now + timedelta(days=21):
            return None            # offseason: stay quiet
        nxt = upcoming[0]
        return ("No races this weekend",
                f"Nothing on the NASCAR schedule this weekend. Enjoy the break! "
                f"Next up: {series_name(nxt['series'])} {nxt['name']} on {fmt_when(nxt['start'])}.", None)
    options = [i for i in range(len(PREVIEW_VARIATIONS)) if i != last_idx]
    idx = rng.choice(options)
    title, intro = PREVIEW_VARIATIONS[idx]
    present = [s for s in SERIES_ORDER if any(r["series"] == s for r in week)]
    present += sorted({r["series"] for r in week} - set(SERIES_ORDER))
    lines = [intro.format(series=join_words(SERIES_PLURAL.get(s, s) for s in present)), ""]
    for r in week:
        place = f" at {r['track']}" if r.get("track") else ""
        lines.append(f"\u2022 {fmt_when(r['start'])} \u2013 {series_name(r['series'])}: {r['name']}{place}")
    return title, "\n".join(lines), idx


def maybe_send_preview(races, state, now):
    if not PREVIEW or not races:
        return
    local = now.astimezone(LOCAL_TZ)
    if local.weekday() != PREVIEW_DAY or (local.hour, local.minute) < PREVIEW_TIME:
        return
    iso = local.isocalendar()
    week_key = f"{iso[0]}-W{iso[1]}"
    with STATE_LOCK:
        if state.get("preview_week") == week_key:
            return
        last = state.get("preview_last")
    built = build_preview(races, now, last)
    if built is None:
        with STATE_LOCK:
            state["preview_week"] = week_key       # offseason: skip quietly this week
            save_state(state)
        return
    title, body, idx = built
    if ntfy_publish({"title": title, "message": body, "priority": 3, "tags": ["checkered_flag"]}):
        with STATE_LOCK:
            state["preview_week"] = week_key
            if idx is not None:
                state["preview_last"] = idx
            save_state(state)
        log.info("Weekend preview sent (%s)", title)


# ----------------------------------------------------------- daily dashboard reminder ----
def build_daily(day_idx):
    """(title, body) for weekday index 0=Mon, or None if this day should be skipped."""
    game = DAILY_GAMES.get(_DAYS[day_idx], "")
    if not game and DAILY_ONLY_CONFIGURED:
        return None
    if game:
        return (f"Dashboard game today: {game}",
                f"Don't forget to earn your extra points on the NASCAR Fan Rewards dashboard today. "
                f"Today's game: {game}.")
    return ("Dashboard points reminder",
            "Don't forget to earn your extra points on the NASCAR Fan Rewards dashboard today.")


def maybe_send_daily(state, now):
    if not DAILY:
        return
    local = now.astimezone(LOCAL_TZ)
    if (local.hour, local.minute) < DAILY_TIME:
        return
    today = local.date().isoformat()
    with STATE_LOCK:
        if state.get("daily_date") == today:
            return
    built = build_daily(local.weekday())
    if built is not None:
        title, body = built
        if not ntfy_publish({"title": title, "message": body, "priority": 3, "tags": ["video_game"],
                             "click": DASHBOARD_URL,
                             "actions": build_actions(None, DASHBOARD_URL, "Open dashboard",
                                                  daily_date=today)}):
            return                      # try again on the next tick
        log.info("Daily reminder sent (%s)", title)
    with STATE_LOCK:
        state["daily_date"] = today
        save_state(state)


# --------------------------------------------------------- custom key icons / banner ----
BANNER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "banner.jpg")
MAX_ICON_BYTES = 512 * 1024
ICON_TYPES = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}


def sniff_image(data):
    """File extension for the image types the dashboard accepts, judged by the file's first bytes."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:4] == b"GIF8":
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def icon_dir():
    return os.path.join(DATA_DIR, "icons")


def custom_icon(state):
    """(path, content type) of an uploaded icon for this state, or None."""
    for ext, ctype in ICON_TYPES.items():
        path = os.path.join(icon_dir(), f"{state}.{ext}")
        if os.path.isfile(path):
            return path, ctype
    return None


def save_custom_icon(state, data):
    ext = sniff_image(data)
    if ext is None:
        raise ValueError("Use a PNG, JPEG, GIF or WebP image.")
    os.makedirs(icon_dir(), exist_ok=True)
    reset_custom_icon(state)
    with open(os.path.join(icon_dir(), f"{state}.{ext}"), "wb") as f:
        f.write(data)


def reset_custom_icon(state):
    for ext in ICON_TYPES:
        try:
            os.remove(os.path.join(icon_dir(), f"{state}.{ext}"))
        except OSError:
            pass


def icon_versions():
    """{state: modified-time or None}; the dashboard uses it to show custom icons and bust its cache."""
    out = {}
    for st in TEST_STATES:
        ci = custom_icon(st)
        out[st] = int(os.path.getmtime(ci[0])) if ci else None
    return out


# ------------------------------------------------------------------ dashboard ----
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DASHBOARD_HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")
SESSIONS = {}                 # token -> expiry (memory only; a restart logs everyone out)
LOGIN_FAILS = {}              # ip -> [timestamps]
SESSION_SECONDS = 12 * 3600


def week_bounds(now):
    local = now.astimezone(LOCAL_TZ)
    monday = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return monday, monday + timedelta(days=7)


def build_races_view(races, state, now):
    """This race week (Monday-Sunday, local time): one row per race with its check-in state."""
    monday, nxt = week_bounds(now)
    enabled = [r for r in races if r["series"].lower() in ENABLED]
    week = sorted((r for r in enabled if monday <= r["start"] < nxt), key=lambda r: r["start"])
    with LIVE_LOCK:
        live = {k: dict(v) for k, v in LIVE.items()}
    latest_started = {}
    for r in week:
        if r["start"] <= now:
            latest_started[r["series"]] = r
    rows = []
    with STATE_LOCK:
        done = dict(state["done"])
    for r in week:
        key = race_key(r)
        started = r["start"] <= now
        age_h = (now - r["start"]).total_seconds() / 3600
        lv = live.get(r["series"])
        is_live = bool(lv and lv["source"] == "feed" and started and age_h < 10
                       and latest_started.get(r["series"]) is r)
        if is_live:
            status = "live"
        elif not started:
            status = "upcoming"
        elif age_h <= 12:
            status = "started"
        else:
            status = "past"
        rows.append({"key": key, "series": r["series"], "series_name": series_name(r["series"]),
                     "name": r["name"], "track": r.get("track", ""),
                     "when": fmt_when(r["start"]), "start_ts": r["start"].timestamp(), "status": status,
                     "done": key in done, "done_ts": done.get(key), "can_mark": started})
    next_up = []
    if not rows:
        upcoming = sorted((r for r in enabled if r["start"] > now and r["start"] < now + timedelta(days=21)),
                          key=lambda r: r["start"])
        next_up = [{"series": r["series"], "series_name": series_name(r["series"]),
                    "name": r["name"], "track": r.get("track", ""),
                    "when": fmt_when(r["start"])} for r in upcoming[:3]]
    return rows, next_up


def build_daily_view(state, now):
    """Configured daily games for this week; only today's can be ticked."""
    local = now.astimezone(LOCAL_TZ)
    monday, _ = week_bounds(now)
    today = local.date()
    with STATE_LOCK:
        done = dict(state["done"])
    rows = []
    for i, d in enumerate(_DAYS):
        game = DAILY_GAMES.get(d, "")
        if not game:
            continue
        date = (monday + timedelta(days=i)).date()
        key = f"daily:{date.isoformat()}"
        rows.append({"key": key, "day": d, "label": DAY_NAMES[i], "date": date.isoformat(), "game": game,
                     "state": "today" if date == today else ("upcoming" if date > today else "past"),
                     "done": key in done, "done_ts": done.get(key), "can_mark": date == today})
    return rows


def build_summary(races_rows, daily_rows):
    pending = []
    for r in races_rows:
        if r["status"] in ("live", "started") and not r["done"]:
            pending.append({"kind": "race", "label": f"{r['series_name']}: {r['name']}"})
    for d in daily_rows:
        if d["state"] == "today" and not d["done"]:
            pending.append({"kind": "daily", "label": f"{d['game']} ({d['label']})"})
    return {"pending": pending, "caught_up": not pending}


def edit_block_reason():
    if not DASHBOARD_PASSWORD:
        return ("Editing is off. Add DASHBOARD_PASSWORD=<something> to your .env once (by hand), "
                "recreate the container, and this section unlocks.")
    if not CONFIG_LOADED:
        return ("The .env file isn't visible to the app. Add the volume '- ./.env:/config/.env' to "
                "docker-compose.yml and recreate the container.")
    if not os.access(CONFIG_FILE, os.W_OK):
        return "The .env file is mounted read-only. Remove ':ro' from its volume line in docker-compose.yml."
    return ""


def dashboard_payload(state, authed):
    now = datetime.now(timezone.utc)
    rows, next_up = build_races_view(DASH["races"], state, now)
    daily = build_daily_view(state, now)
    reason = edit_block_reason()
    return {"now": now.timestamp(), "tz": LOCAL_TZ.key,
            "summary": build_summary(rows, daily),
            "status": status_snapshot(),
            "states": TEST_STATES,
            "races": rows, "next_up": next_up, "daily": daily,
            "notifications": recent_notifications(5),
            "icons": icon_versions(),
            "auth": {"password_set": bool(DASHBOARD_PASSWORD), "logged_in": authed,
                     "can_edit": authed and not reason, "edit_note": reason,
                     "can_upload": authed and bool(DASHBOARD_PASSWORD)}}


def current_file_values():
    if CONFIG_LOADED:
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                return parse_env_text(f.read())
        except OSError:
            pass
    return {k: os.environ[k] for k in SETTINGS_BY_KEY if k in os.environ}


def settings_payload(can_edit):
    vals = current_file_values()
    groups = []
    for g in SETTING_GROUPS:
        items = []
        for sp in SETTINGS:
            if sp["group"] != g:
                continue
            default = "" if sp["default"] in (None, "") else str(sp["default"])
            raw = vals.get(sp["key"])
            if raw is not None and sp["type"] == "multi" and sp.get("aliases"):
                raw = ",".join(dict.fromkeys(sp["aliases"].get(x.strip().lower(), x.strip().lower())
                                             for x in raw.split(",") if x.strip()))
            item = {k: sp[k] for k in ("key", "label", "type", "help", "options", "option_labels", "min", "max",
                                       "recreate", "required") if k in sp}
            item.update({"default": default, "in_file": raw is not None,
                         "value": default if raw is None else raw})
            if sp["type"] == "secret":
                item["has_value"] = bool(raw)
                if not can_edit:
                    item["value"] = ""
            items.append(item)
        groups.append({"name": g, "items": items})
    return {"groups": groups}


def validate_value(spec, raw):
    """Returns (normalized string, error message or None)."""
    typ = spec["type"]
    v = "" if raw is None else str(raw).strip()
    if any(c in v for c in "\n\r\x00"):
        return None, "No line breaks, please."
    if "$" in v:
        return None, "The $ character isn't supported."
    if len(v) > 300:
        return None, "Too long (300 characters max)."
    if v == "":
        if spec.get("required"):
            return None, "This one is required."
        if typ == "multi" and spec.get("allow_empty") is False:
            return None, "Choose at least one."
        return "", None
    try:
        if typ in ("text", "secret"):
            return v, None
        if typ in ("int", "float"):
            n = int(v) if typ == "int" else float(v)
            if "min" in spec and n < spec["min"]:
                return None, f"Must be at least {spec['min']}."
            if "max" in spec and n > spec["max"]:
                return None, f"Must be at most {spec['max']}."
            return (str(n) if typ == "int" else f"{n:g}"), None
        if typ == "bool":
            if v.lower() in ("1", "true", "yes", "on"):
                return "1", None
            if v.lower() in ("0", "false", "no", "off"):
                return "0", None
            return None, "Must be on or off."
        if typ == "time":
            m = re.fullmatch(r"(\d{1,2}):(\d{2})", v)
            if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
                return None, "Use 24-hour HH:MM, e.g. 22:00."
            return f"{int(m.group(1)):02d}:{m.group(2)}", None
        if typ == "select":
            if v.lower() not in spec["options"]:
                return None, "Pick one of: " + ", ".join(spec["options"])
            return v.lower(), None
        if typ == "multi":
            aliases = spec.get("aliases", {})
            picked = {aliases.get(x.strip().lower(), x.strip().lower()) for x in v.split(",") if x.strip()}
            bad = picked - set(spec["options"])
            if bad:
                return None, "Unknown: " + ", ".join(sorted(bad))
            return ",".join(o for o in spec["options"] if o in picked), None
        if typ == "tz":
            ZoneInfo(v)
            return v, None
    except Exception:
        return None, "Not a valid value."
    return v, None


def validate_settings(values):
    clean, errors = {}, {}
    for key, raw in values.items():
        spec = SETTINGS_BY_KEY.get(key)
        if spec is None:
            errors[key] = "Unknown setting."
            continue
        val, err = validate_value(spec, raw)
        if err:
            errors[key] = err
        else:
            clean[key] = val
    return clean, errors


def schedule_restart(delay=1.0):
    """Exit so docker's restart policy relaunches us with the new .env."""
    def _go():
        log.info("Restarting to apply new settings")
        logging.shutdown()
        os._exit(0)
    threading.Timer(delay, _go).start()


def save_settings(values):
    """Returns (response dict, http status). Writes the .env in place, backs up the old one, restarts."""
    reason = edit_block_reason()
    if reason:
        return {"ok": False, "error": reason}, 403
    clean, errors = validate_settings(values)
    if errors:
        return {"ok": False, "errors": errors}, 400
    cur = current_file_values()
    changes = {}
    for key, val in clean.items():
        spec = SETTINGS_BY_KEY[key]
        default = "" if spec["default"] in (None, "") else str(spec["default"])
        old, _ = validate_value(spec, cur.get(key, default))
        if (old if old is not None else cur.get(key, default)) != val:
            changes[key] = val
    if not changes:
        return {"ok": True, "changed": [], "restart": False}, 200
    with open(CONFIG_FILE, encoding="utf-8") as f:
        old_text = f.read()
    new_text = update_env_text(old_text, changes)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, "env.backup"), "w", encoding="utf-8") as f:
        f.write(old_text)
    with open(CONFIG_FILE, "r+", encoding="utf-8") as f:       # in place: keeps a bind-mounted file linked
        f.seek(0)
        f.write(new_text)
        f.truncate()
    log.info("Settings saved from the dashboard: %s", ", ".join(sorted(changes)))
    schedule_restart()
    return {"ok": True, "changed": sorted(changes), "restart": True,
            "recreate_needed": sorted(k for k in changes if SETTINGS_BY_KEY[k].get("recreate"))}, 200


# -------------------------------------------------------------------- tests ----
# The checks listed on the dashboard's Tests card (the same things the --test, --preview, --daily, --icon-test,
# --polling, --probe and --list commands do). "sends" ones put a real notification on your phone.
TEST_LOCK = threading.Lock()
TEST_LAST_RUN = {}


def _t_notification(state):
    ok = ntfy_publish({"title": "NASCAR reminder test", "message": "If you see this, alerts work.", "priority": 3,
                       "tags": ["white_check_mark"], "click": CHECKIN_URL, "actions": build_actions("test")})
    return ok, ("Sent. Your phone should buzz, with the same buttons as a real alert." if ok else
                "FAILED: ntfy couldn't be reached or refused it. Check the ntfy server address and topic.")


def _preview_text(state):
    built = build_preview(DASH["races"], datetime.now(timezone.utc), state.get("preview_last"))
    return None if built is None else (built[0], built[1])


def _t_preview_show(state):
    built = _preview_text(state)
    return True, ("Nothing to preview (no races in the next three weeks)." if built is None
                  else f"[{built[0]}]\n{built[1]}")


def _t_preview_send(state):
    built = _preview_text(state)
    if built is None:
        return True, "Nothing to send (no races in the next three weeks)."
    ok = ntfy_publish({"title": built[0], "message": built[1], "priority": 3, "tags": ["checkered_flag"]})
    return ok, (f"Sent: [{built[0]}]\n{built[1]}" if ok else "FAILED: ntfy couldn't be reached.")


def _t_daily_show(state):
    lines = []
    for i, d in enumerate(DAY_NAMES):
        built = build_daily(i)
        lines.append(f"{d}: " + ("(no reminder: only-on-game-days is on)" if built is None else f"[{built[0]}] {built[1]}"))
    return True, "\n".join(lines)


def _t_daily_send(state):
    today = datetime.now(LOCAL_TZ)
    built = build_daily(today.weekday())
    if built is None:
        return True, "Today has no reminder (only-on-game-days is on and no game is set for today)."
    ok = ntfy_publish({"title": built[0], "message": built[1], "priority": 3, "tags": ["video_game"],
                       "click": DASHBOARD_URL,
                       "actions": build_actions(None, DASHBOARD_URL, "Open dashboard", daily_date=today.date().isoformat())})
    return ok, (f"Sent: [{built[0]}] {built[1]}" if ok else "FAILED: ntfy couldn't be reached.")


def _t_icon_test(state):
    step = 20
    now_ts = time.time()
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(icon_test_path(), "w") as f:
        json.dump({"start": now_ts, "step": step, "until": now_ts + step * len(TEST_STATES)}, f)
    return True, (f"Started. The key will show {' -> '.join(TEST_STATES)}, {step} s each ({step * len(TEST_STATES)} s total). "
                  "Watch the Stream Deck card here and your real key. Nothing is sent to your phone.")


def _t_polling(state):
    now = datetime.now(timezone.utc)
    ok, reason = poll_decision(DASH["races"], state, now)
    return True, (f"Now ({QUIET_TZ.key}): {now.astimezone(QUIET_TZ).strftime('%a %I:%M %p')}\n"
                  f"Quiet days: {'/'.join(sorted(QUIET_DAYS)) or 'none'} | quiet hours {QUIET_START_HOUR:02d}:00-{QUIET_END_HOUR:02d}:00\n"
                  f"Polling nascar.com right now: {'YES' if ok else 'NO'} ({reason})")


def _t_races(state):
    now = datetime.now(timezone.utc)
    up = sorted((r for r in DASH["races"] if r["series"].lower() in ENABLED
                 and r["start"] + timedelta(hours=WINDOW_HOURS) > now), key=lambda r: r["start"])
    if not up:
        return True, "No upcoming races in the saved schedule."
    return True, f"{len(up)} upcoming/live races (times in {LOCAL_TZ.key}); first 15:\n" + "\n".join(
        f"{series_name(r['series']):8} {fmt_when(r['start']):22} {r['name']}" for r in up[:15])


def _t_live_probe(state):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cmd_probe()
    out = buf.getvalue().strip()
    return "FAILED" not in out, out


def _t_schedule_probe(state):
    year = datetime.now(LOCAL_TZ).year
    try:
        data = fetch_feed(year)
    except Exception as e:
        return False, f"Download failed: {str(e)[:200]}"
    races = parse_feed(data, year)
    per = {}
    for r in races:
        per[r["series"]] = per.get(r["series"], 0) + 1
    with_track = sum(1 for r in races if r.get("track"))
    known = {}
    for r in DASH["races"]:
        known[r["series"]] = known.get(r["series"], 0) + 1
    lines = [f"Downloaded the {year} schedule feed: {len(races)} races",
             "Per series: " + (", ".join(f"{series_name(k)} {v}" for k, v in sorted(per.items())) or "none"),
             f"Track names present: {with_track} of {len(races)}",
             f"Races the app has saved: {len(DASH['races'])}"]
    missing = [series_name(k) for k in SERIES_LABELS.values() if k.lower() in ENABLED and per.get(k, 0) == 0]
    ok = bool(races) and not missing
    if missing:
        lines.append("WARNING: the feed has no races for " + ", ".join(missing)
                     + ". NASCAR sometimes sends partial data; the app keeps its saved copy.")
    return ok, "\n".join(lines)


TESTS = [
    {"id": "notification", "name": "Send a test notification", "sends": True, "fn": _t_notification,
     "desc": "Sends a real notification with the same buttons as an alert. Confirms your phone, the ntfy topic and the buttons."},
    {"id": "preview_show", "name": "Show the weekend preview", "sends": False, "fn": _t_preview_show,
     "desc": "Shows what the Thursday race-weekend notification would say. Nothing is sent."},
    {"id": "preview_send", "name": "Send the weekend preview now", "sends": True, "fn": _t_preview_send,
     "desc": "Sends the weekend preview to your phone right now (the weekly one still goes out on its own)."},
    {"id": "daily_show", "name": "Show the daily reminders", "sends": False, "fn": _t_daily_show,
     "desc": "Shows each weekday's reminder text, including the game names from your settings. Nothing is sent."},
    {"id": "daily_send", "name": "Send today's daily reminder now", "sends": True, "fn": _t_daily_send,
     "desc": "Sends today's dashboard-games reminder to your phone, with its buttons."},
    {"id": "icon_test", "name": "Run the key icon test", "sends": False, "fn": _t_icon_test,
     "desc": "Cycles the Stream Deck status through green, yellow, red, hot and offline. Watch the key card here and your real key."},
    {"id": "polling", "name": "Check polling status", "sends": False, "fn": _t_polling,
     "desc": "Shows whether the app may contact nascar.com right now (quiet days and hours), and why."},
    {"id": "races", "name": "List upcoming races", "sends": False, "fn": _t_races,
     "desc": "The races the app will alert for, from its saved schedule, in your time zone."},
    {"id": "live_probe", "name": "Check the live feed", "sends": False, "fn": _t_live_probe, "network": True,
     "desc": "Reads NASCAR's live feed now and shows how the app reads it: flag, series, and whether it counts as live."},
    {"id": "schedule_probe", "name": "Check the schedule feed", "sends": False, "fn": _t_schedule_probe, "network": True,
     "desc": "Downloads NASCAR's schedule feed and counts races per series, to spot partial or empty responses."},
]
TESTS_BY_ID = {t["id"]: t for t in TESTS}


def run_test(test_id, state):
    """Returns (ok, output). One test at a time; output is plain text."""
    t = TESTS_BY_ID[test_id]
    with TEST_LOCK:
        try:
            ok, out = t["fn"](state)
        except Exception as e:
            log.exception("test %s failed", test_id)
            ok, out = False, f"The test crashed: {e}"
    return bool(ok), str(out)


# ------------------------------------------------------------------ http ----
class Handler(BaseHTTPRequestHandler):
    state = None
    MAX_BODY = 65536

    def log_message(self, *a):
        pass

    # ---- helpers
    def _send(self, code, body, ctype="text/plain", headers=None):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if "Cache-Control" not in (headers or {}):
            self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj, headers=None):
        self._send(code, json.dumps(obj), "application/json", headers)

    def _token(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "nc_session":
                return v
        return ""

    def _authed(self):
        tok = self._token()
        exp = SESSIONS.get(tok)
        if exp and exp > time.time():
            return True
        SESSIONS.pop(tok, None)
        return False

    def _can_view(self):
        return (not DASHBOARD_PASSWORD) or self._authed()

    def _body(self):
        """Parsed JSON body of a POST. Needs the custom header, which a cross-site form can't send."""
        if self.headers.get("X-Requested-With") != "nc-dashboard":
            raise PermissionError("missing header")
        n = int(self.headers.get("Content-Length") or 0)
        if n > self.MAX_BODY:
            raise ValueError("body too large")
        raw = self.rfile.read(n) if n else b"{}"
        data = json.loads(raw.decode("utf-8") or "{}")
        if not isinstance(data, dict):
            raise ValueError("expected an object")
        return data

    # ---- routing
    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def _route(self, method):
        try:
            self._dispatch(method)
        except PermissionError:
            self._json(403, {"error": "forbidden"})
        except (ValueError, json.JSONDecodeError):
            self._json(400, {"error": "bad request"})
        except Exception:
            log.exception("request failed: %s %s", method, self.path)
            self._json(500, {"error": "internal error"})

    def _dispatch(self, method):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/health":
            return self._send(200, "ok")
        if path == "/status":
            return self._json(200, status_snapshot())
        if path == "/status/text":
            return self._send(200, "LIVE" if status_snapshot()["live"] else "IDLE")
        if path == "/icon.png":
            try:
                with open(ICON_PATH, "rb") as f:
                    return self._send(200, f.read(), "image/png")
            except OSError:
                return self._send(404, "no icon")
        if path == "/banner.jpg":
            try:
                with open(BANNER_PATH, "rb") as f:
                    return self._send(200, f.read(), "image/jpeg", {"Cache-Control": "public, max-age=86400"})
            except OSError:
                return self._send(404, "no banner")
        m = re.fullmatch(r"/icons/([a-z]+)", path)
        if m and method == "GET":
            ci = custom_icon(m.group(1)) if m.group(1) in TEST_STATES else None
            if not ci:
                return self._send(404, "no custom icon")
            with open(ci[0], "rb") as f:
                return self._send(200, f.read(), ci[1], {"Cache-Control": "public, max-age=3600"})
        if path in ("/", "/dashboard") and method == "GET":
            try:
                with open(DASHBOARD_HTML, "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError:
                return self._send(404, "dashboard.html is missing from the image")
        if path.startswith("/api/"):
            return self._api(method, path)
        parts = path.split("/")
        if len(parts) == 3 and parts[1] == "ack":
            if not ACK_TOKEN or urllib.parse.parse_qs(parsed.query).get("t", [""])[0] != ACK_TOKEN:
                return self._send(403, "forbidden")
            rid = urllib.parse.unquote(parts[2])
            with STATE_LOCK:
                if rid not in self.state["acked"]:
                    self.state["acked"].append(rid)
                    save_state(self.state)
            log.info("Acked %s", rid)
            mark_event_done(self.state, rid)
            return self._send(200, "ok")
        if len(parts) == 3 and parts[1] == "ackdaily":
            if not ACK_TOKEN or urllib.parse.parse_qs(parsed.query).get("t", [""])[0] != ACK_TOKEN:
                return self._send(403, "forbidden")
            day = urllib.parse.unquote(parts[2])
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                return self._send(400, "bad date")
            set_done(self.state, f"daily:{day}", True)
            log.info("Daily activity for %s marked done", day)
            return self._send(200, "ok")
        self._send(404, "not found")

    def _api(self, method, path):
        if path == "/api/login" and method == "POST":
            return self._login()
        if path == "/api/logout" and method == "POST":
            self._body()
            SESSIONS.pop(self._token(), None)
            return self._json(200, {"ok": True}, {"Set-Cookie": "nc_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"})
        if not self._can_view():
            return self._json(401, {"error": "login required", "password_set": True})
        if path == "/api/dashboard" and method == "GET":
            return self._json(200, dashboard_payload(self.state, self._authed()))
        if path == "/api/settings" and method == "GET":
            can_edit = self._authed() and not edit_block_reason()
            out = settings_payload(can_edit)
            out.update({"can_edit": can_edit, "edit_note": edit_block_reason(),
                        "logged_in": self._authed(), "password_set": bool(DASHBOARD_PASSWORD)})
            return self._json(200, out)
        if path == "/api/settings" and method == "POST":
            body = self._body()
            if not self._authed():
                return self._json(403, {"ok": False, "error": "Log in to change settings."})
            values = body.get("values")
            if not isinstance(values, dict):
                raise ValueError("values missing")
            resp, code = save_settings(values)
            return self._json(code, resp)
        if path == "/api/done" and method == "POST":
            return self._done(self._body())
        if path == "/api/tests" and method == "GET":
            return self._json(200, {"tests": [{"id": t["id"], "name": t["name"], "desc": t["desc"],
                                               "sends": t["sends"], "network": bool(t.get("network"))} for t in TESTS],
                                    "can_send": bool(DASHBOARD_PASSWORD and self._authed()),
                                    "password_set": bool(DASHBOARD_PASSWORD)})
        if path == "/api/tests/run" and method == "POST":
            return self._run_test(self._body())
        m = re.fullmatch(r"/api/icon/([a-z]+)(/reset)?", path)
        if m and method == "POST":
            return self._icon(m.group(1), bool(m.group(2)))
        self._json(404, {"error": "not found"})

    def _run_test(self, body):
        tid = str(body.get("id", ""))
        t = TESTS_BY_ID.get(tid)
        if t is None:
            return self._json(404, {"ok": False, "error": "Unknown test."})
        if t["sends"] and not (DASHBOARD_PASSWORD and self._authed()):
            return self._json(403, {"ok": False, "error": "Log in with the dashboard password to run tests that send "
                                                           "notifications (set DASHBOARD_PASSWORD in .env if you haven't)."})
        now = time.time()
        if now - TEST_LAST_RUN.get(tid, 0) < 2:
            return self._json(429, {"ok": False, "error": "Easy: that test just ran."})
        TEST_LAST_RUN[tid] = now
        ok, out = run_test(tid, self.state)
        return self._json(200, {"ok": ok, "id": tid, "output": out, "ran_at": time.time()})

    def _icon(self, state, reset):
        if self.headers.get("X-Requested-With") != "nc-dashboard":
            raise PermissionError("missing header")
        if state not in TEST_STATES:
            return self._json(404, {"ok": False, "error": "Unknown state."})
        if not (DASHBOARD_PASSWORD and self._authed()):
            return self._json(403, {"ok": False, "error": "Set a dashboard password and log in to change icons."})
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_ICON_BYTES:
            self.rfile.read(min(n, 1 << 20))
            return self._json(413, {"ok": False, "error": "That image is over 512 KB."})
        data = self.rfile.read(n) if n else b""
        if reset:
            reset_custom_icon(state)
            return self._json(200, {"ok": True})
        try:
            save_custom_icon(state, data)
        except ValueError as e:
            return self._json(400, {"ok": False, "error": str(e)})
        return self._json(200, {"ok": True})

    def _login(self):
        body = self._body()
        if not DASHBOARD_PASSWORD:
            return self._json(400, {"ok": False, "error": "No password is set."})
        ip = self.client_address[0]
        now = time.time()
        fails = [t for t in LOGIN_FAILS.get(ip, []) if now - t < 300]
        if len(fails) >= 5:
            LOGIN_FAILS[ip] = fails
            return self._json(429, {"ok": False, "error": "Too many attempts. Try again in a few minutes."})
        given = str(body.get("password", "")).encode()
        if not hmac.compare_digest(given, DASHBOARD_PASSWORD.encode()):
            LOGIN_FAILS[ip] = fails + [now]
            return self._json(401, {"ok": False, "error": "Wrong password."})
        LOGIN_FAILS.pop(ip, None)
        for t in [t for t, e in SESSIONS.items() if e <= now]:
            del SESSIONS[t]
        tok = secrets.token_hex(24)
        SESSIONS[tok] = now + SESSION_SECONDS
        cookie = f"nc_session={tok}; Path=/; Max-Age={SESSION_SECONDS}; HttpOnly; SameSite=Strict"
        return self._json(200, {"ok": True}, {"Set-Cookie": cookie})

    def _done(self, body):
        key, done = str(body.get("key", "")), bool(body.get("done", True))
        now = datetime.now(timezone.utc)
        if key.startswith("race:"):
            rows, _ = build_races_view(DASH["races"], self.state, now)
            row = next((r for r in rows if r["key"] == key), None)
            if row is None:
                return self._json(404, {"ok": False, "error": "That race isn't on this week's list."})
            if done and not row["can_mark"]:
                return self._json(400, {"ok": False, "error": "That race hasn't started yet."})
        elif key.startswith("daily:"):
            row = next((d for d in build_daily_view(self.state, now) if d["key"] == key), None)
            if row is None:
                return self._json(404, {"ok": False, "error": "That activity isn't on this week's list."})
            if not row["can_mark"]:
                return self._json(400, {"ok": False, "error": "Only today's activity can be changed."})
        else:
            return self._json(400, {"ok": False, "error": "Unknown item."})
        set_done(self.state, key, done)
        return self._json(200, {"ok": True, "key": key, "done": done})


# --------------------------------------------------------------- service ----
def run_service():
    if not NTFY_TOPIC:
        sys.exit("Set NTFY_TOPIC (a long random topic name) and try again.")
    state = load_state()
    if HTTP_PORT:
        Handler.state = state
        srv = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        log.info("HTTP on :%d (dashboard at /, /status, /icon.png%s)", HTTP_PORT, ", /ack" if ACK_TOKEN else "")
    races, last_refresh, last_live = [], 0.0, 0.0
    log.info("Started. mode=%s series=%s quiet=%s %02d:00-%02d:00 %s", MODE, ",".join(sorted(ENABLED)),
             "/".join(sorted(QUIET_DAYS)), QUIET_START_HOUR, QUIET_END_HOUR, QUIET_TZ.key)
    while True:
        now_ts = time.time()
        now = datetime.now(timezone.utc)
        may_poll, reason = poll_decision(races, state, now)
        if reason != RT["poll_state"]:
            log.info("Polling %s (%s)", "ON" if may_poll else "OFF", reason)
            RT["poll_state"] = reason
        # schedule refresh: always once at startup, then only when polling is allowed;
        # retry a failed/empty load at most every 5 minutes
        # Normal refresh only when polling is allowed. With NO schedule loaded we keep retrying even
        # during quiet hours (slowly), otherwise a failed startup load would leave us blind until Friday.
        if races:
            every, allowed = REFRESH_MIN * 60, may_poll
        else:
            every, allowed = (EMPTY_RETRY_SEC if may_poll else EMPTY_RETRY_QUIET_SEC), True
        if last_refresh == 0.0 or (allowed and now_ts - last_refresh > every):
            races, src = load_races(races)
            DASH["races"] = races
            last_refresh = now_ts
            log.info("Loaded %d races from %s", len(races), src)
            if races:
                check_reschedules(races, state, now)
            elif now_ts - state["last_error_alert"] > 86400:
                ntfy_publish({"title": "NASCAR reminder: no schedule",
                              "message": ("Couldn't load the race schedule from NASCAR: "
                                          + (RT["schedule_error"] or "unknown reason")
                                          + ". It keeps retrying. (A races.json in the data folder is an optional manual override.)"),
                              "priority": 3, "tags": ["warning"]})
                with STATE_LOCK:
                    state["last_error_alert"] = now_ts
                    save_state(state)
        try:
            if may_poll and MODE in ("live", "auto") and now_ts - last_live >= LIVE_POLL_SEC:
                poll_live(state, now_ts)
                last_live = now_ts
            expire_live(now_ts)
            schedule_tick(races, state, now)
            process_events(state, now_ts)
            maybe_send_preview(races, state, now)
            maybe_send_daily(state, now)
            prune(state, now_ts)
        except Exception:
            log.exception("loop failed")
        time.sleep(10)


def cmd_list():
    races, src = load_races()
    now = datetime.now(timezone.utc)
    upcoming = sorted((r for r in races if r["series"].lower() in ENABLED
                       and r["start"] + timedelta(hours=WINDOW_HOURS) > now), key=lambda r: r["start"])
    print(f"Source: {src} | mode: {MODE} | {len(upcoming)} upcoming/live | times in {LOCAL_TZ.key}")
    for r in upcoming[:25]:
        print(f'{series_name(r["series"]):8} {fmt_local(r["start"]):22} {r["name"]}')


def cmd_probe():
    print("Live feed URL:", LIVE_FEED_URL)
    try:
        data = fetch_json(LIVE_FEED_URL)
    except Exception as e:
        print("FAILED to fetch:", e)
        print("In auto mode the app falls back to schedule alerts. Try LIVE_FEED_URL=... if you find another feed.")
        return
    print("Top level:", type(data).__name__,
          (sorted(data.keys())[:40] if isinstance(data, dict) else f"{len(data)} items"))
    print("Raw (first 500 chars):", json.dumps(data)[:500])
    healthy, entries = interpret_live(data)
    print("Recognised flag_state:", healthy)
    for e in entries:
        why = "" if e["supported"] else " (not a Cup/O'Reilly/Truck race: ignored)"
        print(" ", e, "=> LIVE" if entry_is_live(e) else "=> not live" + why)
    print("Flags: 1 green, 2 yellow, 3 red, 4 white, 5 checkered, 8 hot track, 9 cold track. run_type 3 = race.")


def cmd_polling():
    races, _ = load_races()
    state = load_state()
    now = datetime.now(timezone.utc)
    ok, reason = poll_decision(races, state, now)
    print(f"Now ({QUIET_TZ.key}): {now.astimezone(QUIET_TZ).strftime('%a %I:%M %p')}")
    print(f"Quiet days: {'/'.join(sorted(QUIET_DAYS))} | quiet hours {QUIET_START_HOUR:02d}:00-{QUIET_END_HOUR:02d}:00")
    print(f"Polling nascar.com right now: {'YES' if ok else 'NO'} ({reason})")


def cmd_icon_test(step):
    """Cycles /status through green, yellow, red, hot, offline (`step` seconds each). No alerts are sent."""
    if not HTTP_PORT:
        sys.exit("HTTP_PORT is 0, so /status is disabled. Set HTTP_PORT=8099 and recreate the container.")
    url = f"http://127.0.0.1:{HTTP_PORT}/status"

    def peek():
        try:
            d = fetch_json(url)
            return f'state={d.get("state")} live={d.get("live")}'
        except Exception as e:
            return f"unreachable ({e})"

    total = step * len(TEST_STATES)
    print(f"Before: {peek()}")
    os.makedirs(DATA_DIR, exist_ok=True)
    start = time.time()
    with open(icon_test_path(), "w") as f:
        json.dump({"start": start, "step": step, "until": start + total}, f)
    print(f"Icon test: {' -> '.join(TEST_STATES)}, {step}s each ({total}s total). Ctrl-C ends it early.")
    print("Keep each step longer than your Stream Deck poll interval or the key may skip a state.")
    try:
        shown = None
        while time.time() < start + total:
            st = icon_test_state(time.time())
            if st and st != shown:
                shown = st
                print(f"  now showing: {st}")
            time.sleep(1)
    except KeyboardInterrupt:
        print("Stopped early.")
    finally:
        try:
            os.remove(icon_test_path())
        except OSError:
            pass
    print(f"After: {peek()}")


def cmd_preview(send):
    races, src = load_races()
    state = load_state()
    built = build_preview(races, datetime.now(timezone.utc), state.get("preview_last"))
    if built is None:
        print("Nothing to preview (no races in the next three weeks).")
        return
    title, body, idx = built
    print(f"[{title}]\n{body}")
    if send:
        ok = ntfy_publish({"title": title, "message": body, "priority": 3, "tags": ["checkered_flag"]})
        print("\nsent" if ok else "\nFAILED (see log)")


def cmd_daily(send):
    """--daily-print [day] shows a day's reminder; --daily [day] also sends it. day = mon..sun (default today)."""
    flag = "--daily" if send else "--daily-print"
    i = sys.argv.index(flag)
    arg = sys.argv[i + 1][:3].lower() if len(sys.argv) > i + 1 else ""
    idx = _DAYS.index(arg) if arg in _DAYS else datetime.now(LOCAL_TZ).weekday()
    built = build_daily(idx)
    if built is None:
        print(f"No reminder on {_DAYS[idx]} (DAILY_ONLY_CONFIGURED=1 and no game set for that day).")
        return
    title, body = built
    print(f"[{_DAYS[idx]}] {title}\n{body}")
    if send:
        ok = ntfy_publish({"title": title, "message": body, "priority": 3, "tags": ["video_game"],
                           "click": DASHBOARD_URL, "actions": build_actions(None, DASHBOARD_URL, "Open dashboard",
                                                                    daily_date=datetime.now(LOCAL_TZ).date().isoformat())})
        print("\nsent" if ok else "\nFAILED (see log)")


def cmd_test():
    ok = ntfy_publish({"title": "NASCAR reminder test", "message": "If you see this, alerts work.",
                       "priority": 3, "tags": ["white_check_mark"], "click": CHECKIN_URL,
                       "actions": build_actions("test")})
    print("sent" if ok else "FAILED (see log)")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if "--list" in sys.argv:
        cmd_list()
    elif "--icon-test" in sys.argv:
        i = sys.argv.index("--icon-test")
        secs = int(sys.argv[i + 1]) if len(sys.argv) > i + 1 and sys.argv[i + 1].isdigit() else 20
        cmd_icon_test(secs)
    elif "--polling" in sys.argv:
        cmd_polling()
    elif "--daily-print" in sys.argv:
        cmd_daily(False)
    elif "--daily" in sys.argv:
        cmd_daily(True)
    elif "--preview-print" in sys.argv:
        cmd_preview(False)
    elif "--preview" in sys.argv:
        cmd_preview(True)
    elif "--probe" in sys.argv:
        cmd_probe()
    elif "--test" in sys.argv:
        cmd_test()
    else:
        run_service()
