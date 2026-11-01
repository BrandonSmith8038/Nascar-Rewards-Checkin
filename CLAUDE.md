# NASCAR Check-in Reminder

Self-hosted service that helps the owner earn NASCAR Fan Rewards points. It watches NASCAR's public feeds and sends an ntfy push when a Cup, O'Reilly or Truck race goes green, so the owner can press Check In by hand. It also serves a dashboard and a `/status` endpoint that drives a Stream Deck key. User-facing docs are in README.md (keep one README; don't duplicate docs elsewhere).

## Hard rule: reminders only
NASCAR's Fan Rewards Terms of Use prohibit bots/automation for earning points. **Never add anything that logs in to nascar.com or clicks Check In.** The project was deliberately built as reminders only.

## Stack
- `app.py`: the whole service, Python 3.12, **stdlib only** (plus `tzdata` in the image). Section-marked with `# ----- name -----` banners. Keep it single-file, no web framework, no database.
- `dashboard.html`: one static file, vanilla JS/CSS, no build step. Baked into the image.
- `streamdeck-plugin/`: Stream Deck plugin "API Request Multi" (derived from mjbnz/sd-api-request, Apache-2.0; keep LICENCE + NOTICE). Polls `/status`, reads the `state` field, swaps key image. Build by zipping the `.sdPlugin` folder and renaming to `.streamDeckPlugin`.
- `dev-tests/`: Python + jsdom test harness. Needs Node 18+, Pillow, and a Linux-style shell (Git Bash/WSL). Run: `cd dev-tests && npm install jsdom && pip install Pillow && REPO="$(cd .. && pwd)" ./run_all.sh`. Expects 10 PASS lines; all 10 passed on 2026-11-01. On Windows the scripts need workarounds: a `python3` shim (the Store stub fails), `pip install tzdata`, and `/tmp/dt` rewritten to one shared folder like `C:/tmp/dt` (Git Bash and Windows Python see `/tmp` differently); do this in a scratch copy so the repo stays clean. Not yet covered: poll_live flag sequences, poll_decision quiet hours, schedule_tick fallback/hot-hold, schedule merge with partial feeds, preview gating, daily reminder gating/skip, DAILY_UNLOCK_TIME gate, .env writer round trips, schema-vs-constant default equality.
- Port 8099 (`HTTP_PORT`). Dashboard is LAN/Tailscale only.

## Runtime overview
- `run_service()`: ThreadingHTTPServer in a daemon thread, plus a 10 s main loop: `poll_decision` (may we contact nascar.com?), schedule reload, `poll_live` every 60 s, `expire_live`, `schedule_tick`, `process_events`, `maybe_send_preview`, `maybe_send_daily`, `prune`.
- Locks (`STATE_LOCK`, `LIVE_LOCK`, `HISTORY_LOCK`, `TEST_LOCK`) are plain, **non-reentrant**. Don't take `STATE_LOCK` while holding it (`set_done` takes it and calls `save_state` inside).
- **Alerts** (`ALERT_MODE=auto`): the live feed (`cf.nascar.com/live/feeds/live-feed.json`) is the source of truth. `series_id` 1/2/3 = Cup/O'Reilly/Truck; anything else (ARCA etc.) or an `EXCLUDE_NAMES` match is ignored. Live = `flag_state` in {1,2,3,4} and `run_type` 3. Race over = flag 5 or 9, or not live for `END_GRACE_MIN`. First live sighting creates event `live:<Series>:<race_id>`: alert immediately, then `NUDGES` follow-ups every `NUDGE_MIN` until acked. The schedule is only a safety net: "race may not have started" notice after `SCHED_FALLBACK_MIN` (90, since schedule times are TV start ~30 min before green), held back while hot track (flag 8) seen < `HOT_GRACE_MIN`.
- **Schedule**: `cf.nascar.com/cacher/{year}/race_list_basic.json`. The feed sometimes returns partial/empty data, so results are **merged** into `/data/schedule_cache.json` and a bad download never shrinks the schedule. `/data/races.json` overrides the feed entirely. Feed timestamps are naive, interpreted in `FEED_TZ`.
- **Quiet hours**: `poll_decision` is the only gate for contacting nascar.com on a schedule (default: no Mon-Thu, none 22:00-06:00 Eastern, with overrides for live/near-race/postponed races).
- **`/status`** (unauthenticated on purpose): `{live, state: green|yellow|red|hot|offline, cup, oreilly, truck, xfinity (legacy alias), flag, series, test, mode, polling, live_feed_ok, updated}`.
- **Dashboard checklist** (`state["done"]`): keys `race:<Series>:<ident>` and `daily:<YYYY-MM-DD>`. Server-enforced: a race can be ticked only after its start; a daily game only on its own day and only from `DAILY_UNLOCK_TIME` (08:00 local). Race week = Mon-Sun in `TZ`. `build_daily_view` computes row state (`today` / `upcoming` / `past`) and the dashboard buttons follow it: today = muted `.btn.todo` "Mark done", past unticked = disabled `.btn.missed`, upcoming = disabled "Not today", ticked = `.btn.done`. Daily rows link to `DASHBOARD_URL` (blank = `CHECKIN_URL`) via a stretched link; `.actions` sits above it (z-index 1).
- **Daily reminder** at `DAILY_REMINDER_TIME` (22:00): one per local date (`state["daily_date"]`); **skipped if `daily:<today>` is already done**. **Weekend preview** Thursday 18:00: once per ISO week, 10 rotating openings, never the same twice in a row, built from the saved schedule (no NASCAR request).
- **Settings editor**: `.env` is the single source of truth, bind-mounted at `/config/.env`. `apply_config_file()` runs at import and overwrites `os.environ`. Dashboard save: validate, `update_env_text` (keeps comments), back up to `/data/env.backup`, write **in place** (`r+`, truncate, keeps the bind-mounted inode), then `schedule_restart()` (`os._exit(0)`; relies on `restart: unless-stopped`). `HTTP_PORT` and `ICON_URL` are read by compose, so changing them needs `docker compose up -d --force-recreate`.
- **Data** (all under `/data`, host `/mnt/user/appdata/nascar-checkin`): `state.json`, `schedule_cache.json`, `history.jsonl`, `icons/`, `env.backup`, `icon_test.json`, `missed_test.json`, `races.json`. In memory only: `LIVE`, `RT`, `SESSIONS` (12 h), `LOGIN_FAILS`, `DASH["races"]`.
- **HTTP API**: public GET `/health /status /status/text /icon.png /banner.jpg /icons/<state>` and the page itself. Token routes `/ack/<event id>` and `/ackdaily/<date>` need `?t=<ACK_TOKEN>`. `/api/*` needs a session cookie only when `DASHBOARD_PASSWORD` is set (blank = no login, everything open on the LAN); **every POST needs header `X-Requested-With: nc-dashboard`**. With a password set, editing, icon upload and send-type tests need login.
- **CLI**: `docker exec nascar-checkin python app.py <flag>`: `--list --probe --polling --test --preview[-print] --daily[-print] [day] --icon-test [secs]`. `--list`, `--polling`, `--preview*` and `--probe` can contact NASCAR; `--test`, `--preview`, `--daily` send to ntfy.

## Rules when modifying
- **Settings**: add to the `SETTINGS` list (type/default/limits), read it in the config section *after* `apply_config_file()`, add to `.env.example` and the README tuning table. Defaults live in two places (schema and config constants) and must agree; verify by running with an empty environment.
- **NASCAR traffic**: never add a new scheduled NASCAR request outside `poll_decision`; user-initiated tests/CLI are the only exceptions. NASCAR feeds are undocumented: treat every field as optional and tolerate partial/empty responses. Keep `FEED_URL`/`LIVE_FEED_URL` configurable.
- **Dockerfile**: anything read at runtime must be `COPY`'d (`app.py dashboard.html icon.png banner.jpg`). `.dockerignore` excludes `*.md`, `streamdeck/`, `streamdeck-plugin/`.
- **`/status` compatibility**: keep field names, state values and the `xfinity` alias (the Stream Deck plugin depends on it).
- **Security**: every POST keeps the `X-Requested-With` check; new write endpoints sit behind `_authed()` (always true when no password is set). Dashboard JS builds DOM with `h()`/`textContent`, never `innerHTML` with data (race names and notification text are external). Only accept `http(s)://` URLs from server data. Never log secrets. `.env`, `/data`, tokens and the ntfy topic stay out of git.
- **`.env` writing**: in place, keep comments, keep the backup. Values containing `$` or newlines are rejected on purpose (compose would interpolate). In `.env.example`, comments go on their own line (compose may keep a trailing `# text` as part of the value).
- **Series naming**: ingest with `canon_label()`/`canon_series_key()`, store the internal label (`Cup`, `Oreilly`, `Truck`), display with `series_name()` ("O'Reilly"). Old "Xfinity" spellings are accepted everywhere input is read. Series CSS classes use the internal label (`.sr.Oreilly`). Series colours: Cup red, O'Reilly green, Truck yellow.
- **Time**: datetimes in code are timezone-aware UTC; convert to `LOCAL_TZ` only for display/scheduling. Quiet-hour rules use `QUIET_TZ`.
- **ntfy**: max 3 buttons, in the order website, app, ack/points. Android-only broadcast actions (`APP_INTENT`).
- **Dashboard CSS/JS**: class names share one global namespace, so keep them unique per component (the "icon test running" badge once clashed with `.test`; it is now `.badge.testing`). Cards in the same grid row stay equal height (`main { align-items: stretch }`); race/game titles stay on one line (name in `.tt` with ellipsis). Keep daily button tones close to `.btn.done`, no bright gradients. If a daily row state is added or renamed, update `renderDaily`, `build_summary` and `_done` too. Dark theme, wide layout for a 27" monitor.
- **Display-only test flags** (`icon_test.json`, `missed_test.json`): time-limited files read by a view builder to overlay a simulated state. They never write checklist state and expire on their own. New tests of this kind must be excluded from `runAllReadOnly()` and must refresh the dashboard after running. (Current tests: 11, 3 send + 8 read-only.)
- After changing `dashboard.html`: rebuild, then hard refresh the browser (Ctrl+F5); the old page can linger.
- If the owner says a change looks unchanged, check their uploaded file for the edit before debugging, and ask for a hard refresh.
- Be honest about what is verified vs not. Pasting `docker logs` lines is the fastest way to diagnose.

## Known issues / open items
- Unverified in real races: flag codes 2/3/4/5; hot-track behaviour in rain delays; that a notification's race id matches the schedule's (falls back to nearest same-series start within 18 h).
- Unexplained early unlock: once, Thursday's daily game became "Mark done" around 10 pm Wednesday Arizona time (`TZ=America/Phoenix`). No cause found. If it recurs, get a screenshot of the Daily games card (tag + "updated" time). `DAILY_UNLOCK_TIME` wouldn't hide a wrong date that is already past 08:00.
- Before the unlock time, today's game reads "Not today" / "Coming up", which looks odd. An "Opens 8:00 AM" label would be a one-line change in `renderDaily`. "Preview a missed day" can flip today's own game to Missed before the unlock time (cosmetic).
- Not yet confirmed on a real day: the 22:00 reminder staying quiet when the game is ticked, the 08:00 flip, the Thursday preview, and real "Checked in" button ticks.
- Missed days are not stored (a day is missed only because its date is past and unticked; ticking past days is blocked server-side).
- Sessions are in memory (restart logs everyone out); login rate limit is per client IP; cookie has no `Secure` flag (plain HTTP); `ACK_TOKEN` is in the ack URL query string. Acceptable on LAN/Tailscale.
- Restart-on-save needs Docker's restart policy. A hand-edit of `.env` with an editor that replaces the file leaves the bind mount on the old inode until `docker compose up -d --force-recreate`.
- `_t_live_probe` uses `redirect_stdout` (process-global) under `TEST_LOCK`. History trimming isn't atomic across the CLI and the service.
- Untested: `pc-listener.ps1` (never run on Windows), iPhone `APP_URL` route, building the plugin from `streamdeck-plugin/` source.
- Legacy leftovers: `streamdeck/red.png` and `green.png`, `/data/feed_cache.json`, a stray `gitignore` file next to `.gitignore`.
- No project license chosen yet.

## Possible next work (none requested)
- Run `dev-tests/` for the first time and fix failures; add `node_modules/` to `.gitignore` and `dev-tests/` to `.dockerignore`; turn the ad-hoc checks listed above into real tests, including the unlock gate and reminder-skip.
- Link race rows too; banner crop (`.hero img`); hot-reload of `GAME_*` names without a restart.
- Hardening: persisted sessions, HTTPS via reverse proxy/Tailscale serve.
