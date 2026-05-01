import socket, struct, json, time, os, sys, threading
from http.server import HTTPServer, BaseHTTPRequestHandler

MVP_FILE = os.path.expanduser("~/Documents/current_patch.mvp")
CONFIG_FILE = os.path.expanduser("~/Documents/dmx_router_config.json")
SACN_PORT = 5568
HTTP_PORT = 8091
PLIST = os.path.expanduser("~/Library/LaunchAgents/com.eos.dmxrouter.plist")

def load_config():
    d = {"local_ip": "2.0.0.2"}
    try:
        with open(CONFIG_FILE) as f:
            d.update(json.load(f))
    except:
        pass
    return d

def save_config(c):
    with open(CONFIG_FILE, "w") as f:
        json.dump(c, f, indent=2)

cfg = load_config()

state = {
    "running": True,
    "patch_file": MVP_FILE,
    "mappings": 0,
    "local_ip": cfg["local_ip"],
    "rx_fps": {1: 0, 2: 0},
    "tx_fps": {},
    "patch_mtime": 0,
    "venue_universes": [],
    "errors": [],
}
lock = threading.Lock()

def mcast(u):
    return "239.255.%d.%d" % (u >> 8, u & 0xff)

def parse_sacn(data):
    try:
        if len(data) < 126: return None, None
        if data[4:16] != b"ASC-E1.17\x00\x00\x00": return None, None
        uni = struct.unpack_from("!H", data, 113)[0]
        dlen = min((struct.unpack_from("!H", data, 123)[0] & 0x0fff) - 1, 512)
        if len(data) < 126 + dlen: return None, None
        dmx = list(data[126:126+dlen])
        while len(dmx) < 512: dmx.append(0)
        return uni, dmx
    except:
        return None, None

def build_sacn(universe, vals, seq):
    try:
        dmx = bytes(vals[:512])
        src = b"Multiverse Router" + b"\x00" * 47
        src = src[:64]
        cid = b"\x00" * 16
        pc = len(dmx) + 1
        dl = pc + 10
        dmp = struct.pack("!HH", 0x7000|dl, dl) + b"\x02\xa1\x00\x00\x00\x01" + struct.pack("!H", pc) + b"\x00" + dmx
        fl = len(dmp) + 77
        fr = struct.pack("!HH", 0x7000|fl, fl) + b"\x00\x00\x00\x02" + src + struct.pack("!BBHBB", 100, 0, seq&0xff, 0, universe) + dmp
        rl = len(fr) + 22
        rt = struct.pack("!HH", 0x7000|rl, rl) + b"\x00\x00\x00\x04" + cid + fr
        return b"\x00\x10\x00\x00ASC-E1.17\x00\x00\x00\x00\x00\x00\x04" + rt
    except:
        return None

def load_patch(path):
    patch = {}
    try:
        with open(os.path.expanduser(path)) as f:
            data = json.load(f)
        CH = data.get("CH", {})
        VP = data.get("VP", {})
        for cs, vp in VP.items():
            va = vp.get("venueAddr", "")
            if not va: continue
            ch = CH.get(cs, {})
            ea = ch.get("eosAddr", "")
            if not ea: continue
            def pa(s):
                try:
                    u, a = s.split("/")
                    return int(u), int(a)-1
                except:
                    return None, None
            eu, eaddr = pa(ea)
            vu, vaddr = pa(va)
            if eu is None or vu is None: continue
            p = ch.get("fixParams", 1)
            for i in range(p):
                k = (eu, eaddr+i)
                if k not in patch: patch[k] = []
                patch[k].append((vu, vaddr+i))
        with lock:
            state["mappings"] = len(patch)
            state["venue_universes"] = sorted(set(v[0] for vals in patch.values() for v in vals))
        print("Patch: %d mappings" % len(patch))
    except Exception as e:
        print("Patch error:", e)
        with lock: state["errors"].append(str(e))
    return patch

def router_thread():
    patch = {}
    vdata = {}
    seq = {}
    rxc = {1:0, 2:0}
    txc = {}
    last_fps = time.time()
    last_reload = 0
    lip = state["local_ip"]

    out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    out.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 8)
    out.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(lip))

    inp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    inp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    inp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    inp.bind(("", SACN_PORT))
    inp.settimeout(0.05)
    for u in [1,2]:
        mr = struct.pack("4s4s", socket.inet_aton(mcast(u)), socket.inet_aton(lip))
        inp.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mr)

    print("Router listening on", lip)

    while state["running"]:
        mvp = state["patch_file"]
        try:
            mt = os.path.getmtime(os.path.expanduser(mvp))
            if mt != last_reload:
                patch = load_patch(mvp)
                last_reload = mt
                vdata = {}; seq = {}; txc = {}
        except:
            pass

        now = time.time()
        if now - last_fps >= 1.0:
            with lock:
                state["rx_fps"] = dict(rxc)
                state["tx_fps"] = dict(txc)
            rxc = {1:0, 2:0}; txc = {}
            last_fps = now

        try:
            data, _ = inp.recvfrom(638)
            uni, dmx = parse_sacn(data)
            if uni not in [1,2] or not dmx: continue
            rxc[uni] = rxc.get(uni,0)+1
            changed = set()
            for a in range(len(dmx)):
                for (vu,va) in patch.get((uni,a),[]):
                    if vu not in vdata: vdata[vu]=[0]*512; seq[vu]=0
                    if va<512: vdata[vu][va]=dmx[a]; changed.add(vu)
            for vu in changed:
                pkt = build_sacn(vu, vdata[vu], seq[vu])
                if pkt:
                    seq[vu]=(seq[vu]+1)&0xff
                    out.sendto(pkt,(mcast(vu),SACN_PORT))
                    txc[vu]=txc.get(vu,0)+1
        except socket.timeout:
            pass
        except Exception as e:
            with lock: state["errors"].append(str(e))

    inp.close(); out.close()
    print("Router stopped")

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="color-scheme" content="dark">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="2">
<title>Multiverse Router</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@600;700&family=JetBrains+Mono:wght@500&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{background:#0e0e12;color:#eeeef8;font-family:Inter,system-ui,sans-serif;font-size:13px;color-scheme:dark;min-height:100vh}
#hdr{background:#111118;border-bottom:2px solid #f0a030;padding:8px 14px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.htitle{font-size:14px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#f0a030;white-space:nowrap}
.htitle span{color:#fff}
.dot{width:10px;height:10px;border-radius:50%;background:#30e878;box-shadow:0 0 6px #30e878;flex-shrink:0}
.dot.stopped{background:#f03020;box-shadow:0 0 6px #f03020;animation:blink 1s infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.2}}
.rl{font-family:JetBrains Mono,monospace;font-size:11px;color:#30e878}
.rl.stopped{color:#f03020}
.ipi{background:#0e0e12;border:1px solid #333;color:#30b8f0;font-family:JetBrains Mono,monospace;font-size:12px;padding:4px 8px;border-radius:3px;width:130px}
.ipi:focus{border-color:#f0a030;outline:none}
.lbl{font-family:JetBrains Mono,monospace;font-size:10px;color:#666;text-transform:uppercase;letter-spacing:1px;white-space:nowrap}
button{font-family:Inter,sans-serif;font-size:11px;font-weight:700;padding:5px 12px;border-radius:3px;border:none;cursor:pointer;text-transform:uppercase;white-space:nowrap}
.ba{background:transparent;color:#30b8f0;border:1px solid #30b8f0}
.ba:hover{background:#30b8f0;color:#000}
.br{background:#f0a030;color:#000}
.br:hover{background:#ffc040}
.bs{background:transparent;color:#f03020;border:1px solid #f03020}
.bs:hover{background:#f03020;color:#fff}
.wrap{padding:14px}
.card{background:#18181f;border:1px solid #2a2a3a;border-radius:6px;padding:12px 14px;margin-bottom:10px}
.ct{font-family:JetBrains Mono,monospace;font-size:9px;color:#555;text-transform:uppercase;letter-spacing:2px;margin-bottom:10px}
.row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.fp{font-family:JetBrains Mono,monospace;font-size:11px;color:#30b8f0;flex:1}
.mc{font-size:18px;font-weight:700;color:#f0a030;min-width:40px}
.fi{background:#0e0e12;border:1px solid #333;color:#eee;font-family:JetBrains Mono,monospace;font-size:11px;padding:4px 8px;border-radius:3px;flex:1;min-width:200px}
.fi:focus{border-color:#f0a030;outline:none}
.uc{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.uct{font-family:JetBrains Mono,monospace;font-size:9px;color:#555;text-transform:uppercase;letter-spacing:2px;margin-bottom:8px}
.uni{background:#0e0e12;border:1px solid #1e1e2a;border-radius:4px;padding:8px 12px;margin-bottom:6px;display:flex;align-items:center;justify-content:space-between}
.un{font-family:JetBrains Mono,monospace;font-size:11px;color:#aaa}
.uf{font-family:JetBrains Mono,monospace;font-size:18px;font-weight:700}
.active{color:#30e878}
.idle{color:#2a2a3a}
.empty{color:#555;font-family:JetBrains Mono,monospace;font-size:11px}
.err{margin-top:8px;color:#f03020;font-family:JetBrains Mono,monospace;font-size:10px}
</style></head><body>
<div id="hdr">
<div class="htitle">MULTIVERSE <span>ROUTER</span></div>
<div class="dot SDOT"></div>
<span class="rl SCLS">STXT</span>
<span class="lbl">IP:</span>
<form method="POST" action="/set_ip" style="display:flex;align-items:center;gap:6px">
<input class="ipi" type="text" name="ip" value="LIP">
<button type="submit" class="ba">Apply + Restart</button>
</form>
<form method="POST" action="/restart" style="display:flex">
<button type="submit" class="br">Restart</button>
</form>
<form method="POST" action="/stop" style="display:flex">
<button type="submit" class="bs">Stop</button>
</form>
</div>
<div class="wrap">
<div class="card">
<div class="ct">Patch</div>
<div class="row">
<span class="lbl">File</span><span class="fp">PFS</span>
<span class="lbl">Mappings</span><span class="mc">MC</span>
<form method="POST" action="/set_patch" style="display:flex;align-items:center;gap:6px;flex:1">
<input class="fi" type="text" name="path" value="PFF">
<button type="submit" class="ba">Load</button>
</form></div></div>
<div class="card"><div class="uc">
<div><div class="uct">Input - From Eos</div>IB</div>
<div><div class="uct">Output - To Venue</div>OB</div>
</div></div>
EB
</div></body></html>"""

def sp(p):
    d = os.path.expanduser("~/Documents/")
    if p.startswith(d): return "~/Documents/" + p[len(d):]
    h = os.path.expanduser("~/")
    if p.startswith(h): return "~/" + p[len(h):]
    return p

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def sh(self, html):
        b = html.encode()
        self.send_response(200)
        self.send_header("Content-Type","text/html")
        self.send_header("Content-Length",str(len(b)))
        self.end_headers()
        self.wfile.write(b)
    def rd(self):
        self.send_response(302)
        self.send_header("Location","/")
        self.end_headers()
    def bp(self):
        with lock:
            run=state["running"]; lip=state["local_ip"]; pf=state["patch_file"]
            m=state["mappings"]; rxf=dict(state["rx_fps"]); txf=dict(state["tx_fps"])
            vus=list(state["venue_universes"]); errs=list(state["errors"][-3:])
        dot="" if run else " stopped"
        scls="" if run else " stopped"
        stxt="Running" if run else "Stopped"
        ib=""
        for u in [1,2]:
            fps=rxf.get(u,0); cls="active" if fps>0 else "idle"
            ib+='<div class="uni"><span class="un">Universe %d</span><span class="uf %s">%d fps</span></div>' % (u,cls,fps)
        ob=""
        if vus:
            for vu in vus:
                fps=txf.get(vu,0); cls="active" if fps>0 else "idle"
                ob+='<div class="uni"><span class="un">Universe %d</span><span class="uf %s">%d fps</span></div>' % (vu,cls,fps)
        else:
            ob='<span class="empty">No venue universes patched</span>'
        eb=""
        if errs: eb='<div class="card err">'+'<br>'.join(errs)+'</div>'
        h=HTML
        h=h.replace("SDOT",dot).replace("SCLS",scls).replace("STXT",stxt)
        h=h.replace("LIP",lip).replace("PFS",sp(pf)).replace("PFF",pf)
        h=h.replace("MC",str(m)).replace("IB",ib).replace("OB",ob).replace("EB",eb)
        return h
    def do_GET(self): self.sh(self.bp())
    def do_POST(self):
        n=int(self.headers.get("Content-Length",0))
        body=self.rfile.read(n).decode()
        p={}
        for part in body.split("&"):
            if "=" in part:
                k,v=part.split("=",1)
                p[k]=v.replace("+"," ").replace("%2F","/").replace("%7E","~").replace("%3A",":").replace("%2E",".").replace("%5F","_").replace("%2D","-")
        if self.path=="/set_ip":
            ip=p.get("ip","").strip()
            if ip:
                cfg["local_ip"]=ip; save_config(cfg)
                with lock: state["local_ip"]=ip
            self.rd()
            def di():
                time.sleep(1.5)
                os.system("launchctl unload "+PLIST)
                time.sleep(0.5)
                os.system("launchctl load "+PLIST)
            threading.Thread(target=di,daemon=True).start()
        elif self.path=="/set_patch":
            path=p.get("path","").strip()
            if path:
                with lock: state["patch_file"]=path; state["patch_mtime"]=0
            self.rd()
        elif self.path=="/restart":
            self.rd()
            def di():
                time.sleep(1.5)
                os.execv(sys.executable,[sys.executable]+sys.argv)
            threading.Thread(target=di,daemon=True).start()
        elif self.path=="/stop":
            self.rd()
            def di():
                time.sleep(1.5)
                with lock: state["running"]=False
                time.sleep(2); os._exit(0)
            threading.Thread(target=di,daemon=True).start()
        else: self.rd()

def ht():
    srv=HTTPServer(("0.0.0.0",HTTP_PORT),H)
    print("Router UI: http://127.0.0.1:%d" % HTTP_PORT)
    srv.serve_forever()

if __name__=="__main__":
    if len(sys.argv)>1: state["patch_file"]=sys.argv[1]
    with lock: state["local_ip"]=cfg["local_ip"]
    threading.Thread(target=ht,daemon=True).start()
    router_thread()