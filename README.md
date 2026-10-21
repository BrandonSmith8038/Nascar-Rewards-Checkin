# NASCAR check-in reminder (Unraid) - v2

> **Unofficial.** This project is not affiliated with or endorsed by NASCAR. It reads NASCAR's public,
> undocumented JSON feeds, which can change or disappear without notice. It only sends reminders: it never logs in
> to nascar.com or automates the Fan Rewards check-in (the program terms prohibit that).

> **Series names:** the second series is the O'Reilly Auto Parts Series (formerly Xfinity). Settings, `races.json` and saved
> data that still say `xfinity` keep working and are treated as O'Reilly.

## Repository layout
| Path | What it is |
|---|---|
| `app.py`, `dashboard.html`, `Dockerfile`, `docker-compose.yml` | the reminder service (Python, standard library only) and its dashboard page |
| `icon.png`, `banner.jpg` | container icon (also served at `/icon.png`) and the banner shown at the top of the dashboard; replace either file to change it |
| `pc-listener.ps1` | optional Windows script that opens the leaderboard on alerts |
| `races.example.json` | example manual schedule override |
| `streamdeck/` | key images (`state_green.png`, `state_yellow.png`, `state_red.png`, `state_hot.png`, `state_offline.png`) |
| `streamdeck-plugin/` | source of the "API Request Multi" Stream Deck plugin (Apache-2.0, derived from mjbnz/sd-api-request; see its `NOTICE`) |

**Settings stay out of git:** every personal setting (ntfy topic, time zone, IPs, tokens) lives in a file named `.env`
next to `docker-compose.yml`. `.env` is git-ignored and exists only on your server; `.env.example` is the committed
template. `docker-compose.yml` itself has no personal values, so the same file is on GitHub and on the server.

## Deploying from GitHub (one codebase, no placeholder copy)
First time, on the server:
```
cd /mnt/user/appdata
git clone https://github.com/YOUR-USER/YOUR-REPO.git nascar-checkin-src
cd nascar-checkin-src
cp .env.example .env
nano .env              # fill in your real values; this file never leaves the server
docker compose up -d --build
```
Every update after that: push from your computer, then on the server run `./update.sh`
(it does `git pull --ff-only`, rebuilds, and restarts; your `.env` and the `/data` folder are untouched).
- Keep the clone (`nascar-checkin-src`) and the data folder (`/mnt/user/appdata/nascar-checkin`) as separate folders.
- Private repo: `git clone` needs credentials. Use a fine-grained personal access token with read-only "Contents"
  access (`git clone https://<token>@github.com/...`; it is then stored only in the server's `.git/config`), or make the
  repo public (this repo contains no secrets).
- Moving from an older setup that used `environment:` lines in the compose file: copy those values into `.env`, run
  `docker compose down` in the old folder (data is kept), then start the new one as above.

**Installing the Stream Deck plugin from source:** zip the plugin folder and open the result, e.g.
`cd streamdeck-plugin && zip -r API_Request_Multi.streamDeckPlugin com.nascarcheckin.sd-api-request-multi.sdPlugin`
(or build the zip with any tool and rename it to `.streamDeckPlugin`), then double-click it. Built files are
git-ignored; attach them to a GitHub Release instead.


Sends a push when a Cup, O'Reilly, or Truck race is **actually live** so you can press **Check In**
yourself. It never logs into nascar.com or clicks anything (the Fan Rewards terms prohibit
bots/automation for earning points).

## What's new in v2
- **Live detection** from NASCAR's public live feed (flag state), so delays/postponements stop mattering:
  the alert fires when the race really goes green, whenever that is.
- **Safety net:** if the race is past its scheduled start and the feed doesn't show it running, you get a
  "may not have started (delay?)" notice, then a normal alert when it does go live.
- **Reschedule notices:** the schedule is re-pulled hourly; if a start time moves you get a "race moved" push.
- **Stream Deck status endpoint** (`/status`) for a red/green key.
- **Custom Docker icon** for Unraid (`/icon.png`).

## Update an existing install
1. Replace the files in `/mnt/user/appdata/nascar-checkin-src` with this folder's contents
   (keep your edited `docker-compose.yml` values: topic, TZ).
2. `docker compose up -d --build` (note the new `ports:` and `labels:` in the compose file).
3. Verify the live feed (do this once, ideally on a race day or practice session):
   ```
   docker exec nascar-checkin python app.py --probe
   ```
   You want: `Recognised flag_state: True`. Paste the output to me if it doesn't, and we'll adjust.
   Until the feed is recognised, `auto` mode simply behaves like v1 (scheduled alerts).

Modes (`ALERT_MODE`): `auto` (default; live first, schedule as safety net), `live` (live only), `schedule` (v1 behaviour).

## Quiet hours (when it does NOT contact nascar.com)
- **Mon-Thu, all day**, and **every night 10 pm - 6 am Eastern**: no live-feed polling and no schedule refresh.
  Fri-Sun daytime it polls every `LIVE_POLL_SEC` (60 s). (Startup always makes one schedule request.)
- **Exceptions (on by default, `QUIET_OVERRIDE=0` turns them off)**, because a few real cases fall in the quiet times:
  1. A race the feed already shows as live keeps being polled, even past 10 pm (rain-delayed finishes).
  2. Any race on the schedule is polled from 2 h before its start until 5 h after (midweek events like the
     Clash or Duels, or a race moved to a Monday once the schedule feed shows the new date).
  3. A race whose scheduled time passed but was never seen running (rain-out, postponed) keeps getting
     checked during daytime hours for up to 4 days, so a Sunday race moved to Monday/Tuesday isn't missed.
- `docker exec nascar-checkin python app.py --polling` shows whether polling is on right now and why.
  `/status` also reports `"polling"`.

## Dashboard
Open `http://YOUR-SERVER-IP:8099/` (same port as `/status`; keep it on your home network or Tailscale and don't forward
it on your router). Dark theme, refreshes every 5 seconds:
- **Header:** "All caught up" or what's still to do (races started but not ticked, today's daily game).
- **Stream Deck key:** the state the key is showing (green / yellow / red / hot / offline) plus the facts behind it: flag
  number, which series are live, whether the app is polling NASCAR right now, and whether the live feed is healthy.
- **This week's races:** the current race week (Monday to Sunday, your time zone) with a **Mark checked in** button per
  race. It unlocks when the race starts. Once ticked it stays ticked (undo link if you misclick); next week's races are
  new rows, so each starts un-ticked. On a week with no races it shows the next ones.
- **Daily dashboard games:** the days you named in settings (`GAME_MON` ... `GAME_SUN`) with **Mark done**. Only today's
  game can be ticked; each weekday's entry starts fresh the following week.
- **Preview icons:** under the key card, *Change preview icons* lets you upload your own image for each of the five
  states (PNG, JPEG, GIF or WebP, up to 512 KB; needs the dashboard password). **This only changes the picture on the
  dashboard.** Your actual Stream Deck key shows whichever images you chose in the plugin's settings (file paths on your
  PC), and the plugin can't fetch images from this server, so upload the same files here if you want the page to match.
  Reset returns a state to the built-in tile. Uploads are stored in `/data/icons/`.
- **Tests:** every check the app has, each with a Run button and its output shown underneath: send a test notification,
  show or send the weekend preview, show or send the daily reminders, run the key icon test, check polling status, list
  upcoming races, and check NASCAR's live feed and schedule feed (those two do contact NASCAR). The orange ones put a real
  notification on your phone and need the dashboard password; *Run all read-only checks* runs the rest in one go.
- **Series colors:** Cup red, O'Reilly green, Truck yellow.
- **Last 5 notifications:** what was sent, when, and whether ntfy accepted it.
- **Settings:** every `.env` setting in a form, grouped, with validation and the default shown. **Save & restart** rewrites
  your `.env` (comments and layout are kept; the previous version is saved to `/data/env.backup`) and restarts the app so
  the change applies in a few seconds. `HTTP_PORT` and `ICON_URL` are read by docker compose, so they also need
  `docker compose up -d --force-recreate`.

**Password.** Viewing is open (like `/status`). Editing settings needs `DASHBOARD_PASSWORD` in `.env` - add it by hand once
and recreate the container; the dashboard then asks you to log in (session lasts 12 hours, 5 wrong tries locks you out for
5 minutes). It travels over plain HTTP, so this protects against other devices on your network, not against someone
watching the wire. If you set the password, the whole dashboard asks for it (the Stream Deck `/status` endpoint stays open).

**Notification buttons feed the checklist.** With the button address and token configured (`ACK_BASE_URL`, `ACK_TOKEN`),
the race alert's **Checked in** button also ticks that race on the dashboard, and the daily reminder gets a **Points earned**
button that ticks today's game.

**Needs in `docker-compose.yml`:** the volume `- ./.env:/config/.env` (read-write) so the app can see and edit the file,
and `restart: unless-stopped` (already there) so docker relaunches the app after a save. If you edit `.env` by hand
instead, recreate the container as before (`docker compose up -d --force-recreate`).

## Daily dashboard reminder
The Fan Rewards dashboard has games that earn extra points on top of the check-in. Once a day (default 22:00 your
local time, `DAILY_REMINDER_TIME`) you get a notification reminding you to earn them, with a button that opens the
dashboard (`DASHBOARD_URL`, default the leaderboard link) and your "Open NASCAR app" button if you set one up.
- **Name the day's game:** in `.env`, uncomment `GAME_MON` ... `GAME_SUN` and fill in the game (for example
  `GAME_MON=Trivia`). That day's reminder then says "Today's game: Trivia." Days left commented out get the generic reminder.
  Use plain text; a few games for one day can be written together (`GAME_FRI=Trivia, Pick 'em`).
- **Only on game days:** `DAILY_ONLY_CONFIGURED=1` skips days with no game set. `DAILY_REMINDER=0` turns it off.
- **Applying changes:** `.env` is read when the container is created, so after editing run
  `docker compose up -d --force-recreate`.
- **Try it:** `docker exec nascar-checkin python app.py --daily-print mon` shows what Monday's reminder will say;
  `--daily mon` also sends it (leave the day off for today's). It is sent once per day; if the container is down at
  22:00 it sends when it comes back up, still the same day.

## Weekend preview (Thursday evening)
Every Thursday at `PREVIEW_TIME` (18:00 your local time) you get one notification listing the weekend's races
(series, race name, track, and time in your time zone), under a randomly chosen heading and opening line. There are
10 variations and the same one never comes up two weeks in a row. It's built from the saved schedule, so it makes no
requests to nascar.com. On a weekend with no races it says so and names the next race; in the offseason (nothing in
the next 3 weeks) it stays quiet. If the container was down at 6 pm it sends when it comes back up, still on Thursday.
- Preview it now: `docker exec nascar-checkin python app.py --preview-print` (just shows it) or `--preview` (also sends it).
  Before Monday it shows the races up to the coming Monday, so run it mid-week to see a full-weekend example.
- Settings: `PREVIEW=0` turns it off, `PREVIEW_DAY`, `PREVIEW_TIME`.
- The track name comes from NASCAR's schedule feed; it appears after the first schedule refresh on the new version.

## Notification buttons: website and NASCAR app
Each alert has a button for the website. Set `APP_URL` to also get an **Open NASCAR app** button (`APP_LABEL` and
`WEB_LABEL` rename the buttons). ntfy shows at most 3 buttons (website, app, "Checked in").
Test any change instantly, no race needed: `docker exec nascar-checkin python app.py --test`.

I couldn't find a published link that opens the NASCAR MOBILE app, so you need one that works on your phone:
- **iPhone (most reliable):** in the Shortcuts app make a shortcut named `NASCAR` with one action, **Open App**
  -> NASCAR MOBILE. Then `APP_URL=shortcuts://run-shortcut?name=NASCAR` (use `%20` for spaces in the name).
  Shortcuts flashes briefly on screen, then the app opens.
- **Either phone, if it works:** find an https link that opens the app. In the NASCAR app use Share on a screen
  (leaderboard, Fan Rewards), paste the link into Notes, tap it: if it opens the app (not Safari/Chrome), use it as `APP_URL`.
- **Android** (ntfy's buttons can open links but can't launch an app by package name, `com.nascar.nascarmobile`).
  The method that works is a **MacroDroid** macro on the phone, triggered by a broadcast the button sends:
  1. In `.env` set `APP_INTENT=com.nascarcheckin.OPEN_APP` (optionally `APP_LABEL`), then
     `docker compose up -d --force-recreate`. `com.nascarcheckin.OPEN_APP` is only a label for the signal; nothing
     happens on the phone until a macro listens for that exact name.
  2. Install **ntfy** (subscribe to your topic) and **MacroDroid** (free tier is enough). Set the battery setting of
     both to **Unrestricted**, and allow MacroDroid **Display over other apps**. If it asks to enable its accessibility
     service for the Back action, allow it.
  3. Create the macro:
     - **Trigger:** Device Events -> *Intent Received* -> action name `com.nascarcheckin.OPEN_APP`
       (exactly this text, case-sensitive; it must match `APP_INTENT`)
     - **Action 1:** Applications -> *Launch Application* -> NASCAR MOBILE
     - **Action 2:** the *Press Back Button* action. Android leaves the notification drawer open behind an app
       launched this way; this closes it. No wait between the two actions was needed.
  4. Test: run the macro's test button, then `docker exec nascar-checkin python app.py --test` and tap **Open NASCAR app**.
  - *Troubleshooting:* notification doesn't clear and MacroDroid's log is empty -> the tap isn't reaching ntfy (check
    ntfy's battery/notification settings; try the button inside the ntfy app). Macro triggers but the app doesn't
    open -> allow *Display over other apps*. Tasker works the same way (an *Intent Received* event with the same name).
  - *New phone:* the macro lives on the phone, so redo steps 2-3 from this list. You can also export the macro from
    MacroDroid (macro menu -> Export/Share) and keep the file somewhere safe to import later.
  - *Alternatives (untested, only if you'd rather not use MacroDroid):* an https link that opens the app (Settings ->
    Apps -> NASCAR MOBILE -> Open by default; use Share in the app and tap the link from Notes), or a custom link scheme
    found with `adb shell dumpsys package com.nascar.nascarmobile | grep -i -B3 -A8 scheme`. Put it in `APP_URL`.

## 1. Docker icon in Unraid
The compose file sets the label `net.unraid.docker.icon`. Replace `YOUR-UNRAID-IP` with your server's LAN IP
(the container serves the picture itself at `http://IP:8099/icon.png`). Alternatives:
- Host `icon.png` anywhere with a direct URL (e.g. a GitHub repo "raw" link) and put that URL in the label.
- Compose Manager plugin: Edit Stack -> Settings -> set the **Icon URL** field.
- `docker run`: add `--label net.unraid.docker.icon=URL`.
Unraid caches icons; if it doesn't update, reload the Docker tab (or recreate the container).

## 2. Stream Deck key (multi-state)
`http://UNRAID-IP:8099/status` (LAN only) returns JSON like
`{"live": true, "state": "yellow", "flag": 2, "series": "Truck", "cup": false, "oreilly": false, "truck": true, ...}`
(the second series is the O'Reilly Auto Parts Series, formerly Xfinity. The old `xfinity` key is still sent with the same value, so an existing Stream Deck rule keeps working.)

| `state` | meaning (NASCAR flag number) |
|---|---|
| green | race under green (1) or white flag (4) |
| yellow | caution (2) |
| red | red flag / stopped (3) |
| hot | hot track, pre-race warm-up (8) |
| offline | no race running: finished (5), cold track (9), not a race session, no recent data, or feed unreachable |

`live` stays true for green/yellow/red. `flag` is the raw number (null when offline) if you want white and
checkered separately. `/status/text` returns LIVE or IDLE.

**Use the multi-match plugin** (`streamdeck-plugin/`, a modified copy of the "API Request" plugin; build and
install it as described at the top; it sits next to the original as "API Request Multi"):
1. Drag **API Request (Multi Match)** onto a key.
2. Tick **Set button image based on response**, then **Periodically poll a URL for status**.
3. Status URL = `http://UNRAID-IP:8099/status`, poll frequency 5-15 s.
4. JSON Path = `state` (the **Add NASCAR track-state rules** button fills this in plus the rules green/yellow/red/hot).
5. Pick an image for each rule from the `streamdeck` folder (`state_green.png`, `state_yellow.png`, `state_red.png`,
   `state_hot.png`) and set **Default image** = `state_offline.png`.
Rules are checked top to bottom, first match wins; a value like `1, 4` means "1 or 4" (so you could match on
`flag` instead); matching ignores case. The default image is also shown if the status URL can't be reached.
With **no request URL set**, pressing the key just refreshes the status.

**Test without a race:**
```
docker exec nascar-checkin python app.py --icon-test        # green, yellow, red, hot, offline, 20 s each
docker exec nascar-checkin python app.py --icon-test 10     # 10 s each
```
Each step must be longer than the key's poll interval or it can skip a state. Nothing is sent to your phone.

**The original "API Request" plugin:** its Expected Value is compared loosely, so for the old `live` field leave
**Expected Value blank** (blank means "treat the field as a boolean"). Typing `true` does not match.

## Why not scan Twitter/X?
Reading X programmatically needs paid API access (scraping breaks and is against X's terms), and extracting
series + new start time from free-text posts is error-prone. The live feed answers the real question
("is it running right now?") directly, with the hourly schedule refresh covering reschedule notices.

## Setup recap (unchanged)
Pick a long random ntfy topic; subscribe on phone (ntfy app) and PC (ntfy web or `pc-listener.ps1`);
edit `docker-compose.yml`; `docker exec nascar-checkin python app.py --test` and `--list`.
`races.json` in `/mnt/user/appdata/nascar-checkin` still overrides the schedule feed (see `races.example.json`;
give each race a stable `"id"` so reschedule notices work).

## Tuning (env vars)
| Var | Default | Meaning |
|---|---|---|
| ALERT_MODE | auto | auto / live / schedule |
| LIVE_POLL_SEC | 60 | how often the live feed is checked (when polling is allowed) |
| QUIET_DAYS | mon,tue,wed,thu | days with no polling |
| QUIET_START_HOUR / QUIET_END_HOUR | 22 / 6 | nightly quiet window, in QUIET_TZ |
| QUIET_TZ | America/New_York | time zone for the quiet rules |
| QUIET_OVERRIDE | 1 | keep polling for live / scheduled / postponed races (see above) |
| PRE_RACE_HOURS / PENDING_DAYS | 2 / 4 | override window before a start / days to watch for a postponed race |
| END_GRACE_MIN | 5 | minutes not-live before a race counts as over |
| SCHED_FALLBACK_MIN | 90 | auto mode: minutes after the scheduled time before the "may not have started" notice (pre-race show/anthem add ~30 min) |
| HOT_GRACE_MIN | 45 | auto mode: a HOT track holds the "may not have started" notice back until it has been hot this long without a green flag (pre-race vs. jet dryers) |
| APP_URL / APP_INTENT / APP_LABEL / WEB_LABEL | (empty) | optional "Open NASCAR app" button: APP_URL opens a link, APP_INTENT sends an Android broadcast (see above) |
| PREVIEW / PREVIEW_DAY / PREVIEW_TIME | 1 / thu / 18:00 | weekly race-weekend preview (see above) |
| EXCLUDE_NAMES | arca | live races whose name contains any of these words are ignored (only series_id 1-3 = Cup/O'Reilly/Truck are ever alerted) |
| DAILY_REMINDER / DAILY_REMINDER_TIME / DAILY_ONLY_CONFIGURED / DASHBOARD_URL / GAME_MON..GAME_SUN | 1 / 22:00 / 0 / (leaderboard) / (empty) | daily dashboard reminder (see above) |
| DASHBOARD_PASSWORD | (empty) | lets you log in and edit settings on the dashboard (see Dashboard) |
| FIRST_ALERT_MIN | 10 | schedule mode: minutes after start |
| NUDGE_MIN / NUDGES | 45 / 1 | follow-up spacing / count |
| REFRESH_MIN | 60 | schedule re-pull interval (reschedule detection) |
| SERIES | cup,oreilly,truck | series to alert for (the old word `xfinity` still works and means O'Reilly) |
| ACK_TOKEN, ACK_BASE_URL | off | "Checked in" button that stops nudges (URL must be reachable from your phone, e.g. Tailscale IP) |
| LIVE_FEED_URL, FEED_URL, FEED_TZ | NASCAR CF feeds | override if the feeds move |

Logs: `docker logs nascar-checkin`.
