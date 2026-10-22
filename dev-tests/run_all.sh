#!/bin/bash
# Usage: REPO=/path/to/repo ./run_all.sh      (needs python3 + Pillow, node 18+, and `npm install jsdom` in this folder)
# Runs every suite and prints a one-line verdict for each. Uses ports 18099 (dashboard fixture) only.
export REPO="${REPO:-/home/claude/repo}"
HERE="$(cd "$(dirname "$0")" && pwd)"
sh "$HERE/setup_fixtures.sh"
fail=0
verdict() { if [ "$2" = "0" ]; then echo "PASS  $1"; else echo "FAIL  $1"; fail=1; fi; }

reset() { rm -rf /tmp/dt/data /tmp/dt/dataR /tmp/dt/dataS; mkdir -p /tmp/dt/data; sh "$HERE/setup_fixtures.sh"; }
reset; python3 "$HERE/py/test_server.py" > /tmp/dt/out_server.txt 2>&1;  verdict "API: auth, done buttons, ack, settings save, history" $?
reset; python3 "$HERE/py/test_style.py"  > /tmp/dt/out_style.txt 2>&1;   verdict "API: banner + custom icon upload/reset" $?
reset; python3 "$HERE/py/test_rename.py" > /tmp/dt/out_rename.txt 2>&1;  verdict "Series rename, aliases, state migration, Tests API" $?
node "$HERE/js/test_rules.js" > /tmp/dt/out_rules.txt 2>&1;               verdict "Stream Deck plugin: rule matching (unit)" $?

ui() {  # name, env fixture, script, extra args...
  NAME="$1"; ENVF="$2"; SCRIPT="$3"; shift 3
  reset
  python3 "$HERE/py/serve.py" "$ENVF" > /tmp/dt/srv.log 2>&1 & SP=$!
  sleep 2.5
  node "$HERE/js/$SCRIPT" http://127.0.0.1:18099 "$@" > /tmp/dt/out_ui.txt 2>&1; RC=$?
  kill $SP 2>/dev/null; wait $SP 2>/dev/null
  verdict "$NAME" $RC; sleep 1
}
ui "Browser sim: races, daily, mark done/undo"      /tmp/dt/envA dashtest.js
ui "Browser sim: settings form edit + save"         /tmp/dt/envC edittest.js /tmp/dt/envC
ui "Browser sim: icons, show/hide, banner"          /tmp/dt/envC styletest.js
ui "Browser sim: layout, series chips, Tests card (no password)" /tmp/dt/envA layouttest.js
ui "Browser sim: layout, Tests card (logged in)"    /tmp/dt/envC layouttest.js login
ui "Browser sim: login overlay when password set"   /tmp/dt/envC logintest.js
exit $fail
