"""Background pre-warmer: keep the slow, cacheable inputs of the strongest live fires warm.

A daemon thread (started with the app, see install()) wakes every EQUIFIRE_PREWARM_INTERVAL_S (default 900 s),
loads the live FIRMS NRT feed (backend.live.load_feeds('24h'), which also refreshes it), clusters detections
within 15 km, ranks the clusters by FRP sum (clusters inside the five-country baseline area get a x1.5 bonus,
any country is eligible) and warms the top N (default 8), ONE AT A TIME, >= 20 s apart:

  1. landscape  equifire.landscape.get_landscape through physics_spread.LandscapeProvider with the exact
                arguments /simulate and /predict/simulate use (half_km = min(80, radius_km + 20) = 50, 250 m,
                date = start hour, centroid of the ignition detections) -> cache/landscape/*.npz
  2. weather    physics_spread.fetch_weather_grid (5x5 Open-Meteo grid, same start/span as the simulation)
                -> cache/fireweather/http/*
  3. exposure   OPTIONAL, EQUIFIRE_PREWARM_EXPOSURE=1, last, only when this cycle saw no throttling:
                OSM assets + buildings for the footprint of the step-4 simulation -> cache/exposure/*
  4. simulate   top EQUIFIRE_PREWARM_SIM_TOP fires (default 1): the app's own endpoint functions are called
                in-process (/predict/simulate when the archive is loaded, then /simulate), calibration off,
                72 h, so their in-memory result caches (and every disk cache above) are warm.

Politeness / safety: skips a cycle (retries 15 s later) when the app handled a request in the last 5 s or one is
in flight; stops on HTTP 429/5xx/timeouts and backs off exponentially (60 s .. 30 min) across cycles; global time
budget per cycle (default 600 s); never raises into the app; one thread; status memory is bounded.
GET /prewarm/status reports everything.

Env: EQUIFIRE_NO_PREWARM=1 (off; also off with EQUIFIRE_LITE=1 / EQUIFIRE_NO_LOAD=1), EQUIFIRE_PREWARM_FORCE=1
(run even in those modes, for measuring), EQUIFIRE_PREWARM_N, _INTERVAL_S, _SPACING_S, _BUDGET_S, _SIM_TOP,
_EXPOSURE, _IDLE_S, _REWARM_MIN, _INITIAL_DELAY_S.
"""
from __future__ import annotations

import contextlib
import importlib
import math
import os
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone

import numpy as np
from fastapi import APIRouter

CLUSTER_KM = 15.0
SIM_HOURS = 72
RADIUS_KM = 30.0                      # same default as /simulate and /predict/simulate
BASELINE_BONUS = 1.5
# approximate boxes (minlon, minlat, maxlon, maxlat) of the five-country baseline area
BASELINE_BOXES = {
    "Brazil": (-74.0, -34.0, -34.0, 5.5), "Australia": (112.0, -44.0, 154.0, -10.0),
    "India": (68.0, 6.0, 98.0, 36.0), "Canada": (-141.0, 41.7, -52.0, 83.5),
    "United_States": (-125.0, 24.5, -66.9, 49.5),
}
THROTTLE_RE = re.compile(r"\b429\b|too many requests|rate.?limit|\b5\d\d\b|timed? ?out|timeout|overloaded|"
                         r"gateway|service unavailable|connection (reset|refused)", re.I)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


def enabled() -> bool:
    if os.environ.get("EQUIFIRE_NO_PREWARM") == "1":
        return False
    if os.environ.get("EQUIFIRE_PREWARM_FORCE") == "1":
        return True
    return os.environ.get("EQUIFIRE_LITE") != "1" and os.environ.get("EQUIFIRE_NO_LOAD") != "1"


# ------------------------------------------------------------------ clustering + ranking
def in_baseline(lat: float, lon: float) -> str | None:
    for name, (x0, y0, x1, y1) in BASELINE_BOXES.items():
        if x0 <= lon <= x1 and y0 <= lat <= y1:
            return name
    return None


def _km(lat1, lon1, lat2, lon2) -> float:
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742.0 * math.asin(min(1.0, math.sqrt(a)))


def cluster_fires(df, top_n: int = 8, cluster_km: float = CLUSTER_KM, min_detections: int = 2,
                  bonus: float = BASELINE_BONUS) -> list[dict]:
    """Distinct fires from FIRMS NRT detections (df needs lat, lon, frp). Detections are binned to 0.1 deg
    cells, bin centroids closer than cluster_km are merged (union-find), clusters are ranked by
    FRP sum x (bonus inside the baseline area). Returns the top_n dicts, strongest first."""
    if df is None or len(df) == 0:
        return []
    lat = np.asarray(df["lat"], float)
    lon = np.asarray(df["lon"], float)
    frp = np.nan_to_num(np.asarray(df["frp"], float), nan=0.0)
    ok = np.isfinite(lat) & np.isfinite(lon)
    lat, lon, frp = lat[ok], lon[ok], frp[ok]
    if lat.size == 0:
        return []
    bx = np.floor(lon / 0.1).astype(np.int64)
    by = np.floor(lat / 0.1).astype(np.int64)
    key = (by + 1000) * 100000 + (bx + 2000)
    uniq, inv = np.unique(key, return_inverse=True)
    n = len(uniq)
    cnt = np.bincount(inv, minlength=n).astype(float)
    sf = np.bincount(inv, weights=frp, minlength=n)
    w = np.maximum(frp, 1e-3)
    sw = np.bincount(inv, weights=w, minlength=n)
    clat = np.bincount(inv, weights=lat * w, minlength=n) / sw
    clon = np.bincount(inv, weights=lon * w, minlength=n) / sw
    ixy = {(int(k // 100000) - 1000, int(k % 100000) - 2000): i for i, k in enumerate(uniq)}
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    reach = max(1, int(math.ceil(cluster_km / 11.1)) + 1)   # bins to look around (>= cluster_km at any latitude <= 60)
    for (y, x), i in ixy.items():
        for dy in range(0, reach + 1):
            for dx in range(-reach, reach + 1):
                if dy == 0 and dx <= 0:
                    continue
                j = ixy.get((y + dy, x + dx))
                if j is not None and _km(clat[i], clon[i], clat[j], clon[j]) <= cluster_km:
                    a, b = find(i), find(j)
                    if a != b:
                        parent[a] = b
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    out = []
    for idx in groups.values():
        idx = np.asarray(idx)
        nd = int(cnt[idx].sum())
        if nd < min_detections:
            continue
        f = float(sf[idx].sum())
        # chained clusters can be hundreds of km wide (savanna season): centre on the strongest bin and the bins
        # within cluster_km of it, so the point is on a real fire (a click there finds the detections)
        pk = idx[int(np.argmax(sf[idx]))]
        near = np.array([k for k in idx if _km(clat[pk], clon[pk], clat[k], clon[k]) <= cluster_km])
        ww = np.maximum(sf[near], 1e-3)
        la, lo = float(np.average(clat[near], weights=ww)), float(np.average(clon[near], weights=ww))
        region = in_baseline(la, lo)
        out.append({"lat": round(la, 4), "lon": round(lo, 4), "frp_sum_mw": round(f, 1), "n_detections": nd,
                    "region": region, "extent_bins": int(len(idx)), "score": f * (bonus if region else 1.0)})
    out.sort(key=lambda d: -d["score"])
    return out[:max(0, int(top_n))]


# ------------------------------------------------------------------ "is a user request in flight?"
class BusyTracker:
    """Pure-ASGI middleware state. Health/status polls and static files do not count as user work."""

    def __init__(self, idle_s: float = 5.0, clock=time.monotonic):
        self.idle_s, self.clock = idle_s, clock
        self.inflight = 0
        self.last_end = -1e9
        self._lock = threading.Lock()

    @staticmethod
    def counts(path: str) -> bool:
        if path in ("/health", "/prewarm/status", "/", "/favicon.ico"):
            return False
        last = path.rsplit("/", 1)[-1]
        return "." not in last or last.endswith((".json", ".pdf", ".html", ".cap"))

    def begin(self):
        with self._lock:
            self.inflight += 1

    def end(self):
        with self._lock:
            self.inflight = max(0, self.inflight - 1)
            self.last_end = self.clock()

    def busy(self) -> bool:
        with self._lock:
            return self.inflight > 0 or (self.clock() - self.last_end) < self.idle_s

    def middleware(self, app):
        async def asgi(scope, receive, send):
            if scope.get("type") == "http" and self.counts(scope.get("path", "")):
                self.begin()
                try:
                    await app(scope, receive, send)
                finally:
                    self.end()
            else:
                await app(scope, receive, send)
        return asgi


# ------------------------------------------------------------------ the warmer
class Throttled(Exception):
    def __init__(self, msg: str = "", partial: dict | None = None):
        super().__init__(msg)
        self.partial = partial or {}


def _throttle_in(errors) -> str | None:
    for e in errors or []:
        if THROTTLE_RE.search(str(e)):
            return str(e)[:160]
    return None


class Prewarmer:
    def __init__(self, busy: BusyTracker | None = None, feed_loader=None, warm_fire=None,
                 clock=time.monotonic, sleep=time.sleep, wall=time.time):
        self.busy = busy or BusyTracker()
        self.feed_loader = feed_loader or self._default_feed
        self.warm_fire = warm_fire or self._default_warm
        self.clock, self.sleep, self.wall = clock, sleep, wall
        self.n = int(_env_float("EQUIFIRE_PREWARM_N", 8))
        self.interval_s = _env_float("EQUIFIRE_PREWARM_INTERVAL_S", 900)
        self.spacing_s = _env_float("EQUIFIRE_PREWARM_SPACING_S", 20)
        self.budget_s = _env_float("EQUIFIRE_PREWARM_BUDGET_S", 600)
        self.sim_top = int(_env_float("EQUIFIRE_PREWARM_SIM_TOP", 1))
        self.exposure = os.environ.get("EQUIFIRE_PREWARM_EXPOSURE") == "1"
        self.rewarm_s = _env_float("EQUIFIRE_PREWARM_REWARM_MIN", 45) * 60
        self.busy_retry_s = 15.0
        self.backoff_base_s, self.backoff_max_s = 60.0, 1800.0
        self.backoff_s = 0.0
        self.backoff_until = 0.0          # monotonic
        self.state = "idle"               # idle | sleeping | running | backoff | disabled | stopped
        self.cycles = 0
        self.skipped_busy = 0
        self.last_cycle: dict | None = None
        self.fires: deque = deque(maxlen=64)   # per-fire records of recent cycles
        self.warmed: dict[tuple, float] = {}   # (lat/0.1, lon/0.1) -> wall time warmed
        self.next_cycle_wall: float | None = None
        self.requests = {"overpass": 0, "open_meteo": 0, "errors": 0}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # -- feed + per-fire work (defaults call the app; tests inject fakes)
    @staticmethod
    def _default_feed():
        from . import live
        df, _info = live.load_feeds("24h")
        return df

    def _default_warm(self, fire: dict, rank: int) -> dict:
        return warm_fire_default(fire, rank, sim_top=self.sim_top, exposure=self.exposure,
                                 exposure_ok=lambda: self.backoff_s == 0.0)

    # -- one cycle
    def run_cycle(self) -> dict:
        t0 = self.clock()
        cyc = {"started_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "fires": [],
               "warmed": 0, "failed": 0, "skipped": 0, "aborted": None, "feed_detections": None}
        self.state = "running"
        try:
            if self.clock() < self.backoff_until:
                cyc["aborted"] = f"backoff ({self.backoff_until - self.clock():.0f} s left)"
                return cyc
            df = self.feed_loader()
            cyc["feed_detections"] = 0 if df is None else int(len(df))
            fires = cluster_fires(df, self.n)
            for rank, fire in enumerate(fires):
                if self._stop.is_set():
                    cyc["aborted"] = "stopped"
                    break
                if self.clock() - t0 > self.budget_s:
                    cyc["aborted"] = f"time budget {self.budget_s:.0f} s reached"
                    break
                bin_ = (round(fire["lat"] / 0.1), round(fire["lon"] / 0.1))
                if self.wall() - self.warmed.get(bin_, -1e12) < self.rewarm_s:
                    cyc["skipped"] += 1
                    self._record(cyc, {**fire, "rank": rank, "status": "skipped_recent"})
                    continue
                if not self._wait_idle(t0):
                    cyc["aborted"] = "app busy: user requests have priority"
                    self.skipped_busy += 1
                    break
                tf = self.clock()
                rec = {**fire, "rank": rank}
                try:
                    res = self.warm_fire(fire, rank) or {}
                    rec.update(res)
                    rec["status"] = "ok" if not res.get("errors") else "partial"
                    self.warmed[bin_] = self.wall()
                    cyc["warmed"] += 1
                except Throttled as e:
                    rec.update(e.partial)
                    rec.update(status="throttled", errors=[str(e)])
                    cyc["failed"] += 1
                    self._trip_backoff(cyc, str(e))
                except Exception as e:  # noqa: BLE001 - never crash the app
                    rec.update(status="error", errors=[f"{type(e).__name__}: {str(e)[:200]}"])
                    cyc["failed"] += 1
                    self.requests["errors"] += 1
                    if THROTTLE_RE.search(str(e)):
                        self._trip_backoff(cyc, str(e))
                rec["seconds"] = round(self.clock() - tf, 1)
                for k, v in (rec.get("requests") or {}).items():
                    self.requests[k] = self.requests.get(k, 0) + int(v)
                self._record(cyc, rec)
                if cyc["aborted"]:
                    break
                if rank < len(fires) - 1:
                    self._sleep_interruptible(self.spacing_s)
            else:
                if self.backoff_s and not cyc["failed"]:
                    self.backoff_s = 0.0        # a clean cycle clears the backoff
        except Exception as e:  # noqa: BLE001
            cyc["aborted"] = f"{type(e).__name__}: {str(e)[:200]}"
        finally:
            cyc["seconds"] = round(self.clock() - t0, 1)
            self.cycles += 1
            self.last_cycle = cyc
            print(f"[prewarm] cycle {self.cycles}: {cyc['warmed']} warmed, {cyc['skipped']} recent, "
                  f"{cyc['failed']} failed, {cyc['seconds']} s, feed {cyc['feed_detections']} detections"
                  + (f", aborted: {cyc['aborted']}" if cyc["aborted"] else ""), flush=True)
        return cyc

    def _record(self, cyc, rec):
        cyc["fires"].append({k: v for k, v in rec.items() if k != "score"})
        self.fires.append({**cyc["fires"][-1], "cycle": self.cycles + 1,
                           "at_utc": datetime.now(timezone.utc).strftime("%H:%M:%SZ")})

    def _trip_backoff(self, cyc, why: str):
        self.backoff_s = min(self.backoff_max_s, self.backoff_s * 2 if self.backoff_s else self.backoff_base_s)
        self.backoff_until = self.clock() + self.backoff_s
        cyc["aborted"] = f"throttled/unavailable ({why[:80]}): backing off {self.backoff_s:.0f} s"

    def _wait_idle(self, t0) -> bool:
        """Wait (bounded) for the app to be idle. False if it stays busy past the budget."""
        waited = 0.0
        while self.busy.busy():
            if self._stop.is_set() or waited >= 60.0 or self.clock() - t0 > self.budget_s:
                return False
            self._sleep_interruptible(self.busy_retry_s)
            waited += self.busy_retry_s
        return True

    def _sleep_interruptible(self, s: float):
        if self._stop.is_set():
            return
        self.sleep(s) if self.sleep is not time.sleep else self._stop.wait(s)

    # -- thread
    def start(self, initial_delay_s: float | None = None):
        if self._thread is not None and self._thread.is_alive():
            return
        d = _env_float("EQUIFIRE_PREWARM_INITIAL_DELAY_S", 90) if initial_delay_s is None else initial_delay_s
        self._thread = threading.Thread(target=self._loop, args=(d,), name="equifire-prewarm", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self, initial_delay_s: float):
        self.state = "sleeping"
        self.next_cycle_wall = self.wall() + initial_delay_s
        self._stop.wait(initial_delay_s)
        while not self._stop.is_set():
            try:
                if self._data_ready():
                    self.run_cycle()
                else:
                    print("[prewarm] archive still loading, waiting", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"[prewarm] loop error {type(e).__name__}: {e}", flush=True)
            wait = self.interval_s
            self.state = "sleeping"
            self.next_cycle_wall = self.wall() + wait
            self._stop.wait(wait)
        self.state = "stopped"

    @staticmethod
    def _data_ready() -> bool:
        if os.environ.get("EQUIFIRE_PREWARM_FORCE") == "1":
            return True
        try:
            appmod = _app_module()
            return appmod.store.state is not None
        except Exception:  # noqa: BLE001
            return False

    # -- status
    def status(self) -> dict:
        now = self.wall()
        return {
            "enabled": True, "state": self.state, "cycles": self.cycles, "skipped_busy_cycles": self.skipped_busy,
            "config": {"top_n": self.n, "interval_s": self.interval_s, "spacing_s": self.spacing_s,
                       "budget_s": self.budget_s, "sim_top": self.sim_top, "exposure": self.exposure,
                       "idle_s": self.busy.idle_s, "rewarm_min": self.rewarm_s / 60},
            "busy_now": self.busy.busy(), "inflight_user_requests": self.busy.inflight,
            "backoff_s": self.backoff_s,
            "backoff_remaining_s": max(0.0, round(self.backoff_until - self.clock(), 1)),
            "next_cycle_utc": None if not self.next_cycle_wall else
            datetime.fromtimestamp(self.next_cycle_wall, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "next_cycle_in_s": None if not self.next_cycle_wall else max(0, round(self.next_cycle_wall - now)),
            "requests_total": dict(self.requests),
            "last_cycle": self.last_cycle,
            "recent_fires": list(self.fires)[-16:],
            "warmed_areas": len(self.warmed),
            "notes": ["Disk caches (landscape, weather, exposure) persist; in-memory result caches (5-30 min TTL, "
                      "exact lat/lon key) only help an identical request."],
        }


# ------------------------------------------------------------------ default per-fire warm (calls the app)
def _app_module():
    pkg = __package__ or "backend"
    return importlib.import_module(pkg + ".app")


class _CountingGet:
    """Count outbound HTTP requests (requests.Session.send) made by this thread during one fire."""

    def __init__(self):
        self.counts = {"overpass": 0, "open_meteo": 0, "other": 0}
        self._tid = threading.get_ident()
        self._orig = None

    def __enter__(self):
        import requests
        self._orig = requests.Session.send
        me = self

        def send(sess, req, **kw):
            host = (req.url or "").split("/")[2] if "//" in (req.url or "") else ""
            k = ("overpass" if "overpass" in host else "open_meteo" if "open-meteo" in host else "other")
            me.counts[k] += 1
            return me._orig(sess, req, **kw)
        requests.Session.send = send
        return self

    def __exit__(self, *a):
        import requests
        requests.Session.send = self._orig


def warm_fire_default(fire: dict, rank: int, sim_top: int = 1, exposure: bool = False, exposure_ok=lambda: True,
                      hours: int = SIM_HOURS) -> dict:
    """Warm one fire with the same arguments the user-facing endpoints use. See the module docstring."""
    import pandas as pd
    from . import physics_spread as ph
    appmod = _app_module()
    lat, lon = fire["lat"], fire["lon"]
    t0 = ph._parse_start(None)                       # = now floored to the hour (what the UI sends by default)
    out: dict = {"steps": {}, "errors": []}

    def step(name, fn):
        ts = time.perf_counter()
        try:
            r = fn() or {}
        except Exception as e:  # noqa: BLE001
            r = {"error": f"{type(e).__name__}: {str(e)[:160]}"}
        out["steps"][name] = {"s": round(time.perf_counter() - ts, 1), **r}
        hit = _throttle_in(r.get("throttle_errors") or ([r["error"]] if r.get("error") else []))
        for e in ([r["error"]] if r.get("error") else []) + list(r.get("errors") or []):
            out["errors"].append(f"{name}: {str(e)[:160]}")
        if hit:
            raise Throttled(f"{name}: {hit}", partial=out)

    from . import live as _live
    df, _ = _live.load_feeds("24h")
    ign = ph.ignition_live(lat, lon, df, RADIUS_KM)
    if ign is None:
        return {"errors": ["no live detections at the cluster centre any more"], "steps": {}}
    clat, clon = float(np.mean(ign["lats"])), float(np.mean(ign["lons"]))
    out["centroid"] = [round(clat, 5), round(clon, 5)]
    counter = _CountingGet()
    out["requests"] = counter.counts          # live dict: also filled when a step raises Throttled
    with counter:
        # (1) landscape: identical to forecast(): LandscapeProvider(clat, clon, min(80, radius+20), date=w_start)
        def landscape():
            lp = ph.LandscapeProvider(clat, clon, min(80.0, RADIUS_KM + 20), date=t0)
            d = lp.get()
            if d is None:
                return {"error": lp.error or "no landscape"}
            layers = (d.get("meta") or {}).get("layers", {})
            errs = [e for lay in layers.values() for e in (lay.get("errors") or [])]
            osm = (layers.get("osm") or {}).get("errors") or []
            return {"cached": all(l.get("cached") for l in layers.values()) if layers else None,
                    "osm_coverage_pct": (layers.get("osm") or {}).get("coverage_pct"), "errors": errs[:3] or None,
                    "throttle_errors": [str(e) for e in osm]}

        # (2) 5x5 weather grid, same start / span as the simulation (calibration off => w_start = t0)
        def weather():
            wg, info = ph.fetch_weather_grid(clat, clon, t0, int(hours))
            return {"ok": wg is not None, "error": "; ".join(info.get("errors", [])) or None}

        step("landscape", landscape)
        step("weather", weather)
        sim = {}
        if rank < sim_top:
            # (4) the app's own endpoint functions: same cache keys as GET /simulate and /predict/simulate
            if appmod.store.state is not None:
                step("predict_simulate", lambda: _call_predict(appmod, lat, lon, hours))
            step("simulate", lambda: _call_simulate(appmod, clat, clon, hours, sim))
        if exposure and sim.get("result") is not None and exposure_ok() and not out["errors"]:
            def expo():
                from . import exposure as ex
                _a, _b, _p, cov = ex.collect_assets(sim["result"], buildings=True, population=False)
                return {"complete": cov.get("complete"), "errors": [str(v.get("errors")) for v in cov["osm"].values()
                                                                    if v.get("errors")] or None}
            step("exposure", expo)
    out["requests"] = dict(counter.counts)
    return out


def _call_predict(appmod, lat, lon, hours):
    r = appmod.predict_simulation(lat=lat, lon=lon, start=None, hours=hours, step_min=30, ignition="live",
                                  fuel="auto", radius_km=RADIUS_KM, calibration="off")
    return {"frames": len(r.get("frames", []))}


def _call_simulate(appmod, lat, lon, hours, holder):
    r = appmod.simulate_get(lat=lat, lon=lon, start=None, hours=hours, step_min=60, ignition="live", fuel="auto",
                            envelope=True, frames=True, radius_km=RADIUS_KM, calibration="off", weather="grid",
                            goes="off", fwi=True, landscape="on")
    holder["result"] = r
    return {"frames": len(r.get("frames", []))}


# ------------------------------------------------------------------ app wiring
PREWARMER: Prewarmer | None = None
router = APIRouter()


@router.get("/prewarm/status")
def prewarm_status() -> dict:
    if PREWARMER is None:
        return {"enabled": False, "state": "disabled",
                "reason": "EQUIFIRE_NO_PREWARM=1, or EQUIFIRE_LITE / EQUIFIRE_NO_LOAD mode"}
    return PREWARMER.status()


def install(app) -> None:
    """Add GET /prewarm/status and (unless disabled) the busy-tracking middleware and the background thread."""
    global PREWARMER
    app.include_router(router)
    if not enabled():
        return
    PREWARMER = Prewarmer()
    app.add_middleware(_BusyMiddleware, tracker=PREWARMER.busy)
    inner = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(a):
        try:
            PREWARMER.start()
        except Exception as e:  # noqa: BLE001
            print(f"[prewarm] could not start: {e}", flush=True)
        try:
            async with inner(a) as state:
                yield state
        finally:
            PREWARMER.stop()

    app.router.lifespan_context = lifespan


class _BusyMiddleware:
    def __init__(self, app, tracker: BusyTracker):
        self._call = tracker.middleware(app)

    async def __call__(self, scope, receive, send):
        await self._call(scope, receive, send)
