"""--parallel mode: train on GPU and CPU at the same time with resource targets.

Targets (approximate, enforced by pacing between small training chunks):
  GPU  ~95% utilization  - spread model + hyperparameter search on the NVIDIA GPU (XGBoost CUDA)
  CPU  ~65% utilization  - size models + hyperparameter search on CPU threads (XGBoost)
  RAM  <= 18 GB          - hard cap through a systemd cgroup (MemoryMax) when available,
                           plus a watchdog that stops starting new work above the limit
Both lanes keep searching model settings and keep the best model found on validation data.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import xgboost as xgb
from sklearn.metrics import mean_absolute_error, roc_auc_score

GPU_TARGET, CPU_TARGET, RAM_GB = 95.0, 65.0, 18.0
CORES = os.cpu_count() or 4
CHUNK = 25  # trees trained between pacing checks


def reexec_with_memory_cap(ram_gb=RAM_GB):
    """Re-run this program inside a cgroup scope with a hard memory cap (once)."""
    if os.environ.get("EQUIFIRE_CAPPED") or not shutil.which("systemd-run"):
        return False
    cmd = ["systemd-run", "--user", "--scope", "--quiet", "-p", f"MemoryMax={ram_gb:g}G",
           "-p", "MemorySwapMax=0", "--setenv=EQUIFIRE_CAPPED=1", sys.executable] + sys.argv
    os.execvp(cmd[0], cmd)  # replaces this process; no return


def _read(path):
    try:
        return Path(path).read_text()
    except OSError:
        return ""


class Governor:
    """Samples GPU / CPU / RAM once a second, prints status, and tells lanes when to pause."""

    def __init__(self, use_gpu=True):
        self.use_gpu = use_gpu
        self.gpu = 0.0
        self.cpu = 0.0
        self.ram_gb = 0.0
        self.gpu_mem = ""
        self.over_ram = False
        self.stop = False
        self._prev = None
        self.lanes = {}
        threading.Thread(target=self._loop, daemon=True).start()

    def _cpu_now(self):
        f = _read("/proc/stat").splitlines()[0].split()[1:]
        v = list(map(int, f))
        idle, total = v[3] + v[4], sum(v)
        if self._prev:
            di, dt = idle - self._prev[0], total - self._prev[1]
            if dt > 0:
                self.cpu = 0.5 * self.cpu + 0.5 * 100 * (1 - di / dt)
        self._prev = (idle, total)

    def _loop(self):
        tick = 0
        while not self.stop:
            self._cpu_now()
            for line in _read("/proc/self/status").splitlines():
                if line.startswith("VmRSS:"):
                    self.ram_gb = int(line.split()[1]) / 1e6
            self.over_ram = self.ram_gb > RAM_GB * 0.95
            if self.use_gpu and shutil.which("nvidia-smi"):
                try:
                    o = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
                                        "--format=csv,noheader,nounits"], capture_output=True, text=True,
                                       timeout=2).stdout.split(",")
                    self.gpu = 0.5 * self.gpu + 0.5 * float(o[0])
                    self.gpu_mem = f"{float(o[1]) / 1024:.1f}/{float(o[2]) / 1024:.1f} GB"
                except Exception:
                    pass
            tick += 1
            if tick % 10 == 0:
                lanes = " | ".join(f"{k}: {v}" for k, v in self.lanes.items())
                print(f"[parallel] GPU {self.gpu:3.0f}% (target {GPU_TARGET:.0f}, vram {self.gpu_mem}) | "
                      f"CPU {self.cpu:3.0f}% (target {CPU_TARGET:.0f}) | RAM {self.ram_gb:4.1f}/{RAM_GB:g} GB"
                      + (f" | {lanes}" if lanes else ""), flush=True)
            time.sleep(1)

    def pace(self, lane):
        """Called between training chunks: sleep while the lane's resource is above its target."""
        cur, target = (self.gpu, GPU_TARGET) if lane == "gpu" else (self.cpu, CPU_TARGET)
        while not self.stop and cur > target + 3:
            time.sleep(0.15)
            cur = self.gpu if lane == "gpu" else self.cpu
        while self.over_ram and not self.stop:  # memory guard: wait for RAM to drop
            time.sleep(1)


def cpu_threads():
    # 65% of the cores, minus one core that feeds the GPU
    return max(1, int(CORES * CPU_TARGET / 100) - 1)


def sample_params(rng, first=False):
    if first:
        return dict(learning_rate=0.08, max_depth=7, min_child_weight=20, subsample=0.9,
                    colsample_bytree=0.9, reg_lambda=1.0, max_bin=256)
    return {"learning_rate": float(np.exp(rng.uniform(np.log(0.03), np.log(0.25)))),
            "max_depth": int(rng.integers(4, 11)), "min_child_weight": float(np.exp(rng.uniform(0, np.log(200)))),
            "subsample": float(rng.uniform(0.6, 1.0)), "colsample_bytree": float(rng.uniform(0.6, 1.0)),
            "reg_lambda": float(np.exp(rng.uniform(np.log(0.1), np.log(10)))),
            "max_bin": int(rng.choice([128, 256]))}


def fit_chunked(kind, params, device, Xt, yt, Xv, yv, gov, lane, max_trees=1500, patience=3):
    """Train XGBoost in chunks (pace between them), keep best tree count. kind: 'clf' or 'reg'."""
    kw = dict(tree_method="hist", n_estimators=CHUNK, device=device, **params)
    if device == "cpu":
        kw["n_jobs"] = cpu_threads()
    if kind == "clf":
        m = xgb.XGBClassifier(objective="binary:logistic", **kw)
        score = lambda mm: roc_auc_score(yv, mm.predict_proba(Xv)[:, 1])
        better = lambda a, b: a > b
    else:
        m = xgb.XGBRegressor(objective="reg:squarederror", **kw)
        score = lambda mm: mean_absolute_error(np.expm1(yv), np.expm1(mm.predict(Xv)))
        better = lambda a, b: a < b
    best, best_n, bad, booster = None, CHUNK, 0, None
    for n in range(CHUNK, max_trees + 1, CHUNK):
        if gov.stop:
            break
        gov.pace(lane)
        m.fit(Xt, yt, xgb_model=booster)
        booster = m.get_booster()
        s = score(m)
        if best is None or better(s, best):
            best, best_n, bad = s, n, 0
        else:
            bad += 1
            if bad >= patience:
                break
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "m.json"
        booster[:best_n].save_model(p)
        out = xgb.XGBClassifier() if kind == "clf" else xgb.XGBRegressor()
        out.load_model(p)
    for k in ("best_iteration", "best_score"):
        out.get_booster().set_attr(**{k: None})
    return out, float(best), best_n


def search(gov, lane, name, kind, device, Xt, yt, Xv, yv, trials, seed, unit, keep_going=lambda: False,
           best=None):
    """Random search: at least `trials` trials, then more while keep_going() (so this lane stays busy
    until the other lane is finished). Returns (best_model, best_score, best_params)."""
    rng = np.random.default_rng(seed)
    best = best or (None, None, None)
    hb = kind == "clf"
    t0 = time.time()
    i = -1
    while True:
        i += 1
        if gov.stop or (i >= trials and not keep_going()):
            break
        params = sample_params(rng, first=(i == 0))
        m, s, n = fit_chunked(kind, params, device, Xt, yt, Xv, yv, gov, lane)
        is_best = best[0] is None or (s > best[1] if hb else s < best[1])
        if is_best:
            best = (m, s, params)
        gov.lanes[lane] = f"{name} trial {i + 1} best {best[1]:.4f}"
        print(f"[{lane.upper()} lane] {name} trial {i + 1:>2}: {unit} {s:.4f} ({n} trees, "
              f"lr {params['learning_rate']:.3f}, depth {params['max_depth']}){' *best' if is_best else ''} | "
              f"{len(yt):,} training rows | {time.time() - t0:.0f}s", flush=True)
    return best


def train_lanes(spread, size, trials, use_gpu, seed=0, row_cap=1_500_000):
    """spread = (X, label, year), size = (X, Y, year). Returns (pre_models_size, pre_spread).
    GPU lane: spread model. CPU lane: size/area and size/frp. Run at the same time."""
    from . import train as T

    gov = Governor(use_gpu)
    out, errors = {}, []
    done = {"gpu": threading.Event(), "cpu": threading.Event()}

    def split(X, y, year):
        tr, _ = T._split(year)
        Xtr, ytr = X[tr], y[tr]
        if len(ytr) > row_cap:  # keep RAM and trial time bounded
            keep = np.sort(np.random.default_rng(seed).choice(len(ytr), row_cap, replace=False))
            Xtr, ytr = Xtr.iloc[keep], ytr[keep]
        val = T.val_mask(len(ytr))
        return Xtr[~val], ytr[~val], Xtr[val], ytr[val]

    def gpu_lane():
        try:
            X, label, year = spread
            Xt, yt, Xv, yv = split(X, label, year)
            m, s, _ = search(gov, "gpu", "spread", "clf", "cuda" if use_gpu else "cpu", Xt, yt, Xv, yv,
                             trials, seed, "val ROC-AUC", keep_going=lambda: not done["cpu"].is_set())
            m.set_params(device="cpu")
            out["spread"] = m
        except Exception as e:
            errors.append(f"gpu lane: {type(e).__name__}: {e}")
            gov.stop = True
        finally:
            done["gpu"].set()

    def cpu_lane():
        try:
            X, Y, year = size
            data = {t: split(X, np.log1p(Y[t].values), year) for t in ("area", "frp")}
            best = {t: None for t in data}
            for rnd in range(1000):  # round 0 = minimum trials per target, later rounds = keep busy
                for target, (Xt, yt, Xv, yv) in data.items():
                    n = max(2, trials // 2) if rnd == 0 else 1
                    best[target] = search(gov, "cpu", f"size/{target}", "reg", "cpu", Xt, yt, Xv, yv, n,
                                          seed + 1 + rnd, "val MAE", best=best[target])
                if done["gpu"].is_set() or gov.stop:
                    break
            for t in data:
                out[t] = best[t][0]
        except Exception as e:
            errors.append(f"cpu lane: {type(e).__name__}: {e}")
            gov.stop = True
        finally:
            done["cpu"].set()

    print(f"[parallel] GPU lane (CUDA) + CPU lane ({cpu_threads()} of {CORES} threads) start now; "
          f"targets GPU {GPU_TARGET:.0f}% / CPU {CPU_TARGET:.0f}% / RAM {RAM_GB:g} GB", flush=True)
    ts = [threading.Thread(target=gpu_lane), threading.Thread(target=cpu_lane)]
    [t.start() for t in ts]
    try:
        [t.join() for t in ts]
    finally:
        gov.stop = True
    if errors:
        raise RuntimeError("; ".join(errors))
    return {k: out[k] for k in ("area", "frp")}, out["spread"]
