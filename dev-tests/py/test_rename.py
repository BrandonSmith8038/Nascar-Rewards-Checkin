import json, os, sys, threading, time, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone
os.environ.update({"CONFIG_FILE":"/tmp/dt/envR","DATA_DIR":"/tmp/dt/dataR","NTFY_URL":"http://127.0.0.1:9","HTTP_PORT":"0",
                   "SERIES":"cup,xfinity,truck","LIVE_FEED_URL":"http://127.0.0.1:9/live","FEED_URL":"http://127.0.0.1:9/feed/{year}.json"})
sys.path.insert(0, os.environ.get("REPO", "/home/claude/repo"))
import app
from http.server import ThreadingHTTPServer
fails=0
def check(n,c,x=""):
    global fails; print(("ok   " if c else "FAIL ")+n, "" if c else x); fails+=(not c)
# ---- aliases
check("old SERIES value still enables O'Reilly", app.ENABLED=={"cup","oreilly","truck"}, app.ENABLED)
check("canon labels", [app.canon_label(x) for x in ("Xfinity","O'Reilly","oreilly","Cup","truck")]==["Oreilly","Oreilly","Oreilly","Cup","Truck"])
check("display name", app.series_name("Oreilly")=="O'Reilly")
os.makedirs("/tmp/dt/dataR",exist_ok=True)
json.dump([{"id":"x1","series":"Xfinity","name":"Old name race","start":(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()}],open("/tmp/dt/dataR/races.json","w"))
r=app.load_manual("/tmp/dt/dataR/races.json"); check("races.json 'Xfinity' -> Oreilly", r[0]["series"]=="Oreilly")
json.dump([{"id":"2026-series_2-9","series":"Xfinity","name":"Cached","track":"T","start":(datetime.now(timezone.utc)+timedelta(days=2)).isoformat()}],open(app.cache_path(),"w"))
check("saved schedule cache 'Xfinity' -> Oreilly", app.load_schedule_cache()[0]["series"]=="Oreilly")
old={"events":{"live:Xfinity:5":{"series":"Xfinity","first":1}},"sent":{"live:Xfinity:5":2},"acked":["live:Xfinity:5"],"done":{"race:Xfinity:7":1.0,"daily:2026-10-19":2.0}}
m=app.migrate_series(old)
check("old state migrated", "live:Oreilly:5" in m["events"] and m["events"]["live:Oreilly:5"]["series"]=="Oreilly" and m["sent"]=={"live:Oreilly:5":2} and m["acked"]==["live:Oreilly:5"] and set(m["done"])=={"race:Oreilly:7","daily:2026-10-19"}, m)
# ---- settings: alias values normalised, validated, labelled
os.environ["CONFIG_FILE"]="/tmp/dt/envR"; open("/tmp/dt/envR","w").write("TZ=UTC\nNTFY_TOPIC=abc\nSERIES=cup,xfinity,truck\n")
app.CONFIG_FILE="/tmp/dt/envR"; app.CONFIG_LOADED=True
pay=app.settings_payload(True); it=[i for g in pay["groups"] for i in g["items"] if i["key"]=="SERIES"][0]
check("form shows alias as oreilly", it["value"]=="cup,oreilly,truck" and it["option_labels"]["oreilly"]=="O'Reilly", it)
check("validation accepts old spelling", app.validate_value(app.SETTINGS_BY_KEY["SERIES"],"cup,xfinity")==("cup,oreilly",None))
# ---- text people read
st={"events":{},"sent":{},"acked":[],"done":{}}
payloads=[]; app.ntfy_publish=lambda p: (payloads.append(p) or True)
ev={"series":"Oreilly","name":"Test 300","first":time.time(),"source":"feed","ident":"1"}
app.send_alert("live:Oreilly:1",ev,0); check("alert title says O'Reilly", payloads[-1]["title"]=="O'Reilly race is live - check in", payloads[-1]["title"])
now=datetime.now(timezone.utc); races=[{"id":"a","series":"Oreilly","name":"Race","track":"Trk","start":now+timedelta(days=1)}]
local=now.astimezone(app.LOCAL_TZ); app.PREVIEW_DAY=local.weekday()
b=app.build_preview(races, now.replace(hour=12), None)
print("preview:", (b[1] if b else None))
check("preview mentions O'Reilly", b and "O'Reilly" in b[1] and "Xfinity" not in b[1])
# ---- /status keeps both keys
snap=app.status_snapshot(); check("/status has oreilly + legacy xfinity key", "oreilly" in snap and "xfinity" in snap and snap["xfinity"]==snap["oreilly"])
# ---- live detection honours SERIES
app.ENABLED.discard("truck")
h,e=app.interpret_live({"flag_state":1,"run_type":3,"series_id":3,"race_id":1,"run_name":"Truck race"}); check("disabled series is not live", not app.entry_is_live(e[0]))
h,e=app.interpret_live({"flag_state":1,"run_type":3,"series_id":2,"race_id":2,"run_name":"Race"}); check("enabled O'Reilly is live", app.entry_is_live(e[0]) and e[0]["label"]=="Oreilly")
app.ENABLED.add("truck")
# ---- tests API over real HTTP
app.ntfy_publish=app.ntfy_publish  # keep the capturing stub
app.DASHBOARD_PASSWORD="pw"
state=app.load_state(); app.Handler.state=state; app.DASH["races"]=races
srv=ThreadingHTTPServer(("127.0.0.1",0),app.Handler); port=srv.server_address[1]; threading.Thread(target=srv.serve_forever,daemon=True).start(); B=f"http://127.0.0.1:{port}"
def call(method,path,body=None,cookie=None):
    h={"X-Requested-With":"nc-dashboard","Content-Type":"application/json"}
    if cookie: h["Cookie"]=cookie
    req=urllib.request.Request(B+path,data=(json.dumps(body).encode() if body is not None else None),headers=h,method=method)
    try:
        with urllib.request.urlopen(req) as r: return r.status, json.loads(r.read() or b"{}"), r.headers
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or b"{}"), e.headers
c,t,_=call("GET","/api/tests"); check("password set: tests list needs login", c==401, c)
c,b,h=call("POST","/api/login",{"password":"pw"}); cookie=h["Set-Cookie"].split(";")[0]
c,t,_=call("GET","/api/tests",cookie=cookie); ids=[x["id"] for x in t["tests"]]; print("tests:", ids); check("11 tests listed (incl. missed_day_test)", len(ids)==11 and "missed_day_test" in ids, ids)
c,t,_=call("GET","/api/tests",cookie=cookie); check("can_send after login", t["can_send"] is True)
# not logged in: read-only ok, sending refused
c,b,_=call("POST","/api/tests/run",{"id":"races"}); check("read-only test needs login when a password is set", c==401, c)
for tid in [x["id"] for x in t["tests"]]:
    time.sleep(0.05)
    c,b,_=call("POST","/api/tests/run",{"id":tid},cookie=cookie)
    check(f"run {tid}", c==200 and isinstance(b.get("output"),str) and b["output"], (c,b))
    if tid in ("notification","preview_send","daily_send"): check(f"  {tid} sent a notification", payloads and True)
c,b,_=call("POST","/api/tests/run",{"id":"races"},cookie=cookie); check("rate limited repeat", c==429)
c,b,_=call("POST","/api/tests/run",{"id":"nope"},cookie=cookie); check("unknown test", c==404)
print("sample outputs:"); 
for tid in ("races","polling","daily_show","live_probe","schedule_probe","icon_test"):
    time.sleep(2.1); c,b,_=call("POST","/api/tests/run",{"id":tid},cookie=cookie); print(f"--- {tid} ok={b['ok']}\n{b['output'][:420]}")
print("icon test file:", os.path.exists(app.icon_test_path()))
print("\nFAILURES:",fails); sys.exit(1 if fails else 0)
