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

Also serves a tiny HTTP endpoint (/status) for a Stream Deck key, and /icon.png.

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
import json
import logging
import os
import random
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


def get_tz(name, default):
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(default)


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
ENABLED = {s.strip().lower() for s in os.environ.get("SERIES", "cup,xfinity,truck").split(",") if s.strip()}

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
DAILY_GAMES = {d: os.environ.get(f"GAME_{d.upper()}", "").strip() for d in _DAYS}

DATA_DIR = os.environ.get("DATA_DIR", "/data")
HTTP_PORT = env_int("HTTP_PORT", env_int("ACK_PORT", 8099))   # 0 disables the HTTP server
ACK_BASE_URL = os.environ.get("ACK_BASE_URL", "").rstrip("/")
ACK_TOKEN = os.environ.get("ACK_TOKEN", "").strip()
ICON_PATH = os.environ.get("ICON_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png"))

SERIES_KEYS = (("series_1", "Cup"), ("series_2", "Xfinity"), ("series_3", "Truck"))
SERIES_BY_ID = {1: "Cup", 2: "Xfinity", 3: "Truck"}
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
    for k, v in (("sent", {}), ("acked", []), ("events", {}), ("starts", {}), ("last_error_alert", 0)):
        s.setdefault(k, v)
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
            "id": r.get("id") or f'{r["series"]}-{r.get("name", "race")}',
            "series": r["series"],
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
            return [{"id": r["id"], "series": r["series"], "name": r["name"], "track": r.get("track", ""),
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
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 300
    except Exception as e:
        log.warning("ntfy publish failed: %s", e)
        return False


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
            # only Cup/Xfinity/Truck have check-in; other series (ARCA etc.) are ignored
            "supported": sid in SERIES_BY_ID and not any(
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
                     "" if e0["supported"] else " - IGNORED (not a Cup/Xfinity/Truck race)")
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
                    LIVE[label] = {"source": "feed", "since": now_ts, "last_seen": now_ts}
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
        state["events"][eid] = {"series": label, "name": e["name"] or f"{label} race",
                                "first": now_ts, "source": "feed"}
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
        live = {"Cup", "Xfinity", "Truck"} if state in ("green", "yellow", "red") else set()
    out = {"live": bool(live), "state": state}
    for s in ("Cup", "Xfinity", "Truck"):
        out[s.lower()] = s in live
    fresh = bool(RT["live_ok_ts"]) and now_ts - RT["live_ok_ts"] <= STATE_FRESH_SEC
    out.update({"flag": RT["flag"] if fresh else None, "series": RT["flag_series"] if fresh else None,
                "test": testing, "mode": MODE, "polling": RT["poll_state"],
                "live_feed_ok": bool(live_healthy(now_ts)),
                "updated": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    return out


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
def build_actions(eid=None, url=None, plain_label="Open leaderboard"):
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
    return actions


def send_alert(eid, ev, stage):
    first = stage == 0
    series, name = ev["series"], ev["name"]
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
                "sched": r["start"].timestamp(), "source": "fallback" if healthy else "schedule"}
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
                ntfy_publish({"title": f'{r["series"]} race moved',
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
SERIES_ORDER = ["Truck", "Xfinity", "Cup"]
SERIES_PLURAL = {"Truck": "Trucks", "Xfinity": "Xfinity", "Cup": "Cup"}


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
                f"Next up: {nxt['series']} {nxt['name']} on {fmt_when(nxt['start'])}.", None)
    options = [i for i in range(len(PREVIEW_VARIATIONS)) if i != last_idx]
    idx = rng.choice(options)
    title, intro = PREVIEW_VARIATIONS[idx]
    present = [s for s in SERIES_ORDER if any(r["series"] == s for r in week)]
    present += sorted({r["series"] for r in week} - set(SERIES_ORDER))
    lines = [intro.format(series=join_words(SERIES_PLURAL.get(s, s) for s in present)), ""]
    for r in week:
        place = f" at {r['track']}" if r.get("track") else ""
        lines.append(f"\u2022 {fmt_when(r['start'])} \u2013 {r['series']}: {r['name']}{place}")
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
                             "actions": build_actions(None, DASHBOARD_URL, "Open dashboard")}):
            return                      # try again on the next tick
        log.info("Daily reminder sent (%s)", title)
    with STATE_LOCK:
        state["daily_date"] = today
        save_state(state)


# ------------------------------------------------------------------ http ----
class Handler(BaseHTTPRequestHandler):
    state = None

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/plain"):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _handle(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/health":
            return self._send(200, "ok")
        if path == "/status":
            return self._send(200, json.dumps(status_snapshot()), "application/json")
        if path == "/status/text":
            return self._send(200, "LIVE" if status_snapshot()["live"] else "IDLE")
        if path == "/icon.png":
            try:
                with open(ICON_PATH, "rb") as f:
                    return self._send(200, f.read(), "image/png")
            except OSError:
                return self._send(404, "no icon")
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
            return self._send(200, "ok")
        self._send(404, "not found")

    do_GET = _handle
    do_POST = _handle


# --------------------------------------------------------------- service ----
def run_service():
    if not NTFY_TOPIC:
        sys.exit("Set NTFY_TOPIC (a long random topic name) and try again.")
    state = load_state()
    if HTTP_PORT:
        Handler.state = state
        srv = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        log.info("HTTP on :%d (/status, /icon.png%s)", HTTP_PORT, ", /ack" if ACK_TOKEN else "")
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
        print(f'{r["series"]:8} {fmt_local(r["start"]):22} {r["name"]}')


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
        why = "" if e["supported"] else " (not a Cup/Xfinity/Truck race: ignored)"
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
                           "click": DASHBOARD_URL, "actions": build_actions(None, DASHBOARD_URL, "Open dashboard")})
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