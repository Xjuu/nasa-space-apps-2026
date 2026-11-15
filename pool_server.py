#!/usr/bin/env python3
"""EquiFire training pool: website + task coordinator.

People open http://<this-laptop-ip>:8000 in a browser, read the disclaimer, tick "I accept",
press Download, and then run the downloaded pool_worker.py themselves. Each worker trains
test models (hyperparameter search) on its own CPU/GPU and reports scores back here.
Best settings are saved to reports/pool_best.json; use them with: python main.py --use-pool-params

    .venv/bin/python pool_server.py                 # port 8000, 200 tasks
    .venv/bin/python pool_server.py --tasks 500 --port 8080
"""
import argparse
import json
import secrets
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
POOL = ROOT / "cache" / "pool"
DATA = POOL / "pool_data.npz"
RESULTS = ROOT / "reports" / "pool_results.json"
BEST = ROOT / "reports" / "pool_best.json"
WORKER_SRC = ROOT / "pool_worker.py"
LEASE_SECONDS = 1800
MAX_BODY = 64 * 1024
HIGHER_BETTER = {"spread": True, "size_area": False, "size_frp": False}
METRIC = {"spread": "ROC-AUC", "size_area": "MAE km2", "size_frp": "MAE MW"}

DISCLAIMER_HTML = """
<p><b>What this is.</b> EquiFire is a NASA Space Apps 2026 project that harmonizes MODIS and VIIRS
satellite fire detections and predicts wildfire spread. This pool lets volunteers lend computing
power to train its machine-learning models.</p>
<p><b>What the worker does.</b> The script you download is <b>strictly for helping train the EquiFire
ML models</b>. After you start it yourself, it downloads a fire training dataset from this server, uses
your computer's CPU and/or NVIDIA GPU and RAM to train test models, and sends back only the model
settings it tried and their accuracy scores.</p>
<p><b>What it does not do.</b> It does not read, upload or change your files, does not install
anything, does not start automatically, and runs only while its window is open. The server sends it
only numbers (model settings), never code. You can read the whole script before running it.</p>
<p><b>Your control.</b> Nothing runs when you press Download &mdash; you get a file. You decide whether
to run it. Stop it any time with Ctrl+C. Run it only on a computer you own or are allowed to use.
Heavy computation makes computers warmer and use more power.</p>
"""

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>EquiFire Training Pool</title>
<style>
:root{--bg:#f6f4ef;--fg:#1d1d1b;--mut:#6b6860;--card:#fff;--line:#ddd8cc;--acc:#c2410c;--ok:#15803d}
@media (prefers-color-scheme:dark){:root{--bg:#151412;--fg:#ece9e2;--mut:#a29f97;--card:#1f1e1b;--line:#35332e;--acc:#fb923c;--ok:#4ade80}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif}
main{max-width:860px;margin:0 auto;padding:24px 16px}
h1{margin:0 0 4px;font-size:28px}.sub{color:var(--mut);margin:0 0 20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px;margin:0 0 16px}
label{display:flex;gap:10px;align-items:flex-start;font-weight:600;margin:14px 0}
input[type=checkbox]{width:20px;height:20px;margin-top:2px}
button{background:var(--acc);color:#fff;border:0;border-radius:8px;padding:12px 20px;font-size:16px;cursor:pointer}
button:disabled{opacity:.4;cursor:not-allowed}
code,pre{font:14px ui-monospace,monospace}pre{background:var(--bg);padding:10px;border-radius:6px;overflow:auto}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.stat b{display:block;font-size:24px}.stat span{color:var(--mut);font-size:13px}
table{width:100%;border-collapse:collapse;font-size:14px}td,th{text-align:left;padding:6px;border-bottom:1px solid var(--line)}
.bar{height:8px;background:var(--line);border-radius:4px;overflow:hidden}.bar i{display:block;height:100%;background:var(--ok)}
</style></head><body><main>
<h1>EquiFire Training Pool</h1><p class="sub">Volunteer computing for wildfire ML &middot; NASA Space Apps 2026</p>
<div class="card"><h2>Disclaimer</h2>__DISCLAIMER__
<form method="post" action="/download">
<label><input type="checkbox" name="accept" value="yes" id="acc" required>
I have read the disclaimer and accept it. I will run the worker only on a computer I own or am allowed to use.</label>
<button id="dl" type="submit" disabled>Download worker script</button></form>
<p class="sub" style="margin-top:12px">Then, on your computer: <code>pip install numpy xgboost scikit-learn</code>
and <code>python pool_worker.py</code> (add <code>--device both</code> to use NVIDIA GPU and CPU at once).</p></div>
<div class="card"><h2>Pool status</h2><div class="stats" id="stats"></div>
<div class="bar" style="margin:14px 0"><i id="prog" style="width:0"></i></div>
<h3>Best results</h3><table id="best"></table><h3>Workers online</h3><table id="workers"></table></div>
</main><script>
const acc=document.getElementById('acc'),dl=document.getElementById('dl');
acc.addEventListener('change',()=>dl.disabled=!acc.checked);
async function tick(){try{const s=await (await fetch('/api/status')).json();
document.getElementById('stats').innerHTML=[['Tasks done',s.done+' / '+s.total],['Workers online',s.workers.length],
['Rows trained',s.rows_trained.toLocaleString()],['GPU tasks',s.gpu_tasks]].map(([k,v])=>`<div class="stat"><b>${v}</b><span>${k}</span></div>`).join('');
document.getElementById('prog').style.width=(100*s.done/Math.max(1,s.total))+'%';
document.getElementById('best').innerHTML='<tr><th>Model</th><th>Best score</th><th>Tasks</th><th>Found by</th></tr>'+
Object.entries(s.best).map(([k,b])=>`<tr><td>${k}</td><td>${b?b.metric+' '+b.score.toFixed(4):'-'}</td><td>${b?b.n:0}</td><td>${b?b.worker:'-'}</td></tr>`).join('');
document.getElementById('workers').innerHTML='<tr><th>Worker</th><th>Device</th><th>Tasks</th></tr>'+
s.workers.map(w=>`<tr><td>${w.name}</td><td>${w.device}</td><td>${w.done}</td></tr>`).join('');}catch(e){}}
tick();setInterval(tick,3000);
</script></body></html>""".replace("__DISCLAIMER__", DISCLAIMER_HTML)


# ---------------------------------------------------------------- dataset
def build_dataset(max_rows):
    """Training-period data only (newest test years stay hidden), split train/val."""
    from equifire import datasets, weather
    from equifire.collect import load_cells
    from equifire.common import REPORTS

    test_from = 2023
    rf = REPORTS / "results.json"
    if rf.exists():
        test_from = json.loads(rf.read_text()).get("spread", {}).get("test_from_year", test_from)
    cells = load_cells()
    rng = np.random.default_rng(0)
    out = {}

    X, label, year, meta = datasets.build_spread(cells, max_rows)
    X = weather.join_weather(X, meta, weather.cached_frames())
    m = year < test_from
    out["spread"] = (X[m].to_numpy(np.float32), label[m].astype(np.float32), list(X.columns))

    Xs, Y, ys = datasets.build_size(cells)
    m = ys < test_from
    for t in ("area", "frp"):
        out[f"size_{t}"] = (Xs[m].to_numpy(np.float32), np.log1p(Y[t].values[m]).astype(np.float32),
                            list(Xs.columns))

    arrays = {}
    for kind, (Xa, ya, cols) in out.items():
        if len(Xa) > max_rows:
            idx = rng.choice(len(Xa), max_rows, replace=False)
            Xa, ya = Xa[idx], ya[idx]
        val = rng.random(len(Xa)) < 0.2
        arrays[f"X_{kind}_train"], arrays[f"y_{kind}_train"] = Xa[~val], ya[~val]
        arrays[f"X_{kind}_val"], arrays[f"y_{kind}_val"] = Xa[val], ya[val]
        (POOL / f"{kind}_features.json").write_text(json.dumps(cols))
        print(f"[pool] {kind}: {len(Xa):,} rows ({(~val).sum():,} train / {val.sum():,} val), years < {test_from}")
    np.savez_compressed(DATA, **arrays)
    print(f"[pool] dataset -> {DATA} ({DATA.stat().st_size / 1e6:.0f} MB)")


# ---------------------------------------------------------------- tasks
def sample_params(rng):
    return {
        "learning_rate": float(np.exp(rng.uniform(np.log(0.02), np.log(0.3)))),
        "max_depth": int(rng.integers(3, 11)),
        "min_child_weight": float(np.exp(rng.uniform(np.log(1), np.log(200)))),
        "subsample": float(rng.uniform(0.5, 1.0)),
        "colsample_bytree": float(rng.uniform(0.5, 1.0)),
        "reg_lambda": float(np.exp(rng.uniform(np.log(0.1), np.log(10)))),
        "max_bin": int(rng.choice([64, 128, 256])),
    }


class Pool:
    def __init__(self, n_tasks, seed):
        self.lock = threading.Lock()
        rng = np.random.default_rng(seed)
        kinds = list(HIGHER_BETTER)
        self.tasks = [{"id": i, "kind": kinds[i % len(kinds)], "params": sample_params(rng),
                       "status": "pending", "lease": 0, "worker": None} for i in range(n_tasks)]
        self.results = json.loads(RESULTS.read_text()) if RESULTS.exists() else []
        done_ids = {r["id"] for r in self.results if "score" in r}
        for t in self.tasks:  # resume after restart
            if t["id"] in done_ids:
                t["status"] = "done"
        self.workers = {}

    def next_task(self, worker, device):
        with self.lock:
            now = time.time()
            w = self.workers.setdefault(worker, {"name": worker, "device": device, "done": 0})
            w["seen"], w["device"] = now, device
            for t in self.tasks:
                if t["status"] == "leased" and t["lease"] < now:
                    t["status"] = "pending"  # worker vanished, hand task to someone else
            for t in self.tasks:
                if t["status"] == "pending":
                    t.update(status="leased", lease=now + LEASE_SECONDS, worker=worker)
                    return {"id": t["id"], "kind": t["kind"], "params": t["params"]}
            if all(t["status"] == "done" for t in self.tasks):
                return {"done": True}
            return {"wait": 15}

    def submit(self, r):
        with self.lock:
            tid = int(r.get("id", -1))
            if not 0 <= tid < len(self.tasks):
                return False
            t = self.tasks[tid]
            if t["status"] != "leased" or t["worker"] != r.get("worker"):
                return False
            if "error" in r or not isinstance(r.get("score"), (int, float)) or not np.isfinite(r["score"]):
                t["status"] = "pending"
                return True
            t["status"] = "done"
            rec = {"id": tid, "kind": t["kind"], "params": t["params"], "score": float(r["score"]),
                   "n_trees": int(r.get("n_trees", 0)), "seconds": float(r.get("seconds", 0)),
                   "rows": int(r.get("rows", 0)), "worker": str(r["worker"])[:80],
                   "device": str(r.get("device", "?"))[:8], "time": time.time()}
            self.results.append(rec)
            w = self.workers.get(rec["worker"])
            if w:
                w["done"] += 1
            RESULTS.parent.mkdir(exist_ok=True)
            RESULTS.write_text(json.dumps(self.results, indent=1))
            BEST.write_text(json.dumps(self.best(), indent=1))
            print(f"[pool] task {tid:>4} {t['kind']:<9} {METRIC[t['kind']]} {rec['score']:.4f} "
                  f"from {rec['worker']} ({rec['device']}) | {self.n_done()}/{len(self.tasks)} done", flush=True)
            return True

    def n_done(self):
        return sum(t["status"] == "done" for t in self.tasks)

    def best(self):
        out = {}
        for kind, hb in HIGHER_BETTER.items():
            rs = [r for r in self.results if r["kind"] == kind]
            if not rs:
                out[kind] = None
                continue
            b = (max if hb else min)(rs, key=lambda r: r["score"])
            out[kind] = {"metric": METRIC[kind], "score": b["score"], "params": b["params"],
                         "n_trees": b["n_trees"], "worker": b["worker"], "n": len(rs)}
        return out

    def status(self):
        with self.lock:
            now = time.time()
            return {"done": self.n_done(), "total": len(self.tasks),
                    "rows_trained": sum(r["rows"] for r in self.results),
                    "gpu_tasks": sum(r["device"] == "cuda" for r in self.results),
                    "workers": [{"name": w["name"], "device": w["device"], "done": w["done"]}
                                for w in self.workers.values() if now - w["seen"] < 120],
                    "best": self.best()}


# ---------------------------------------------------------------- http
def make_handler(pool, token, public_url):
    worker_src = WORKER_SRC.read_text()

    class H(BaseHTTPRequestHandler):
        server_version = "EquiFirePool/1.0"

        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json", extra=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body)
            if isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _authed(self):
            return secrets.compare_digest(self.headers.get("X-Pool-Token", ""), token)

        def _body(self):
            n = int(self.headers.get("Content-Length", 0))
            if n > MAX_BODY:
                raise ValueError("body too large")
            return self.rfile.read(n)

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            if u.path == "/":
                return self._send(200, PAGE, "text/html; charset=utf-8")
            if u.path == "/api/status":
                return self._send(200, pool.status())
            if not self._authed():
                return self._send(403, {"error": "missing or wrong pool token"})
            if u.path == "/api/task":
                w = q.get("worker", ["?"])[0][:80]
                d = q.get("device", ["cpu"])[0][:8]
                return self._send(200, pool.next_task(w, d))
            if u.path == "/api/dataset_info":
                return self._send(200, {"bytes": DATA.stat().st_size})
            if u.path == "/api/dataset":
                size = DATA.stat().st_size
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(size))
                self.end_headers()
                with open(DATA, "rb") as f:
                    while chunk := f.read(1 << 20):
                        self.wfile.write(chunk)
                return
            self._send(404, {"error": "not found"})

        def do_POST(self):
            u = urllib.parse.urlparse(self.path)
            try:
                body = self._body()
            except ValueError:
                return self._send(413, {"error": "too large"})
            if u.path == "/download":
                form = urllib.parse.parse_qs(body.decode(errors="replace"))
                if form.get("accept") != ["yes"]:
                    return self._send(400, "<p>You must accept the disclaimer before downloading. "
                                           "<a href='/'>Back</a></p>", "text/html; charset=utf-8")
                src = worker_src.replace("__POOL_SERVER__", public_url).replace("__POOL_TOKEN__", token)
                print(f"[pool] worker script downloaded by {self.client_address[0]} (disclaimer accepted)")
                return self._send(200, src, "text/x-python; charset=utf-8",
                                  {"Content-Disposition": 'attachment; filename="pool_worker.py"'})
            if u.path == "/api/result":
                if not self._authed():
                    return self._send(403, {"error": "missing or wrong pool token"})
                try:
                    ok = pool.submit(json.loads(body))
                except (ValueError, TypeError):
                    ok = False
                return self._send(200 if ok else 409, {"ok": ok})
            self._send(404, {"error": "not found"})

    return H


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="0.0.0.0", help="0.0.0.0 = reachable from your network")
    ap.add_argument("--tasks", type=int, default=200, help="number of model settings to try")
    ap.add_argument("--max-rows", type=int, default=500_000, help="rows per model in the shared dataset")
    ap.add_argument("--rebuild", action="store_true", help="rebuild shared dataset from cache/")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    POOL.mkdir(parents=True, exist_ok=True)
    if a.rebuild or not DATA.exists():
        build_dataset(a.max_rows)
    token = secrets.token_urlsafe(16)
    url = f"http://{lan_ip()}:{a.port}"
    pool = Pool(a.tasks, a.seed)
    srv = ThreadingHTTPServer((a.host, a.port), make_handler(pool, token, url))
    print("=" * 72)
    print(f" EquiFire training pool running: {url}   ({pool.n_done()}/{a.tasks} tasks done)")
    print(f" Open it in a browser. Volunteers accept the disclaimer, download, and run the worker.")
    print(f" Local worker on this laptop: .venv/bin/python pool_worker.py --server {url} --token {token}")
    print(f" Best settings -> reports/pool_best.json | Ctrl+C to stop")
    print("=" * 72, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[pool] stopped")


if __name__ == "__main__":
    main()
