"""Unit tests for backend/prewarm.py (mocks only, no network).

    .venv/bin/python -m pytest backend/test_prewarm.py -q
"""
from __future__ import annotations

import os

os.environ.setdefault("EQUIFIRE_NO_LOAD", "1")

import pandas as pd  # noqa: E402
import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import prewarm as pw  # noqa: E402


def _df(rows):
    return pd.DataFrame(rows, columns=["lat", "lon", "frp"])


def _fire(lat, lon, n, frp):
    return [(lat + 0.01 * (i % 5), lon + 0.01 * (i // 5), frp) for i in range(n)]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def make(feed, warm, **kw):
    clk = Clock()
    p = pw.Prewarmer(feed_loader=lambda: feed, warm_fire=warm, clock=clk, sleep=clk.sleep, wall=clk, **kw)
    p.spacing_s, p.budget_s = 20.0, 600.0
    return p, clk


# ------------------------------------------------------------------ clustering / ranking
def test_cluster_merges_within_15km_and_ranks_by_frp():
    rows = (_fire(-10.0, -55.0, 10, 5.0)            # 50 MW
            + _fire(-10.1, -55.05, 10, 5.0)         # ~12 km away: same fire -> 100 MW total
            + _fire(60.0, 30.0, 10, 20.0)           # 200 MW, outside the baseline area
            + _fire(-20.0, 140.0, 3, 1.0))          # small
    out = pw.cluster_fires(_df(rows), top_n=8, bonus=1.0)
    assert len(out) == 3
    assert [round(f["frp_sum_mw"]) for f in out] == [200, 100, 3]
    assert out[1]["n_detections"] == 20 and out[1]["region"] == "Brazil"
    assert out[0]["region"] is None


def test_cluster_separate_beyond_15km():
    rows = _fire(10.0, 20.0, 6, 10.0) + _fire(10.3, 20.0, 6, 10.0)   # 33 km apart
    assert len(pw.cluster_fires(_df(rows), top_n=8)) == 2


def test_baseline_bonus_changes_rank():
    rows = _fire(61.0, 30.0, 10, 10.0) + _fire(-10.0, -55.0, 10, 8.0)   # 100 vs 80 MW
    assert pw.cluster_fires(_df(rows), 2, bonus=1.0)[0]["region"] is None
    assert pw.cluster_fires(_df(rows), 2, bonus=1.5)[0]["region"] == "Brazil"   # 80 * 1.5 = 120 > 100


def test_cluster_top_n_empty_and_singletons():
    assert pw.cluster_fires(None) == [] and pw.cluster_fires(_df([])) == []
    rows = [(0.0, 0.0, 99.0)]                                       # a single detection is ignored (min 2)
    assert pw.cluster_fires(_df(rows)) == []
    many = sum((_fire(i * 1.0, 10.0, 4, 5.0 + i) for i in range(12)), [])
    assert len(pw.cluster_fires(_df(many), top_n=8)) == 8


def test_cluster_longitude_negative_and_equator_bins():
    rows = _fire(-0.02, -0.02, 6, 3.0)                              # straddles the bin edges around (0, 0)
    out = pw.cluster_fires(_df(rows))
    assert len(out) == 1 and out[0]["n_detections"] == 6


# ------------------------------------------------------------------ cycle: spacing, budget, errors
FEED = _df(sum((_fire(-10 - 2.0 * i, -55.0, 6, 10.0 - i) for i in range(5)), []))


def test_cycle_warms_in_rank_order_with_spacing():
    calls = []
    p, clk = make(FEED, lambda f, r: calls.append((r, clk.t)) or {"steps": {}, "requests": {"open_meteo": 1}})
    cyc = p.run_cycle()
    assert [c[0] for c in calls] == [0, 1, 2, 3, 4] and cyc["warmed"] == 5
    gaps = [b[1] - a[1] for a, b in zip(calls, calls[1:])]
    assert all(g >= 20.0 for g in gaps)
    assert p.requests["open_meteo"] == 5
    assert p.last_cycle is cyc and p.status()["cycles"] == 1


def test_budget_stops_cycle():
    p, clk = make(FEED, lambda f, r: None)
    p.budget_s = 45.0
    cyc = p.run_cycle()
    assert cyc["warmed"] < 5 and "time budget" in cyc["aborted"]


def test_recent_fires_are_skipped_next_cycle():
    n = []
    p, clk = make(FEED, lambda f, r: n.append(r) or {})
    p.run_cycle()
    c2 = p.run_cycle()
    assert len(n) == 5 and c2["skipped"] == 5
    clk.t += 3600
    p.run_cycle()
    assert len(n) == 10


def test_exceptions_do_not_propagate_and_are_recorded():
    def warm(f, r):
        if r == 1:
            raise ValueError("boom")
        return {}
    p, _ = make(FEED, warm)
    cyc = p.run_cycle()
    assert cyc["warmed"] == 4 and cyc["failed"] == 1
    assert any(f.get("status") == "error" for f in cyc["fires"])
    p2 = pw.Prewarmer(feed_loader=lambda: (_ for _ in ()).throw(RuntimeError("feed down")), warm_fire=lambda f, r: {},
                      sleep=lambda s: None)
    assert "feed down" in p2.run_cycle()["aborted"]


# ------------------------------------------------------------------ backoff
def test_429_trips_exponential_backoff_and_blocks_next_cycles():
    def warm(f, r):
        raise pw.Throttled("landscape: 429 Too Many Requests")
    p, clk = make(FEED, warm)
    c1 = p.run_cycle()
    assert c1["failed"] == 1 and "backing off 60" in c1["aborted"] and p.backoff_s == 60
    c2 = p.run_cycle()                                  # inside the backoff window: nothing is called
    assert "backoff" in c2["aborted"] and c2["fires"] == []
    clk.t += 61
    c3 = p.run_cycle()                                  # window over, throttled again -> doubled
    assert p.backoff_s == 120 and c3["failed"] == 1
    for _ in range(8):
        clk.t += 4000
        p.run_cycle()
    assert p.backoff_s == p.backoff_max_s == 1800


def test_http_5xx_exception_text_trips_backoff():
    p, _ = make(FEED, lambda f, r: (_ for _ in ()).throw(RuntimeError("HTTP 504 gateway timeout from overpass")))
    assert p.run_cycle()["failed"] == 1 and p.backoff_s == 60


def test_throttle_detection_regex():
    assert pw._throttle_in(["HTTP 429"]) and pw._throttle_in(["server returned 503"]) and pw._throttle_in(["Read timed out"])
    assert pw._throttle_in(["no fuel data", "ok"]) is None


def test_clean_cycle_clears_backoff():
    state = {"fail": True}

    def warm(f, r):
        if state["fail"]:
            raise pw.Throttled("429")
        return {}
    p, clk = make(FEED, warm)
    p.run_cycle()
    clk.t += 100
    state["fail"] = False
    p.run_cycle()
    assert p.backoff_s == 0.0


# ------------------------------------------------------------------ busy handling
def test_busy_tracker_inflight_and_idle_window():
    t = [100.0]
    b = pw.BusyTracker(idle_s=5.0, clock=lambda: t[0])
    assert not b.busy()
    b.begin()
    assert b.busy() and b.inflight == 1
    b.end()
    assert b.busy()                       # finished 0 s ago
    t[0] += 5.1
    assert not b.busy()
    assert not b.counts("/health") and not b.counts("/prewarm/status") and not b.counts("/app.js")
    assert b.counts("/simulate") and b.counts("/predict/simulate") and b.counts("/brief.pdf")


def test_cycle_waits_while_busy_then_runs():
    p, clk = make(FEED, lambda f, r: {})
    seq = iter([True, True, False] + [False] * 50)
    p.busy.busy = lambda: next(seq)
    cyc = p.run_cycle()
    assert cyc["warmed"] == 5 and clk.t - 1000 >= 2 * 15   # slept 2 x 15 s waiting for idle


def test_cycle_skipped_when_app_stays_busy():
    called = []
    p, _ = make(FEED, lambda f, r: called.append(r) or {})
    p.busy.busy = lambda: True
    cyc = p.run_cycle()
    assert called == [] and "busy" in cyc["aborted"] and p.skipped_busy == 1


def test_middleware_tracks_requests_and_status_endpoint():
    app = FastAPI()

    @app.get("/work")
    def work():
        return {"busy": tracker.busy(), "inflight": tracker.inflight}

    tracker = pw.BusyTracker(idle_s=5.0)
    app.add_middleware(pw._BusyMiddleware, tracker=tracker)
    c = TestClient(app)
    assert c.get("/work").json() == {"busy": True, "inflight": 1}
    assert tracker.inflight == 0 and tracker.busy()      # 5 s idle window started


# ------------------------------------------------------------------ status + enable flags
def test_status_shape_and_endpoint():
    p, _ = make(FEED, lambda f, r: {"steps": {"landscape": {"s": 1.0}}})
    p.run_cycle()
    s = p.status()
    for k in ("state", "cycles", "config", "last_cycle", "recent_fires", "next_cycle_in_s", "backoff_s",
              "requests_total", "busy_now"):
        assert k in s
    assert len(s["recent_fires"]) == 5 and s["last_cycle"]["warmed"] == 5
    old = pw.PREWARMER
    try:
        pw.PREWARMER = p
        app = FastAPI()
        app.include_router(pw.router)
        assert TestClient(app).get("/prewarm/status").json()["cycles"] == 1
        pw.PREWARMER = None
        assert TestClient(app).get("/prewarm/status").json()["enabled"] is False
    finally:
        pw.PREWARMER = old


def test_enabled_flags(monkeypatch):
    for k in ("EQUIFIRE_NO_PREWARM", "EQUIFIRE_LITE", "EQUIFIRE_NO_LOAD", "EQUIFIRE_PREWARM_FORCE"):
        monkeypatch.delenv(k, raising=False)
    assert pw.enabled()
    monkeypatch.setenv("EQUIFIRE_LITE", "1")
    assert not pw.enabled()
    monkeypatch.setenv("EQUIFIRE_PREWARM_FORCE", "1")
    assert pw.enabled()
    monkeypatch.setenv("EQUIFIRE_NO_PREWARM", "1")
    assert not pw.enabled()
    monkeypatch.delenv("EQUIFIRE_NO_PREWARM")
    monkeypatch.delenv("EQUIFIRE_LITE")
    monkeypatch.setenv("EQUIFIRE_NO_LOAD", "1")
    monkeypatch.delenv("EQUIFIRE_PREWARM_FORCE")
    assert not pw.enabled()


def test_install_disabled_registers_only_status(monkeypatch):
    monkeypatch.setenv("EQUIFIRE_NO_PREWARM", "1")
    monkeypatch.setattr(pw, "PREWARMER", None)
    app = FastAPI()
    pw.install(app)
    assert TestClient(app).get("/prewarm/status").json()["state"] == "disabled"


def test_install_enabled_starts_thread_in_lifespan(monkeypatch):
    for k in ("EQUIFIRE_NO_PREWARM", "EQUIFIRE_LITE", "EQUIFIRE_NO_LOAD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("EQUIFIRE_PREWARM_INITIAL_DELAY_S", "3600")
    monkeypatch.setattr(pw, "PREWARMER", None)
    app = FastAPI()
    pw.install(app)
    with TestClient(app) as c:
        s = c.get("/prewarm/status").json()
        assert s["enabled"] and s["state"] in ("sleeping", "idle") and s["config"]["top_n"] == 8
        assert pw.PREWARMER._thread.is_alive()
    assert pw.PREWARMER._stop.is_set()


# ------------------------------------------------------------------ default warm path, mocked app
def test_warm_fire_default_throttle_and_step_order(monkeypatch):
    from backend import live, physics_spread as ph
    det = pd.DataFrame({"lat": [-10.0, -10.01], "lon": [-55.0, -55.01], "frp": [5.0, 6.0],
                        "t": pd.to_datetime(["2026-11-15 10:00"] * 2), "sat": ["N", "N"]})
    monkeypatch.setattr(live, "load_feeds", lambda w="24h", **k: (det, []))
    order = []

    class LP:
        def __init__(self, *a, **k): order.append("lp args %s" % (a,))
        error = None
        def get(self):
            return {"meta": {"layers": {"osm": {"errors": ["Overpass 429 Too Many Requests"], "coverage_pct": 0}}},
                    "fuel_model": 1}
    monkeypatch.setattr(ph, "LandscapeProvider", LP)
    monkeypatch.setattr(ph, "fetch_weather_grid", lambda *a, **k: order.append("weather") or (object(), {"errors": []}))
    with pytest.raises(pw.Throttled):
        pw.warm_fire_default({"lat": -10.0, "lon": -55.0}, 0, sim_top=0)
    assert order[0].endswith(", 50.0)") and "-10.004999" in order[0]      # centroid + half_km = min(80, 30 + 20)


def test_warm_fire_default_full_path(monkeypatch):
    from backend import live, physics_spread as ph
    det = pd.DataFrame({"lat": [-10.0, -10.01], "lon": [-55.0, -55.01], "frp": [5.0, 6.0],
                        "t": pd.to_datetime(["2026-11-15 10:00"] * 2), "sat": ["N", "N"]})
    monkeypatch.setattr(live, "load_feeds", lambda w="24h", **k: (det, []))
    seen = []

    class LP:
        def __init__(self, *a, **k): pass
        error = None
        def get(self):
            return {"meta": {"layers": {"osm": {"cached": True, "coverage_pct": 90}}}, "fuel_model": 1}
    monkeypatch.setattr(ph, "LandscapeProvider", LP)
    monkeypatch.setattr(ph, "fetch_weather_grid", lambda *a, **k: (object(), {"errors": []}))

    class FakeApp:
        class store:
            state = object()

        @staticmethod
        def predict_simulation(**kw):
            seen.append(("predict", kw["calibration"], kw["hours"]))
            return {"frames": [1, 2]}

        @staticmethod
        def simulate_get(**kw):
            seen.append(("simulate", kw["calibration"], kw["hours"], kw["landscape"], kw["weather"]))
            return {"frames": [1]}
    monkeypatch.setattr(pw, "_app_module", lambda: FakeApp)
    out = pw.warm_fire_default({"lat": -10.0, "lon": -55.0}, 0, sim_top=3)
    assert not out["errors"] and set(out["steps"]) == {"landscape", "weather", "predict_simulate", "simulate"}
    assert seen == [("predict", "off", 72), ("simulate", "off", 72, "on", "grid")]
    out2 = pw.warm_fire_default({"lat": -10.0, "lon": -55.0}, 5, sim_top=3)      # rank beyond sim_top: inputs only
    assert set(out2["steps"]) == {"landscape", "weather"}


def test_throttled_record_keeps_partial_steps_and_requests():
    def warm(f, r):
        raise pw.Throttled("landscape: HTTP 429", partial={"steps": {"landscape": {"s": 3.0}}, "requests": {"overpass": 2}})
    p, _ = make(FEED, warm)
    cyc = p.run_cycle()
    rec = cyc["fires"][0]
    assert rec["status"] == "throttled" and rec["steps"]["landscape"]["s"] == 3.0
    assert p.requests["overpass"] == 2
