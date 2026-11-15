#!/usr/bin/env python3
"""EquiFire live learning server (web dashboard on your LAN).

Endless loop, one round = one real fire from years the model has never been trained on:
  1. PREDICT  - model sees only what was known on the start day and forecasts the spread.
  2. REVEAL   - real outcome is compared with the prediction (accuracy % is recorded).
  3. LEARN    - model is updated on that outcome (extra trees), then the case is thrown away.
Only the model changes. No case, input or outcome is kept in memory, so the next prediction
cannot look anything up; it can only be better because the model learned. Each update also mixes in
a fixed sample of the ORIGINAL training data (not earlier cases) so single fires cannot distort it.
Scores are "prequential": every case is predicted BEFORE the model has seen it, so the running
accuracy is an honest measure of improvement. A frozen copy of the starting model is scored on
the same cases for comparison.

    .venv/bin/python live_server.py                  # dashboard on http://<laptop-lan-ip>:8088
    .venv/bin/python live_server.py --use-gpu --port 8088 --interval 2
    .venv/bin/python live_server.py --reset          # forget online learning, start from base model
State is saved in models/online_*.json and reports/online_log.csv, so restarts continue learning.
"""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import precision_recall_curve

import forecast
from equifire import datasets, weather
from equifire.collect import load_cells
from equifire.common import (EPOCH, GRID, MODELS, REPORTS, STRIDE_DAY, KeySet, cell_center, cell_key,
                             day_to_year)

BASE_F, ONLINE_F, META_F = MODELS / "online_base.json", MODELS / "online_spread.json", MODELS / "online_meta.json"
LOG_F = REPORTS / "online_log.csv"
LOG_COLS = ["round", "time", "date", "lat", "lon", "start_cells", "ml", "frozen", "rule", "trees"]
UPDATE_TREES, UPDATE_LR, MAX_TREES = 4, 0.02, 4000
REPLAY_F, REPLAY_ROWS = Path(__file__).resolve().parent / "cache" / "replay_spread.parquet", 20000
HISTORY_DAYS, W = 7, 20


class Learner:
    def __init__(self, a):
        self.a = a
        self.lock = threading.Lock()
        self.log = pd.read_csv(LOG_F) if LOG_F.exists() and not a.reset else pd.DataFrame(columns=LOG_COLS)
        self.last = None
        self.status = "starting"
        self.error = None
        self.updates_on = True

        print("[live] loading satellite data...", flush=True)
        cells = load_cells()
        self.act = datasets.viirs_daily(cells).sort_values("day").reset_index(drop=True)
        self.days_arr = self.act["day"].values
        self.ks = KeySet(cell_key(self.act["day"], self.act["gx"], self.act["gy"]))
        meta_prev = json.loads(META_F.read_text()) if META_F.exists() else {}
        res = json.loads((REPORTS / "results.json").read_text())["spread"] if (REPORTS / "results.json").exists() else {}
        self.test_from = meta_prev.get("test_from", res.get("test_from_year", 2023))
        self.frames = weather.cached_frames()
        self.persist, _ = forecast.persistence_days(self.act, self.ks, self.test_from)
        self.res = {"baseline_col": res.get("baseline_col", "nbr_n"),
                    "threshold_baseline": res.get("threshold_baseline", 1.5)}

        if a.reset or not (BASE_F.exists() and ONLINE_F.exists() and META_F.exists()):
            self._train_base(cells)
        if not REPLAY_F.exists():
            self._build_replay(cells)
        self.replay = pd.read_parquet(REPLAY_F)
        self.rrng = np.random.default_rng(1)
        meta = json.loads(META_F.read_text())
        self.features, self.threshold = meta["features"], meta["threshold"]
        self.base, self.model = xgb.XGBClassifier(), xgb.XGBClassifier()
        self.base.load_model(BASE_F)
        self.model.load_model(ONLINE_F)
        for m in (self.base, self.model):
            for k in ("best_iteration", "best_score"):
                m.get_booster().set_attr(**{k: None})
        self.res.update(features=self.features, threshold_ml=self.threshold)
        pool = self.act[day_to_year(self.days_arr) >= self.test_from]
        self.pool = pool[pool["day"] <= self.days_arr.max() - a.days]
        if self.pool.empty:
            raise SystemExit(f"no unseen data for years >= {self.test_from}; download more years first")
        self.rng = np.random.default_rng(a.seed + len(self.log))
        print(f"[live] ready: {len(self.pool):,} burning cell-days in unseen years >= {self.test_from}, "
              f"model has {self._trees()} trees, {len(self.log)} past rounds", flush=True)

    def _build_replay(self, cells):
        """Sample of the ORIGINAL training rows (years before test). Mixed into every update so one
        fire cannot drag the model away from what it learned from the full training set."""
        X, label, year, meta = datasets.build_spread(cells, 300_000)
        X = weather.join_weather(X, meta, self.frames, verbose=False)
        m = year < self.test_from
        X, y = X[m].reset_index(drop=True), label[m]
        idx = np.random.default_rng(0).choice(len(y), min(REPLAY_ROWS * 3, len(y)), replace=False)
        R = X.iloc[idx].copy()
        R["_y"] = y[idx]
        REPLAY_F.parent.mkdir(exist_ok=True)
        R.to_parquet(REPLAY_F)
        print(f"[live] replay sample of original training data: {len(R):,} rows", flush=True)

    def _trees(self):
        return len(self.model.get_booster().get_dump())

    # ------------------------------------------------------------ base model
    def _train_base(self, cells):
        print("[live] training base model on years before %d (first start only)..." % self.test_from, flush=True)
        X, label, year, meta = datasets.build_spread(cells, self.a.max_rows)
        X = weather.join_weather(X, meta, self.frames)
        m = year < self.test_from
        X, y = X[m].reset_index(drop=True), label[m]
        val = np.random.default_rng(0).random(len(X)) < 0.2
        clf = xgb.XGBClassifier(n_estimators=800, learning_rate=0.05, max_depth=6, min_child_weight=20,
                                subsample=0.8, colsample_bytree=0.8, tree_method="hist",
                                device="cuda" if self.a.use_gpu else "cpu", eval_metric="auc",
                                early_stopping_rounds=40)
        clf.fit(X[~val], y[~val], eval_set=[(X[val], y[val])], verbose=50)
        p, r, t = precision_recall_curve(y[val], clf.predict_proba(X[val])[:, 1])
        thr = float(t[np.argmax((2 * p * r / np.maximum(p + r, 1e-9))[:-1])])
        MODELS.mkdir(exist_ok=True)
        # keep only the trees up to the best iteration and drop the stored best_iteration marker,
        # otherwise XGBoost would ignore every tree added later by online learning
        bst = clf.get_booster()[: clf.best_iteration + 1]
        for k in ("best_iteration", "best_score"):
            bst.set_attr(**{k: None})
        bst.save_model(BASE_F)
        bst.save_model(ONLINE_F)
        META_F.write_text(json.dumps({"features": list(X.columns), "threshold": thr, "test_from": self.test_from}))
        if LOG_F.exists():
            LOG_F.unlink()
        self.log = pd.DataFrame(columns=LOG_COLS)
        print(f"[live] base model ready: {clf.best_iteration + 1} trees, decision threshold {thr:.3f}", flush=True)

    # ------------------------------------------------------------ cases
    def _day_rows(self, d):
        i, j = np.searchsorted(self.days_arr, [d, d + 1])
        return self.act.iloc[i:j]

    def _win(self, df, cx, cy):
        return df[(np.abs(df["gx"] - cx) <= W) & (np.abs(df["gy"] - cy) <= W)]

    def pick_case(self):
        for _ in range(500):
            s = self.pool.iloc[int(self.rng.integers(len(self.pool)))]
            t0, cx, cy = int(s["day"]), int(s["gx"]), int(s["gy"])
            start = self._win(self._day_rows(t0), cx, cy)
            if len(start) < self.a.min_cells:
                continue
            hist = pd.concat([self._win(self._day_rows(t0 - k), cx, cy)
                              for k in range(HISTORY_DAYS, -1, -1)], ignore_index=True)
            init = np.stack([start["gx"].values, start["gy"].values], 1)
            init_set = {tuple(c) for c in init.tolist()}
            truth, cum = [], set()
            for k in range(1, self.a.days + 1):
                tr = self._win(self._day_rows(t0 + k), cx, cy)
                pts = np.stack([tr["gx"].values, tr["gy"].values], 1)
                if len(pts):
                    pts = pts[np.abs(pts[:, None, :] - init[None, :, :]).max(2).min(1) <= k]
                cum |= {tuple(c) for c in pts.tolist()} - init_set
                truth.append(set(cum))
            return t0, cx, cy, start, hist, init_set, truth
        raise RuntimeError("no suitable fire found")

    def _score(self, model, method, t0, cx, cy, start, hist, truth):
        sim = forecast.Simulator(model, self.res, self.frames)
        days, _ = sim.run(t0, hist, start, self.a.days, self.persist, method, window=(cx, cy, W))
        cum, ious = set(), []
        for k, nd in enumerate(days, 1):
            cum |= nd
            ious.append(forecast.iou(cum, truth[k - 1]))
        return float(np.mean(ious)), cum

    def _learn(self, model, t0, cx, cy):
        """Teacher-forced update: real burning cells each day -> did neighbours ignite next day?"""
        Xs, ys = [], []
        for k in range(self.a.days):
            fr = self._win(self._day_rows(t0 + k), cx, cy).copy()
            if fr.empty:
                continue
            fr["prev"] = self.ks.has(cell_key(fr["day"], fr["gx"], fr["gy"]) - STRIDE_DAY).astype(np.float32)
            g = datasets.spread_candidates(fr, self.ks)
            if g.empty:
                continue
            X, d, x, y = datasets.spread_features(g, self.ks)
            m = (np.abs(x - cx) <= W) & (np.abs(y - cy) <= W)
            if not m.any():
                continue
            X, d, x, y, tk = X[m].reset_index(drop=True), d[m], x[m], y[m], g["tkey"].values[m]
            lat, lon = cell_center(x, y)
            X = weather.join_weather(X, pd.DataFrame({"day": d, "lat": lat, "lon": lon}), self.frames, verbose=False)
            Xs.append(X[self.features])
            ys.append(self.ks.has(tk + STRIDE_DAY).astype(np.int8))
        if not Xs:
            return 0
        Xn, yn = pd.concat(Xs, ignore_index=True), np.concatenate(ys)
        return self._apply(model, Xn, yn)

    def _apply(self, model, Xn, yn):
        """Update `model` with labelled rows of one case. Returns number of rows learned from."""
        if self._trees() >= MAX_TREES:
            return 0
        R = self.replay.iloc[self.rrng.choice(len(self.replay), min(REPLAY_ROWS, len(self.replay)), replace=False)]
        Xm = pd.concat([R[self.features], Xn[self.features]], ignore_index=True)
        ym = np.concatenate([R["_y"].values, yn])
        model.set_params(n_estimators=UPDATE_TREES, learning_rate=UPDATE_LR)
        model.fit(Xm, ym, xgb_model=model.get_booster())  # continue training: adds trees
        return len(yn)

    # ------------------------------------------------------------ loop
    def round(self):
        t0, cx, cy, start, hist, init_set, truth = self.pick_case()
        # 1. PREDICT (before the model has seen this case)
        ml, ml_cells = self._score(self.model, "ML", t0, cx, cy, start, hist, truth)
        frozen, _ = self._score(self.base, "ML", t0, cx, cy, start, hist, truth)
        rule, _ = self._score(self.model, "rule", t0, cx, cy, start, hist, truth)
        # 2/3. REVEAL + LEARN, then drop the case
        n_learned = self._learn(self.model, t0, cx, cy) if self.updates_on else 0
        if n_learned:
            self.model.save_model(ONLINE_F)
        lat, lon = (float(v) for v in cell_center(cx, cy))
        row = {"round": len(self.log) + 1, "time": int(time.time()), "date": str(EPOCH + np.timedelta64(t0, "D")),
               "lat": round(lat, 3), "lon": round(lon, 3), "start_cells": len(start),
               "ml": ml, "frozen": frozen, "rule": rule, "trees": self._trees()}
        with self.lock:
            self.log = pd.concat([self.log, pd.DataFrame([row])], ignore_index=True)
            REPORTS.mkdir(exist_ok=True)
            self.log.to_csv(LOG_F, index=False)
            self.last = {**row, "learned_rows": n_learned,
                         "map": {"start": [list(c) for c in init_set], "truth": [list(c) for c in truth[-1]],
                                 "pred": [list(c) for c in ml_cells], "cx": cx, "cy": cy, "w": W}}
        print(f"[round {row['round']:>5}] {row['date']} lat {lat:+6.2f} lon {lon:+7.2f} {len(start):>3} cells | "
              f"accuracy ML {ml:5.1%}  frozen {frozen:5.1%}  rule {rule:5.1%} | learned from {n_learned} cells, "
              f"{row['trees']} trees | avg last 50: ML {self.log['ml'].tail(50).mean():.1%} "
              f"frozen {self.log['frozen'].tail(50).mean():.1%}", flush=True)

    def run_forever(self):
        self.status = "running"
        while True:
            try:
                self.round()
                self.error = None
            except Exception as e:  # keep serving; show the problem on the page
                self.error = f"{type(e).__name__}: {e}"
                print("[live] round failed:", self.error, flush=True)
            time.sleep(self.a.interval)

    def state(self):
        with self.lock:
            L = self.log
            n = len(L)
            tail = L.tail(400)

            def avg(col, k, first=False):
                s = L[col].head(k) if first else L[col].tail(k)
                return float(s.mean()) if len(s) else None
            return {"status": self.status, "error": self.error, "rounds": n, "trees": self._trees(),
                    "updates_on": self.updates_on, "days": self.a.days, "test_from": self.test_from,
                    "series": {c: [round(float(v), 4) for v in tail[c]] for c in ("ml", "frozen", "rule")},
                    "first": {c: avg(c, 50, True) for c in ("ml", "frozen", "rule")},
                    "recent": {c: avg(c, 50) for c in ("ml", "frozen", "rule")},
                    "overall": {c: avg(c, n) for c in ("ml", "frozen", "rule")},
                    "last": self.last}


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>EquiFire Live Learning</title>
<style>
:root{--bg:#f6f4ef;--fg:#1d1d1b;--mut:#6b6860;--card:#fff;--line:#ddd8cc;--ml:#c2410c;--fz:#2563eb;--ru:#6b6860;--ok:#15803d}
@media (prefers-color-scheme:dark){:root{--bg:#151412;--fg:#ece9e2;--mut:#a29f97;--card:#1f1e1b;--line:#35332e;--ml:#fb923c;--fz:#60a5fa;--ru:#a29f97;--ok:#4ade80}}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif}
main{max-width:960px;margin:0 auto;padding:20px 16px}h1{margin:0;font-size:26px}.sub{color:var(--mut);margin:2px 0 16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin:0 0 14px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.stat b{display:block;font-size:26px}.stat span{color:var(--mut);font-size:13px}
canvas{width:100%;display:block}.leg span{margin-right:14px;font-size:13px}.leg i{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px}
.err{color:#b91c1c;font-size:14px}
</style></head><body><main>
<h1>EquiFire Live Learning</h1><p class="sub">Predict a real fire &rarr; reveal outcome &rarr; learn from it &rarr; repeat. Every case is predicted before the model sees it.</p>
<div class="card"><div class="grid" id="stats"></div><div class="err" id="err"></div></div>
<div class="card"><b>Accuracy per round</b> <span class="sub">(overlap of predicted vs real newly burned area, 10-round moving average)</span>
<canvas id="chart" height="230"></canvas>
<div class="leg"><span><i style="background:var(--ml)"></i>ML model (keeps learning)</span><span><i style="background:var(--fz)"></i>frozen starting model</span><span><i style="background:var(--ru)"></i>simple rule</span></div></div>
<div class="card"><b>Last case</b> <span class="sub" id="lastinfo"></span><canvas id="map" height="380"></canvas>
<div class="leg"><span><i style="background:#888"></i>burning at start</span><span><i style="background:var(--ok)"></i>really burned (hidden)</span><span><i style="background:var(--ml)"></i>predicted</span></div></div>
</main><script>
const css=n=>getComputedStyle(document.documentElement).getPropertyValue(n);
const pct=v=>v==null?'-':(100*v).toFixed(1)+'%';
function ma(a,k){return a.map((_,i)=>{const s=a.slice(Math.max(0,i-k+1),i+1);return s.reduce((x,y)=>x+y,0)/s.length})}
function fit(c){const r=devicePixelRatio||1,w=c.clientWidth,h=+c.getAttribute('height');c.width=w*r;c.height=h*r;const x=c.getContext('2d');x.scale(r,r);return[x,w,h]}
function chart(s){const[x,w,h]=fit(document.getElementById('chart'));x.clearRect(0,0,w,h);const pad=34;
const ser=[['ml','--ml'],['frozen','--fz'],['rule','--ru']].map(([k,c])=>[ma(s[k],10),css(c)]);
const mx=Math.max(.05,...ser.flatMap(a=>a[0]))*1.15;x.strokeStyle=css('--line');x.fillStyle=css('--mut');x.font='11px system-ui';
for(let i=0;i<=4;i++){const y=pad/2+(h-pad)*(1-i/4);x.beginPath();x.moveTo(pad,y);x.lineTo(w,y);x.stroke();x.fillText((100*mx*i/4).toFixed(0)+'%',0,y+4)}
ser.forEach(([a,c])=>{if(a.length<2)return;x.strokeStyle=c;x.lineWidth=2;x.beginPath();a.forEach((v,i)=>{const X=pad+(w-pad)*i/(a.length-1),Y=pad/2+(h-pad)*(1-v/mx);i?x.lineTo(X,Y):x.moveTo(X,Y)});x.stroke()})}
function map(l){const[x,w,h]=fit(document.getElementById('map'));x.clearRect(0,0,w,h);if(!l||!l.map)return;const m=l.map,n=2*m.w+1,s=Math.min(w,h)/n,ox=(w-s*n)/2;
const draw=(cells,col,fill)=>{x.fillStyle=col;x.strokeStyle=col;cells.forEach(([gx,gy])=>{const X=ox+(gx-m.cx+m.w)*s,Y=(m.cy+m.w-gy)*s;fill?x.fillRect(X,Y,s-1,s-1):(x.lineWidth=2,x.strokeRect(X+1,Y+1,s-3,s-3))})};
draw(m.start,'#888',true);draw(m.truth,css('--ok'),true);draw(m.pred,css('--ml'),false)}
async function tick(){try{const s=await(await fetch('/api/state')).json();
const imp=(s.recent.ml!=null&&s.recent.frozen)?(100*(s.recent.ml-s.recent.frozen)/s.recent.frozen):null;
document.getElementById('stats').innerHTML=[['Rounds played',s.rounds.toLocaleString()],['ML accuracy (last 50)',pct(s.recent.ml)],
['Frozen model (last 50)',pct(s.recent.frozen)],['Simple rule (last 50)',pct(s.recent.rule)],
['Gain vs frozen',imp==null?'-':(imp>=0?'+':'')+imp.toFixed(1)+'%'],['ML first 50 rounds',pct(s.first.ml)],['Trees learned',s.trees]]
.map(([k,v])=>`<div class="stat"><b>${v}</b><span>${k}</span></div>`).join('');
document.getElementById('err').textContent=s.error?('Last round problem: '+s.error):'';
chart(s.series);map(s.last);
if(s.last)document.getElementById('lastinfo').textContent=`#${s.last.round} · ${s.last.date} · lat ${s.last.lat}, lon ${s.last.lon} · ${s.last.start_cells} burning cells · ML ${pct(s.last.ml)} · learned from ${s.last.learned_rows} cells`}catch(e){}}
tick();setInterval(tick,2000);
</script></body></html>"""


def make_handler(L):
    class H(BaseHTTPRequestHandler):
        server_version = "EquiFireLive/1.0"

        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/api/state":
                body, ctype = json.dumps(L.state()).encode(), "application/json"
            elif self.path in ("/", "/index.html"):
                body, ctype = PAGE.encode(), "text/html; charset=utf-8"
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return H


def lan_urls(port):
    urls = []
    try:
        out = subprocess.run(["ip", "-4", "-o", "addr"], capture_output=True, text=True).stdout
        for line in out.splitlines():
            ip = line.split()[3].split("/")[0]
            if not ip.startswith("127."):
                urls.append(f"http://{ip}:{port}")
    except OSError:
        pass
    return urls or [f"http://{socket.gethostname()}:{port}"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0", help="0.0.0.0 = reachable from your LAN")
    ap.add_argument("--port", type=int, default=8088)
    ap.add_argument("--days", type=int, default=3, help="forecast length per round")
    ap.add_argument("--interval", type=float, default=2.0, help="seconds between rounds")
    ap.add_argument("--min-cells", type=int, default=5)
    ap.add_argument("--max-rows", type=int, default=800_000, help="rows for the base model (first start)")
    ap.add_argument("--use-gpu", action="store_true", help="train the base model on the CUDA GPU")
    ap.add_argument("--no-learn", action="store_true", help="predict only, do not update the model")
    ap.add_argument("--reset", action="store_true", help="drop online learning, retrain base model")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()

    L = Learner(a)
    L.updates_on = not a.no_learn
    srv = ThreadingHTTPServer((a.host, a.port), make_handler(L))
    print("=" * 72)
    print(" EquiFire live learning dashboard. Open on any device in your LAN:")
    for u in lan_urls(a.port):
        print("   ", u)
    print(" Ctrl+C to stop (learning is saved after every round).")
    print("=" * 72, flush=True)
    threading.Thread(target=L.run_forever, daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[live] stopped")


if __name__ == "__main__":
    main()
