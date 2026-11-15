#!/usr/bin/env python3
"""EquiFire training-pool worker.

DISCLAIMER: This script exists strictly to help train the EquiFire wildfire ML models
(NASA Space Apps 2026 project). While it runs it uses this computer's CPU and/or NVIDIA GPU
and RAM to train test models on a fire dataset downloaded from the pool server. It sends back
only the model settings it tried and their accuracy scores. It does not read, upload or change
any of your files, and it runs only while this window is open. Stop it any time with Ctrl+C.
Run it only on a computer you own or have permission to use.

Usage:
    pip install numpy xgboost scikit-learn
    python pool_worker.py                  # uses NVIDIA GPU if present, else CPU
    python pool_worker.py --device both    # one job on GPU and one on CPU at the same time
    python pool_worker.py --device cpu
"""
import argparse
import io
import json
import os
import warnings
import secrets
import sys
import threading
import time
import urllib.request

SERVER = "__POOL_SERVER__"  # filled in by the pool website when you download this file
TOKEN = "__POOL_TOKEN__"
DISCLAIMER = __doc__.split("Usage:")[0].strip()

# Only these settings are accepted from the server, each clamped to a safe range.
# The server never sends code: only numbers.
PARAM_RANGES = {
    "learning_rate": (0.005, 0.5), "max_depth": (2, 12), "min_child_weight": (0.1, 1000.0),
    "subsample": (0.3, 1.0), "colsample_bytree": (0.3, 1.0), "reg_lambda": (0.0, 100.0),
    "max_bin": (16, 512),
}
INT_PARAMS = {"max_depth", "max_bin"}
KINDS = {"spread", "size_area", "size_frp"}


def api(path, payload=None, timeout=60):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(SERVER + path, data=data, method="POST" if data else "GET",
                                 headers={"X-Pool-Token": TOKEN, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def load_dataset():
    import numpy as np
    cache = os.path.join(os.path.dirname(os.path.abspath(__file__)), "equifire_pool_data.npz")
    info = api("/api/dataset_info")
    if not (os.path.exists(cache) and os.path.getsize(cache) == info["bytes"]):
        print(f"[worker] downloading training data ({info['bytes'] / 1e6:.0f} MB)...", flush=True)
        req = urllib.request.Request(SERVER + "/api/dataset", headers={"X-Pool-Token": TOKEN})
        with urllib.request.urlopen(req, timeout=600) as r, open(cache + ".part", "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
        os.replace(cache + ".part", cache)
    d = np.load(cache, allow_pickle=False)  # plain arrays only, no code
    data = {k: d[k] for k in d.files}
    rows = sum(len(v) for k, v in data.items() if k.startswith("y_"))
    print(f"[worker] dataset ready: {rows:,} rows in RAM", flush=True)
    return data


def clean_params(raw):
    out = {}
    for k, (lo, hi) in PARAM_RANGES.items():
        if k in raw:
            v = float(raw[k])
            v = min(max(v, lo), hi)
            out[k] = int(round(v)) if k in INT_PARAMS else v
    return out


def cuda_available():
    try:
        import numpy as np
        import xgboost as xgb
        xgb.XGBClassifier(device="cuda", n_estimators=2).fit(np.random.rand(64, 3), np.arange(64) % 2)
        return True
    except Exception:
        return False


def run_task(task, data, device):
    import numpy as np
    import xgboost as xgb
    from sklearn.metrics import mean_absolute_error, roc_auc_score

    kind = task["kind"]
    if kind not in KINDS:
        raise ValueError(f"unknown task kind {kind!r}")
    p = clean_params(task.get("params", {}))
    common = dict(device=device, tree_method="hist", n_estimators=3000, early_stopping_rounds=50, **p)
    Xt, Xv = data[f"X_{kind}_train"], data[f"X_{kind}_val"]
    yt, yv = data[f"y_{kind}_train"], data[f"y_{kind}_val"]
    t0 = time.time()
    if kind == "spread":
        m = xgb.XGBClassifier(objective="binary:logistic", eval_metric="auc", **common)
        m.fit(Xt, yt, eval_set=[(Xv, yv)], verbose=False)
        score = float(roc_auc_score(yv, m.predict_proba(Xv)[:, 1]))
    else:
        m = xgb.XGBRegressor(objective="reg:squarederror", eval_metric="mae", **common)
        m.fit(Xt, yt, eval_set=[(Xv, yv)], verbose=False)  # targets are log1p
        score = float(mean_absolute_error(np.expm1(yv), np.expm1(m.predict(Xv))))
    return {"score": score, "n_trees": int(m.best_iteration) + 1, "seconds": time.time() - t0,
            "rows": int(len(yt))}


def loop(data, device, name, stats):
    while True:
        try:
            t = api(f"/api/task?worker={name}&device={device}")
        except Exception as e:
            print(f"[{device}] server unreachable ({e}); retrying in 10 s", flush=True)
            time.sleep(10)
            continue
        if t.get("done"):
            print(f"[{device}] pool finished all tasks. Thank you!", flush=True)
            return
        if "wait" in t:
            time.sleep(t["wait"])
            continue
        try:
            r = run_task(t, data, device)
        except Exception as e:
            print(f"[{device}] task {t.get('id')} failed: {e}", flush=True)
            api("/api/result", {"id": t["id"], "worker": name, "error": str(e)[:300]})
            continue
        api("/api/result", {"id": t["id"], "worker": name, "device": device, **r})
        stats["done"] += 1
        stats["rows"] += r["rows"]
        print(f"[{device}] task {t['id']:>4} {t['kind']:<9} score {r['score']:.4f} | {r['n_trees']} trees, "
              f"{r['seconds']:.0f}s | this worker: {stats['done']} tasks, {stats['rows']:,} rows trained",
              flush=True)


def main():
    global SERVER, TOKEN
    warnings.filterwarnings("ignore", message=".*device.*")
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", choices=["auto", "cuda", "cpu", "both"], default="auto")
    ap.add_argument("--server", default=SERVER)
    ap.add_argument("--token", default=TOKEN)
    ap.add_argument("--accept", action="store_true", help="accept the disclaimer without the prompt")
    a = ap.parse_args()
    SERVER, TOKEN = a.server.rstrip("/"), a.token
    if SERVER.startswith("__"):
        sys.exit("Download this worker from the pool website, or pass --server and --token.")

    print("=" * 72 + "\n" + DISCLAIMER + "\n" + "=" * 72)
    if not a.accept and input("Type 'yes' to accept and start: ").strip().lower() != "yes":
        sys.exit("Not accepted. Nothing was run.")

    has_cuda = cuda_available()
    if a.device == "auto":
        devices = ["cuda" if has_cuda else "cpu"]
    elif a.device == "both":
        devices = ["cuda", "cpu"] if has_cuda else ["cpu"]
    else:
        devices = [a.device]
    if "cuda" in devices and not has_cuda:
        sys.exit("No CUDA (NVIDIA) GPU usable by XGBoost found. Use --device cpu.")
    print(f"[worker] NVIDIA CUDA GPU: {'yes' if has_cuda else 'no'} | using: {', '.join(devices)}")

    data = load_dataset()
    name = f"volunteer-{secrets.token_hex(3)}"  # anonymous: no hostname/username sent
    stats = {"done": 0, "rows": 0}
    threads = [threading.Thread(target=loop, args=(data, d, f"{name}-{d}", stats), daemon=True) for d in devices]
    for t in threads:
        t.start()
    try:
        while any(t.is_alive() for t in threads):
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[worker] stopped by you. Unfinished task returns to the pool automatically.")


if __name__ == "__main__":
    main()
