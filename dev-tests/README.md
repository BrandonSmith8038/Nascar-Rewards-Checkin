# dev-tests

Throwaway-but-useful test harness used while building the app. Everything here was run against the real
`app.py` / `dashboard.html` / plugin source. Nothing here touches a real `.env`; fixtures go to `/tmp/dt`.

## Run everything
```
cd dev-tests
npm install jsdom            # browser simulation (no real browser is used)
pip install Pillow           # test_style.py builds tiny PNG/JPEG files
REPO="$(cd .. && pwd)" ./run_all.sh      # when this folder sits at <repo>/dev-tests
```
Expected: ten `PASS` lines. Output of each suite is in `/tmp/dt/out_*.txt` (read it when one fails).

## What is here
| File | Covers |
|---|---|
| `py/test_server.py` | Real HTTP server on a random port: login/logout, CSRF header, brute-force lockout, dashboard payload, mark-done rules (race must have started, daily only today), `/ack` and `/ackdaily` token routes ticking the checklist, settings validation + save (comments kept, backup written, restart scheduled), notification history |
| `py/test_style.py` | `/banner.jpg`, custom key icons (upload, replace, magic-byte validation, size limit, reset), CSRF on upload |
| `py/test_rename.py` | Xfinity -> O'Reilly aliases (`SERIES`, `races.json`, schedule cache, saved state migration, settings form), alert/preview wording, `/status` legacy key, live detection honours `SERIES`, Tests API (all 11 tests, rate limit, login rules) |
| `py/serve.py` | Fixture server used by the browser simulations (port 18099, fake races, 3 fake notifications) |
| `js/test_rules.js` | Stream Deck plugin rule matching (pure functions) |
| `js/dashtest.js`, `edittest.js`, `styletest.js`, `layouttest.js`, `logintest.js` | The dashboard page in jsdom against the real server: rendering, escaping, buttons, settings form, show/hide, icon manager, layout/series chips/Tests card, login overlay |
| `js/pitest.js`, `js/harness.js` | Not in `run_all.sh`. `pitest.js` exercises the plugin's property inspector (add/remove/move rules, file-path handling); `harness.js` runs the plugin runtime against a live `/status` (start `py/serve.py` with a fixture, or the real app, and pass its URL) |

## Limits
- jsdom does not do layout. These tests prove structure and behaviour, not how the page looks.
- No Docker, no Unraid, no real Stream Deck software, no real phone were involved.
- The NASCAR feeds are not reachable from the sandbox these were built in, so feed parsing is tested with
  fabricated payloads shaped like the real ones the owner pasted once (see HANDOFF.md).
