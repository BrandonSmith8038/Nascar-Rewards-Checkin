# NASCAR check-in reminder (Unraid)

> **Unofficial.** This project is not affiliated with or endorsed by NASCAR. It reads NASCAR's public, undocumented
> JSON feeds, which can change or disappear without notice. It only sends reminders: it never logs in to nascar.com or
> automates the Fan Rewards check-in (the program terms prohibit that).

A small self-hosted service that helps you earn NASCAR Fan Rewards points. It:

- sends a push notification when a **Cup, O'Reilly or Truck race is actually live**, so you can press **Check In** yourself;
- sends a **weekend preview** on Thursday evening and a **daily reminder** to earn the dashboard game points;
- serves a **dashboard** (status, this week's races, daily games, recent notifications, tests, settings editor);
- serves a **status endpoint** that drives a colour-changing **Stream Deck key**.

The second series is the O'Reilly Auto Parts Series (formerly Xfinity). Old settings, `races.json` files and saved data
that still say `xfinity` keep working and are treated as O'Reilly.

## Repository layout

| Path | What it is |
|---|---|
| `app.py`, `dashboard.html`, `Dockerfile`, `docker-compose.yml` | the service (Python, standard library only) and its dashboard page |
| `icon.png`, `banner.jpg` | container icon (also served at `/icon.png`) and the dashboard banner; replace either file to change it |
| `.env.example` | template for every setting; copy it to `.env` |
| `update.sh` | `git pull`, rebuild and restart |
| `pc-listener.ps1` | optional Windows script that opens the leaderboard when an alert arrives (untested) |
| `races.example.json` | example manual schedule override |
| `streamdeck/` | key images `state_green.png`, `state_yellow.png`, `state_red.png`, `state_hot.png`, `state_offline.png` |
| `streamdeck-plugin/` | source of the "API Request Multi" Stream Deck plugin (Apache-2.0, derived from mjbnz/sd-api-request; see its `NOTICE`) |
| `dev-tests/` | test harness (Python + jsdom); see its own README |
| `handoff.md` | developer notes for continuing work on the project |

**Settings stay out of git.** Every personal setting (ntfy topic, time zone, IPs, tokens) lives in `.env` next to
`docker-compose.yml`. `.env` is git-ignored and exists only on your server. `docker-compose.yml` has no personal values.

## Install and update

First time, on the server:
```
cd /mnt/user/appdata
git clone https://github.com/YOUR-USER/YOUR-REPO.git nascar-checkin-src
cd nascar-checkin-src
cp .env.example .env
nano .env              # fill in your values; this file never leaves the server
docker compose up -d --build
```
Every update after that: push from your computer, then on the server run `./update.sh`
(`git pull --ff-only`, rebuild, restart; your `.env` and the data folder are untouched). Then **hard refresh** the dashboard
(Ctrl+F5), because `dashboard.html` is baked into the image.

- Keep the clone (`nascar-checkin-src`) and the data folder (`/mnt/user/appdata/nascar-checkin`) as separate folders.
- A private repo needs credentials for `git clone`: use a fine-grained personal access token with read-only "Contents" access,
  or make the repo public (it contains no secrets).
- The compose file needs the volume `- ./.env:/config/.env` (read-write, so the dashboard can save settings) and
  `restart: unless-stopped` (so Docker relaunches the app after a save).
- Required settings: `TZ` (your IANA time zone) and `NTFY_TOPIC` (a long random name; anyone who knows it can read your alerts).
- Subscribe to the topic in the **ntfy** app on your phone (and ntfy web or `pc-listener.ps1` on a PC).
- Check it works: `docker exec nascar-checkin python app.py --test` sends a test notification. Logs: `docker logs nascar-checkin`.

Applying changes to `.env`:
- Edited from the dashboard: **Save & restart** rewrites `.env` and restarts the app in a few seconds.
- Edited by hand: run `docker compose up -d --force-recreate`. (An editor that replaces the file leaves the container looking at
  the old copy until it is recreated.)
- `HTTP_PORT` and `ICON_URL` are read by docker compose, so changing them also needs `docker compose up -d --force-recreate`.

## How alerts work

`ALERT_MODE` (default `auto`):

- **`auto`**: live detection first, the schedule only as a safety net.
- **`live`**: live detection only.
- **`schedule`**: alert at the scheduled start plus `FIRST_ALERT_MIN`.

**Live detection.** The app reads NASCAR's public live feed. A race counts as live when the flag is green, yellow, red or white
during a race session (not practice or qualifying). Only Cup, O'Reilly and Truck are alerted (ARCA and others are ignored, as is
any series you turn off or any race whose name contains a word in `EXCLUDE_NAMES`). When a race goes live you get an alert
right away, then `NUDGES` follow-ups (default 1) every `NUDGE_MIN` minutes (default 45) while it is still live and you haven't
pressed **Checked in**. A race is over on the checkered or cold flag, or after `END_GRACE_MIN` minutes without being live.

**Safety net.** Schedule times are TV start times, about 30 minutes before the green flag. If a race is `SCHED_FALLBACK_MIN`
minutes (default 90) past its scheduled time and the feed doesn't show it running, you get a "may not have started (delay?)"
notice, then a normal alert when it does go live. A hot track (pre-race warm-up) holds that notice back for `HOT_GRACE_MIN`
minutes. If the live feed is down, auto mode behaves like schedule mode.

**Reschedules.** The schedule is re-pulled every `REFRESH_MIN` minutes (60). A race that moves by 10 minutes or more gets a
"race moved" notification. The schedule is merged into a saved copy, so a partial or empty download never shrinks it.
A `races.json` in the data folder overrides the feed entirely (see `races.example.json`; give each race a stable `id`).

### Quiet hours (when it does NOT contact nascar.com)
- **Mon-Thu, all day**, and **every night 10 pm - 6 am Eastern**: no live-feed polling and no schedule refresh. Fri-Sun daytime it
  polls every `LIVE_POLL_SEC` (60 s). Startup always makes one schedule request.
- **Exceptions** (on by default; `QUIET_OVERRIDE=0` turns them off):
  1. A race the feed already shows as live keeps being polled, even past 10 pm.
  2. Any race on the schedule is polled from `PRE_RACE_HOURS` (2) before its start until `WINDOW_HOURS` (5) after.
  3. A race whose time passed but was never seen running (postponed) is checked during daytime hours for up to `PENDING_DAYS` (4)
     days.
- `docker exec nascar-checkin python app.py --polling` shows whether polling is on right now and why. `/status` also reports it.

## Dashboard

Open `http://YOUR-SERVER-IP:8099/`. Keep it on your home network or Tailscale and **do not forward the port on your router**.
Dark theme, refreshes every 5 seconds.

- **Status bar:** "All caught up" or what is still to do (races started but not ticked, today's daily game once it has unlocked).
- **Stream Deck key:** the state the key is showing (green / yellow / red / hot / offline) and the facts behind it: flag number,
  which series are live, whether the app is polling NASCAR, whether the live feed is healthy, alert mode and the time zone the app
  is actually using. *Change preview icons* lets you upload your own picture per state (PNG, JPEG, GIF or WebP, up to 512 KB;
  needs the dashboard password). This changes the dashboard preview only; your real key shows the images you chose in the plugin.
- **This week's races** (Monday to Sunday in your time zone): a **Mark checked in** button per race that unlocks when the race
  starts. Once ticked it stays ticked (undo link if you misclick). On a week with no races it shows the next ones.
- **Daily dashboard games:** the days you named in settings (`GAME_MON` ... `GAME_SUN`).
  - A game is clickable (opens `DASHBOARD_URL`, default the leaderboard, in a new tab); the buttons on the right never navigate.
  - **Mark done** appears on the game's own day from `DAILY_UNLOCK_TIME` (default 08:00, your time zone). Before that, today's game
    reads "Not today", and so do later days.
  - An earlier day you didn't tick shows a red, disabled **Missed**. Missed days can't be recovered.
  - Each weekday's entry starts fresh the following week.
- **Last 5 notifications:** what was sent, when, and whether ntfy accepted it.
- **Tests:** every check the app has, each with a Run button and its output underneath: send a test notification, show or send the
  weekend preview, show or send the daily reminders, the key icon test, **Preview a missed day** (shows the next "Not today" game as
  Missed for 30 seconds, changes nothing), polling status, upcoming races, and the live-feed and schedule-feed checks (those two
  contact NASCAR). Orange tests send a real notification and need the dashboard password. *Run all read-only checks* runs the rest.
- **Settings:** every `.env` setting in a form, grouped, with validation and the default shown. Comments and layout in `.env` are
  kept and the previous version is saved to `/data/env.backup`.

**Password.** Set `DASHBOARD_PASSWORD` in `.env` by hand once and recreate the container. Without it the dashboard is view-only
(settings read-only; icon upload and the notification-sending tests are locked). With it, the whole dashboard asks you to log in
(session lasts 12 hours; 5 wrong tries locks that address out for 5 minutes). The password travels over plain HTTP, so this protects
against other devices on your network, not against someone watching the wire. `/status` stays open for the Stream Deck.

## Notifications

### Race alerts
Each alert has an **Open website** button (`CHECKIN_URL`). ntfy shows at most three buttons: website, optional app button, and
optional **Checked in**.

With `ACK_BASE_URL` (an address your phone can reach, e.g. your Tailscale IP with port 8099) and `ACK_TOKEN` set, **Checked in**
stops the follow-ups and also ticks that race on the dashboard. The daily reminder gets a **Points earned** button that ticks that
day's game.

### Open the NASCAR app from the notification (optional)
I couldn't find a published link that opens the NASCAR mobile app, so use one of these. `APP_LABEL` and `WEB_LABEL` rename the buttons.

- **iPhone:** in the Shortcuts app make a shortcut named `NASCAR` with one action, **Open App** -> NASCAR MOBILE, then set
  `APP_URL=shortcuts://run-shortcut?name=NASCAR` (use `%20` for spaces). Untested by the author.
- **Android (MacroDroid):** ntfy buttons can't launch an app by package name, so the button sends a broadcast that a macro turns into
  "launch the app".
  1. In `.env` set `APP_INTENT=com.nascarcheckin.OPEN_APP` (the name is only a label for the signal), then recreate the container.
  2. Install **ntfy** (subscribed to your topic) and **MacroDroid** (free tier is enough). Set the battery setting of both to
     **Unrestricted** and allow MacroDroid **Display over other apps**.
  3. Create the macro. Trigger: *Intent Received* with the action name `com.nascarcheckin.OPEN_APP` (exact, case-sensitive). Action 1:
     *Launch Application* -> NASCAR MOBILE. Action 2: *Press Back Button* (closes the notification drawer Android leaves open).
  4. Test with the macro's test button, then `docker exec nascar-checkin python app.py --test` and tap **Open NASCAR app**.
  - If the notification doesn't clear and MacroDroid's log is empty, the tap isn't reaching ntfy (check ntfy's battery and
    notification settings). If the macro triggers but the app doesn't open, allow *Display over other apps*. Tasker works the same way.
  - On a new phone the macro has to be recreated; MacroDroid can export it for later import.
- **Either phone, if it works:** an https link that opens the app (use Share inside the app, paste into Notes, tap it) as `APP_URL`.

### Weekend preview (Thursday evening)
Every Thursday at `PREVIEW_TIME` (18:00 your time) one notification lists the weekend's races (series, race, track, local time) under
one of 10 headings; the same one never comes up two weeks running. It is built from the saved schedule, so it makes no request to
nascar.com. With no races that weekend it says so and names the next race; in the offseason (nothing in the next 3 weeks) it stays
quiet. If the container was down at 6 pm it sends when it comes back, still on Thursday.
`PREVIEW=0` turns it off; `PREVIEW_DAY` and `PREVIEW_TIME` change when. Try it with `python app.py --preview-print` or `--preview`.

### Daily dashboard reminder
The Fan Rewards dashboard has games that earn extra points on top of the check-in. Once a day at `DAILY_REMINDER_TIME` (22:00 your
time) you get a notification with a button that opens `DASHBOARD_URL` (and your app button, if configured).

- **Name the day's game:** set `GAME_MON` ... `GAME_SUN` (for example `GAME_MON=Trivia`, or `GAME_FRI=Trivia, Pick 'em`). That day's
  reminder then says "Today's game: Trivia." Days left blank get the generic reminder.
- **Already done? No reminder.** If you have already marked that day's game done when the reminder time arrives, nothing is sent.
  Ticking it after the reminder went out changes nothing.
- **Only on game days:** `DAILY_ONLY_CONFIGURED=1` skips days with no game set. `DAILY_REMINDER=0` turns the reminder off.
- It is sent once per day; if the container was down at 22:00 it sends when it comes back, still the same day.
- Try it: `python app.py --daily-print mon` shows Monday's text; `--daily mon` also sends it (leave the day off for today). Those
  commands always send, whether or not the day is ticked.

## Stream Deck key

`http://YOUR-SERVER-IP:8099/status` (LAN only) returns JSON like
`{"live": true, "state": "yellow", "flag": 2, "series": "Truck", "cup": false, "oreilly": false, "truck": true, ...}`.
The old `xfinity` field is still sent with the same value as `oreilly`, so an existing Stream Deck rule keeps working.

| `state` | meaning (NASCAR flag number) |
|---|---|
| green | race under green (1) or white flag (4) |
| yellow | caution (2) |
| red | red flag / stopped (3) |
| hot | hot track, pre-race warm-up (8) |
| offline | no race running: finished (5), cold track (9), not a race session, no recent data, or feed unreachable |

`live` is true for green, yellow and red. `flag` is the raw number (null when offline). `/status/text` returns `LIVE` or `IDLE`.

**Use the multi-match plugin** from `streamdeck-plugin/`. Build it by zipping the folder and renaming the result:
```
cd streamdeck-plugin && zip -r API_Request_Multi.streamDeckPlugin com.nascarcheckin.sd-api-request-multi.sdPlugin
```
Double-click the file to install; it sits next to the original as "API Request Multi". Built files are git-ignored: attach them to a
GitHub Release instead.

1. Drag **API Request (Multi Match)** onto a key.
2. Tick **Set button image based on response**, then **Periodically poll a URL for status**.
3. Status URL = `http://YOUR-SERVER-IP:8099/status`, poll frequency 5-15 s.
4. JSON Path = `state`. The **Add NASCAR track-state rules** button fills this in plus the rules green/yellow/red/hot.
5. Pick an image per rule from the `streamdeck` folder and set **Default image** = `state_offline.png`.

Rules are checked top to bottom and the first match wins. A value like `1, 4` means "1 or 4", matching ignores case, and the default
image is also shown if the status URL can't be reached. With no request URL set, pressing the key just refreshes the status.
(In the original "API Request" plugin, leave **Expected Value** blank for the old `live` field: typing `true` does not match.)

**Test without a race:** `docker exec nascar-checkin python app.py --icon-test` (or `--icon-test 10` for 10 s per state), or use the
dashboard's Tests card. Each step must be longer than the key's poll interval. Nothing is sent to your phone.

## Docker icon in Unraid
The compose file sets the label `net.unraid.docker.icon` from `ICON_URL`. Set it to
`http://YOUR-UNRAID-IP:8099/icon.png` (the container serves the picture itself), or host `icon.png` anywhere with a direct URL.
Unraid caches icons; if it doesn't update, reload the Docker tab, recreate the container, or add `?v=2` to the URL.

## Settings reference

All settings are in `.env` (and editable on the dashboard). Anything you leave out uses the default shown. Put comments on their own
line, never after a value. The `$` character and line breaks are not allowed in values.

| Setting | Default | Meaning |
|---|---|---|
| `TZ` | UTC | your time zone; used for everything you read and for reminder times (**required**) |
| `NTFY_TOPIC` | none | your ntfy topic (**required**; treat it like a password) |
| `ALERT_MODE` | auto | `auto`, `live` or `schedule` |
| `SERIES` | cup,oreilly,truck | series to alert for (`xfinity` still works and means O'Reilly) |
| `FIRST_ALERT_MIN` | 10 | schedule mode: minutes after the scheduled start |
| `SCHED_FALLBACK_MIN` | 90 | auto: minutes past the scheduled time before "may not have started" |
| `HOT_GRACE_MIN` | 45 | auto: a hot track holds that notice back this long |
| `NUDGE_MIN` / `NUDGES` | 45 / 1 | follow-up spacing / count |
| `FALLBACK_LIVE_HOURS` | 2 | no live feed: the Stream Deck key stays green this long after a scheduled alert |
| `EXCLUDE_NAMES` | arca | live races whose name contains any of these words are ignored |
| `QUIET_DAYS` | mon,tue,wed,thu | days with no polling |
| `QUIET_START_HOUR` / `QUIET_END_HOUR` | 22 / 6 | nightly quiet window |
| `QUIET_TZ` | America/New_York | time zone for the quiet rules |
| `QUIET_OVERRIDE` | 1 | keep polling for live, scheduled and postponed races |
| `PRE_RACE_HOURS` / `WINDOW_HOURS` / `PENDING_DAYS` | 2 / 5 / 4 | override window before a start / after a start / days to watch a postponed race |
| `PREVIEW` / `PREVIEW_DAY` / `PREVIEW_TIME` | 1 / thu / 18:00 | weekend preview |
| `DAILY_REMINDER` / `DAILY_REMINDER_TIME` | 1 / 22:00 | daily reminder on/off and time |
| `DAILY_UNLOCK_TIME` | 08:00 | local time from which the day's game can be marked done on the dashboard |
| `DAILY_ONLY_CONFIGURED` | 0 | 1 = send the daily reminder only on days with a game set |
| `DASHBOARD_URL` | leaderboard | page the daily reminder and the daily game rows open |
| `GAME_MON` ... `GAME_SUN` | blank | the dashboard game for each weekday |
| `CHECKIN_URL` | https://www.nascar.com/leaderboard | page the "Open website" button opens |
| `APP_URL` / `APP_INTENT` / `APP_LABEL` / `WEB_LABEL` | blank / blank / Open NASCAR app / Open website | optional app button and button names |
| `ACK_TOKEN` / `ACK_BASE_URL` | off | "Checked in" / "Points earned" buttons (the address must be reachable from your phone) |
| `DASHBOARD_PASSWORD` | blank | enables login and editing on the dashboard |
| `NTFY_URL` / `NTFY_TOKEN` | https://ntfy.sh / blank | ntfy server and optional access token |
| `LIVE_POLL_SEC` | 60 | how often the live feed is checked when polling is allowed |
| `END_GRACE_MIN` | 5 | minutes not live before a race counts as over |
| `REFRESH_MIN` | 60 | schedule re-pull interval |
| `EMPTY_RETRY_SEC` / `EMPTY_RETRY_QUIET_SEC` | 300 / 900 | retry interval when no schedule is loaded |
| `FEED_URL` / `LIVE_FEED_URL` / `FEED_TZ` | NASCAR feeds / America/New_York | override if the feeds move (keep `{year}` in `FEED_URL`) |
| `HTTP_PORT` | 8099 | dashboard and status port (needs a recreate) |
| `ICON_URL` | blank | Unraid Docker-tab icon URL (read by docker compose; needs a recreate) |

## Command line

Run with `docker exec nascar-checkin python app.py <flag>`:

| Flag | What it does |
|---|---|
| `--test` | send a test notification |
| `--list` | upcoming races it will alert for (may contact NASCAR) |
| `--probe` | read the live feed and show how it is interpreted (contacts NASCAR) |
| `--polling` | whether quiet hours currently block polling |
| `--preview-print` / `--preview` | show / send the weekend preview |
| `--daily-print [day]` / `--daily [day]` | show / send the daily reminder (day = mon..sun, default today) |
| `--icon-test [secs]` | cycle `/status` through green, yellow, red, hot, offline (default 20 s each) |

## Notes

- Reading X/Twitter for delays isn't used: it needs paid API access and free-text parsing is error-prone. The live feed answers "is it
  running right now?" directly, and the hourly schedule refresh covers reschedule notices.
- The project has no license yet. The plugin folder keeps its Apache-2.0 `LICENCE` and `NOTICE`.