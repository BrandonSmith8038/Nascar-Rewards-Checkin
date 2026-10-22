import json, os, sys, threading, time, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone
os.environ.update({"CONFIG_FILE":"/tmp/dt/.env","DATA_DIR":"/tmp/dt/data","NTFY_URL":"http://127.0.0.1:9","HTTP_PORT":"0"})
sys.path.insert(0, os.environ.get("REPO", "/home/claude/repo"))
import app
from http.server import ThreadingHTTPServer
restarts=[]; app.schedule_restart=lambda delay=1.0: restarts.append(1)
state=app.load_state(); app.Handler.state=state
now=datetime.now(timezone.utc)
app.DASH["races"]=[
 {"id":"2026-series_3-111","series":"Truck","name":"Past Truck","track":"Somewhere","start":now-timedelta(hours=26)},
 {"id":"2026-series_2-222","series":"Oreilly","name":"Started OReilly Race","track":"Track X","start":now-timedelta(hours=2)},
 {"id":"2026-series_1-333","series":"Cup","name":"Future Cup","track":"Track C","start":now+timedelta(hours=30)}]
# make sure all three fall in the same race week for the test regardless of today's weekday
monday,_=app.week_bounds(now)
app.DASH["races"][0]["start"]=monday+timedelta(hours=10)
app.DASH["races"][1]["start"]=min(now-timedelta(hours=2), monday+timedelta(days=7)-timedelta(hours=3))
app.DASH["races"][2]["start"]=monday+timedelta(days=6, hours=20)
srv=ThreadingHTTPServer(("127.0.0.1",0),app.Handler); port=srv.server_address[1]
threading.Thread(target=srv.serve_forever,daemon=True).start()
B=f"http://127.0.0.1:{port}"
def call(method,path,body=None,cookie=None,csrf=True,headers=None):
    h={"Content-Type":"application/json"}
    if csrf: h["X-Requested-With"]="nc-dashboard"
    if cookie: h["Cookie"]=cookie
    h.update(headers or {})
    req=urllib.request.Request(B+path,data=(json.dumps(body).encode() if body is not None else None),headers=h,method=method)
    try:
        with urllib.request.urlopen(req) as r: return r.status, json.loads(r.read() or b"{}"), r.headers
    except urllib.error.HTTPError as e:
        raw=e.read(); 
        try: return e.code, json.loads(raw), e.headers
        except Exception: return e.code, raw.decode()[:80], e.headers
fails=0
def check(name, cond, extra=""):
    global fails
    print(("ok   " if cond else "FAIL ")+name, extra if not cond else "")
    if not cond: fails+=1

# ---- public endpoints
c,b,_=call("GET","/status"); check("/status open", c==200 and "state" in b)
req=urllib.request.urlopen(B+"/"); html=req.read(); check("/ serves dashboard html (or 404 until built)", req.status in (200,))
# ---- password set: everything under /api needs login
c,b,_=call("GET","/api/dashboard"); check("dashboard needs login", c==401)
c,b,_=call("POST","/api/login",{"password":"nope"}); check("wrong password rejected", c==401)
c,b,_=call("POST","/api/login",{"password":"pw-test"},csrf=False); check("login without CSRF header rejected", c==403)
c,b,h=call("POST","/api/login",{"password":"pw-test"}); check("login ok", c==200)
cookie=h["Set-Cookie"].split(";")[0]; check("cookie is HttpOnly+SameSite", "HttpOnly" in h["Set-Cookie"] and "SameSite=Strict" in h["Set-Cookie"])
c,d,_=call("GET","/api/dashboard",cookie=cookie); check("dashboard after login", c==200 and d["auth"]["can_edit"] is True, d.get("auth"))
check("races listed", [r["name"] for r in d["races"]]==["Past Truck","Started OReilly Race","Future Cup"] or len(d["races"])>=2, [r["name"] for r in d["races"]])
rows={r["name"]:r for r in d["races"]}
age=(now-app.DASH["races"][0]["start"]).total_seconds()/3600
exp_truck="upcoming" if age<0 else ("started" if age<=12 else "past")
check("statuses", rows["Past Truck"]["status"]==exp_truck and rows["Future Cup"]["status"]=="upcoming" and rows["Future Cup"]["can_mark"] is False, {k:v["status"] for k,v in rows.items()})
check("daily strip shows configured game", [x["game"] for x in d["daily"]]==["Trivia"], d["daily"])
# ---- done buttons
c,b,_=call("POST","/api/done",{"key":rows["Future Cup"]["key"],"done":True},cookie=cookie); check("cannot tick a race that hasn't started", c==400)
started=[r for r in d["races"] if r["can_mark"]][0]
c,b,_=call("POST","/api/done",{"key":started["key"],"done":True},cookie=cookie); check("tick started race", c==200)
c,d2,_=call("GET","/api/dashboard",cookie=cookie); check("race shows done", next(r for r in d2["races"] if r["key"]==started["key"])["done"] is True)
c,b,_=call("POST","/api/done",{"key":started["key"],"done":False},cookie=cookie); check("undo tick", c==200)
c,b,_=call("POST","/api/done",{"key":"race:Cup:nonexistent","done":True},cookie=cookie); check("unknown race rejected", c==404)
c,b,_=call("POST","/api/done",{"key":"evil","done":True},cookie=cookie); check("garbage key rejected", c==400)
dk=d["daily"][0]
c,b,_=call("POST","/api/done",{"key":dk["key"],"done":True},cookie=cookie)
check("daily only on its own day", (c==200)==(dk["can_mark"]), (c,dk["state"]))
# ---- notification button route marks the dashboard done
state["events"]["live:Oreilly:222"]={"series":"Oreilly","ident":"222","first":time.time(),"source":"feed","name":"x"}
state["done"].clear()
req=urllib.request.Request(B+"/ack/live:Oreilly:222?t=tok123",method="POST"); urllib.request.urlopen(req)
check("ack button ticks the race", any(k.startswith("race:Oreilly:222") for k in state["done"]), list(state["done"]))
try: urllib.request.urlopen(urllib.request.Request(B+"/ack/live:Oreilly:222?t=bad",method="POST")); check("bad ack token rejected",False)
except urllib.error.HTTPError as e: check("bad ack token rejected", e.code==403)
today=datetime.now(app.LOCAL_TZ).date().isoformat()
urllib.request.urlopen(urllib.request.Request(B+f"/ackdaily/{today}?t=tok123",method="POST"))
check("daily ack button", f"daily:{today}" in state["done"])
# ---- settings
c,s,_=call("GET","/api/settings",cookie=cookie); check("settings payload", c==200 and s["can_edit"] and len(s["groups"])>=8, s.get("edit_note"))
items={i["key"]:i for g in s["groups"] for i in g["items"]}
check("secret value visible to logged-in editor", items["NTFY_TOPIC"]["value"]=="secret-topic-123")
check("defaults shown for unset keys", items["NUDGES"]["value"]=="1" and items["NUDGES"]["in_file"] is False)
c,b,_=call("POST","/api/settings",{"values":{"NUDGES":"99","TZ":"Nope/Zone","PREVIEW_TIME":"25:00","GAME_FRI":"a$b"}},cookie=cookie)
check("validation errors", c==400 and set(b["errors"])=={"NUDGES","TZ","PREVIEW_TIME","GAME_FRI"}, b)
before=open("/tmp/dt/.env").read()
c,b,_=call("POST","/api/settings",{"values":{"NUDGES":"3","GAME_MON":"Pick 'em, Trivia","GAME_WED":"","TZ":"America/Chicago","PREVIEW_TIME":"17:30","NTFY_TOPIC":"secret-topic-123","HTTP_PORT":"9000"}},cookie=cookie)
after=open("/tmp/dt/.env").read()
check("save ok + restart scheduled", c==200 and b["restart"] and restarts==[1], b)
check("changed keys", b["changed"]==["GAME_MON","GAME_WED","HTTP_PORT","NUDGES","PREVIEW_TIME","TZ"], b["changed"])
check("recreate warning for HTTP_PORT", b["recreate_needed"]==["HTTP_PORT"])
p=app.parse_env_text(after)
check("file content", p["NUDGES"]=="3" and p["GAME_MON"]=="Pick 'em, Trivia" and "GAME_WED" not in p and p["TZ"]=="America/Chicago" and p["NTFY_TOPIC"]=="secret-topic-123", p)
check("comments preserved", "# test env" in after and "# GAME_TUE=" in after)
check("backup written", open("/tmp/dt/data/env.backup").read()==before)
c,b,_=call("POST","/api/settings",{"values":{"NUDGES":"3"}},cookie=cookie); check("no-op save doesn't restart", c==200 and b["restart"] is False and restarts==[1])
c,b,_=call("POST","/api/settings",{"values":{"NUDGES":"3"}},csrf=False,cookie=cookie); check("settings POST needs CSRF header", c==403)
c,b,_=call("POST","/api/settings",{"values":{"NUDGES":"4"}}); check("settings POST needs login", c in (401,403))
# ---- logout, brute force
c,b,_=call("POST","/api/logout",{},cookie=cookie); 
c,b,_=call("GET","/api/dashboard",cookie=cookie); check("logged out", c==401)
codes=[call("POST","/api/login",{"password":"x"})[0] for _ in range(7)]
check("brute force limited", 429 in codes, codes)
# ---- notification history
class Cap(app.BaseHTTPRequestHandler):
    def do_POST(self): self.rfile.read(int(self.headers.get("Content-Length",0))); self.send_response(200); self.end_headers(); self.wfile.write(b"{}")
    def log_message(self,*a): pass
cs=ThreadingHTTPServer(("127.0.0.1",0),Cap); threading.Thread(target=cs.serve_forever,daemon=True).start()
app.NTFY_URL=f"http://127.0.0.1:{cs.server_address[1]}"
for i in range(7): app.ntfy_publish({"title":f"T{i}","message":f"msg {i}"})
app.NTFY_URL="http://127.0.0.1:9"; app.ntfy_publish({"title":"fails","message":"x"})
r=app.recent_notifications(5)
check("history newest-first, 5 max, failures recorded", [x["title"] for x in r]==["fails","T6","T5","T4","T3"] and r[0]["ok"] is False and r[1]["ok"] is True, [x["title"] for x in r])
# ---- missed-day preview overlay (fake 'now' = Monday noon so a later game day always exists)
saved_games=dict(app.DAILY_GAMES); app.DAILY_GAMES["tue"]="Tuesday Game"
done_before=dict(state["done"])
mon_noon=(monday.astimezone(app.LOCAL_TZ).replace(hour=12)).astimezone(timezone.utc)
mpath=app.missed_test_path()
if os.path.exists(mpath): os.remove(mpath)
tue=lambda: [r for r in app.build_daily_view(state, mon_noon) if r["day"]=="tue"][0]
check("missed preview: off by default", tue()["state"]=="upcoming")
json.dump({"until": mon_noon.timestamp()+30}, open(mpath,"w"))
r=tue(); check("missed preview: next upcoming game shows as past, not clickable", r["state"]=="past" and r["can_mark"] is False and r["done"] is False, r)
json.dump({"until": mon_noon.timestamp()-1}, open(mpath,"w")); check("missed preview: expires by itself", tue()["state"]=="upcoming")
os.remove(mpath); app.DAILY_GAMES.clear(); app.DAILY_GAMES.update(saved_games)
check("missed preview never writes checklist state", state["done"]==done_before, state["done"])
print("\nFAILURES:",fails); sys.exit(1 if fails else 0)
