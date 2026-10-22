import os, sys, threading, time
from datetime import datetime, timedelta, timezone
os.environ.update({"CONFIG_FILE": sys.argv[1], "DATA_DIR": "/tmp/dt/data", "NTFY_URL": "http://127.0.0.1:9", "HTTP_PORT": "0"})
sys.path.insert(0, os.environ.get("REPO", "/home/claude/repo"))
import app
from http.server import ThreadingHTTPServer
app.schedule_restart = lambda delay=1.0: None
state = app.load_state(); app.Handler.state = state
now = datetime.now(timezone.utc); monday, _ = app.week_bounds(now)
app.DASH["races"] = [
 {"id": "2026-series_3-111", "series": "Truck", "name": "Past Truck", "track": "Somewhere Speedway", "start": monday + timedelta(hours=10)},
 {"id": "2026-series_2-222", "series": "Oreilly", "name": "Started OReilly Race", "track": "Track X", "start": now - timedelta(hours=2)},
 {"id": "2026-series_1-333", "series": "Cup", "name": "<b>Future Cup</b>", "track": "Track C", "start": monday + timedelta(days=6, hours=20)}]
for i in range(3): app.ntfy_publish({"title": f"Alert {i}", "message": f"Body {i}\nsecond line"})
srv = ThreadingHTTPServer(("127.0.0.1", 18099), app.Handler); print("serving", flush=True); srv.serve_forever()
