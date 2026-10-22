import json, os, sys, threading, urllib.request, urllib.error, io
os.environ.update({"CONFIG_FILE":"/tmp/dt/envS","DATA_DIR":"/tmp/dt/dataS","NTFY_URL":"http://127.0.0.1:9","HTTP_PORT":"0"})
sys.path.insert(0, os.environ.get("REPO", "/home/claude/repo"))
import app
from http.server import ThreadingHTTPServer
from PIL import Image
state=app.load_state(); app.Handler.state=state
srv=ThreadingHTTPServer(("127.0.0.1",0),app.Handler); port=srv.server_address[1]
threading.Thread(target=srv.serve_forever,daemon=True).start(); B=f"http://127.0.0.1:{port}"
fails=0
def check(n,c,x=""):
    global fails; print(("ok   " if c else "FAIL ")+n, "" if c else x); fails+= (not c)
def call(method,path,body=None,cookie=None,headers=None,raw=False):
    h={"X-Requested-With":"nc-dashboard"}; h.update(headers or {})
    if cookie: h["Cookie"]=cookie
    data=body if isinstance(body,(bytes,bytearray)) else (json.dumps(body).encode() if body is not None else None)
    if body is not None and not isinstance(body,(bytes,bytearray)): h["Content-Type"]="application/json"
    req=urllib.request.Request(B+path,data=data,headers=h,method=method)
    try:
        with urllib.request.urlopen(req) as r: return r.status, (r.read() if raw else json.loads(r.read() or b"{}")), r.headers
    except urllib.error.HTTPError as e:
        b=e.read()
        try: return e.code, json.loads(b), e.headers
        except Exception: return e.code, b, e.headers
def png(color):
    buf=io.BytesIO(); Image.new("RGB",(144,144),color).save(buf,"PNG"); return buf.getvalue()

# banner
c,b,h=call("GET","/banner.jpg",raw=True); check("banner served as jpeg with caching", c==200 and h["Content-Type"]=="image/jpeg" and "max-age" in h["Cache-Control"] and b[:3]==b"\xff\xd8\xff")
check("banner size sane (<400 KB)", len(b)<400*1024, len(b))
# icons: public GET 404 until uploaded
c,b,_=call("GET","/icons/green",raw=True); check("no custom icon -> 404", c==404)
c,b,_=call("GET","/icons/evil",raw=True); check("unknown state -> 404", c==404)
# upload needs password+login
c,b,_=call("POST","/api/icon/green",png("red"),headers={"Content-Type":"image/png"}); check("upload without password set rejected", c==403, b)
# set a password via the config and reload module state
app.DASHBOARD_PASSWORD="pw"
c,b,h=call("POST","/api/login",{"password":"pw"}); cookie=h["Set-Cookie"].split(";")[0]; check("login", c==200)
c,b,_=call("POST","/api/icon/green",png("red"),cookie=cookie,headers={"Content-Type":"image/png"}); check("upload png ok", c==200, b)
c,img,h=call("GET","/icons/green",raw=True); check("custom icon served", c==200 and h["Content-Type"]=="image/png" and img[:4]==b"\x89PNG")
c,d,_=call("GET","/api/dashboard",cookie=cookie); check("dashboard reports icon version + can_upload", d["icons"]["green"] and d["icons"]["yellow"] is None and d["auth"]["can_upload"] is True, d["icons"])
# replace with jpeg -> old png removed
buf=io.BytesIO(); Image.new("RGB",(64,64),"blue").save(buf,"JPEG"); c,b,_=call("POST","/api/icon/green",buf.getvalue(),cookie=cookie,headers={"Content-Type":"image/jpeg"})
files=sorted(os.listdir(app.icon_dir())); check("replacing swaps the file type", files==["green.jpg"], files)
c,img,h=call("GET","/icons/green",raw=True); check("served as jpeg now", h["Content-Type"]=="image/jpeg")
# bad uploads
c,b,_=call("POST","/api/icon/green",b"not an image at all",cookie=cookie); check("non-image rejected", c==400, b)
c,b,_=call("POST","/api/icon/green",b"<svg onload=alert(1)>",cookie=cookie); check("svg/html rejected", c==400)
c,b,_=call("POST","/api/icon/green",b"\x89PNG\r\n\x1a\n"+b"0"*(600*1024),cookie=cookie); check("oversize rejected", c==413, c)
c,b,_=call("POST","/api/icon/green",png("red"),cookie=cookie,headers={"X-Requested-With":"x"}); check("upload needs CSRF header", c==403)
c,b,_=call("POST","/api/icon/nope",png("red"),cookie=cookie); check("unknown state upload rejected", c==404)
c,b,_=call("POST","/api/icon/green/reset",{},cookie=cookie); check("reset ok", c==200 and not os.listdir(app.icon_dir()))
c,b,_=call("GET","/icons/green",raw=True); check("after reset -> 404 (page falls back to the built-in tile)", c==404)
# state endpoint unaffected
c,b,_=call("GET","/status"); check("/status still open", c==200 and "state" in b)
print("\nFAILURES:",fails); sys.exit(1 if fails else 0)
