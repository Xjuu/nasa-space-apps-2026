"""Live NRT anomaly alerts on synthetic feeds and a synthetic baseline (no network, no archive).

    .venv/bin/python -m pytest backend/test_live.py -q
    LIVE=1 .venv/bin/python -m pytest backend/test_live.py -q    # also the real FIRMS feeds + real baseline
"""
from __future__ import annotations

import datetime as dt
import os
import xml.etree.ElementTree as ET

os.environ.setdefault("EQUIFIRE_NO_LOAD", "1")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import app as appmod  # noqa: E402
from backend import live  # noqa: E402

END = dt.datetime(2026, 11, 14, 12, 0)
HOT = (-10.0, -55.0)      # routinely burns ~4 cell-days in this week; live: 40 -> critical
NORMAL = (-12.0, -50.0)   # routinely ~10; live: 10 -> no alert
NEW = (30.0, -100.0)      # never burned in week 44 (only in 2 years); live 12 -> out of season
OUTSIDE = (50.0, 10.0)    # not in the coverage mask -> no baseline
HEADER = "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight\n"


def cell(lat, lon):
    return int(np.floor((lon + 180) / live.GRID)), int(np.floor((lat + 90) / live.GRID))


def week_now():
    mid = END - dt.timedelta(hours=84)
    return live.week_of((np.datetime64(mid.date()) - live.EPOCH).astype(int))


def synthetic_baseline() -> live.Baseline:
    w = week_now()
    rows = []
    for res in live.RES:
        for (lat, lon), per_year, years in ((HOT, 4.0, live.YEARS), (NORMAL, 10.0, live.YEARS),
                                            (NEW, 3.0, [2010, 2020])):
            rid = int(live.region_id(*cell(lat, lon), res))
            for y in live.YEARS:
                src = "reconstructed" if y < 2012 else "viirs"
                c = per_year + (y % 3 - 1) if y in years else 0.0   # some year-to-year spread
                for wk in (w - 1, w, w + 1):
                    if c > 0:
                        rows.append((res, rid, y, wk, c, src))
            if (lat, lon) == NEW:   # NEW region has archive fires in another season -> covered
                rows.append((res, rid, 2015, (w + 20) % 52, 5.0, "viirs"))
    df = pd.DataFrame(rows, columns=["res", "rid", "year", "week", "cnt", "src"])
    df["src"] = df["src"].astype("category")
    mask = np.zeros((live.NX, live.NY), bool)
    for lat, lon in (HOT, NORMAL, NEW):
        gx, gy = cell(lat, lon)
        mask[gx - 10:gx + 10, gy - 10:gy + 10] = True
    meta = {"years": live.YEARS, "reconstructed_years": [2008, 2009, 2010, 2011], "recon_scale_k": 1.3}
    return live.Baseline(df, meta, mask)


def feed_csv(sat_code: str, groups) -> bytes:
    lines = [HEADER]
    for (lat, lon), ncell in groups:
        for i in range(ncell):
            d = END - dt.timedelta(days=i % 6, hours=1)
            la, lo = lat + 0.051 * (i // 6) + 0.001, lon + 0.001
            lines.append(f"{la:.5f},{lo:.5f},330,0.4,0.4,{d:%Y-%m-%d},{d:%H%M},{sat_code},VIIRS,n,2.0NRT,290,"
                         f"{5 + i},D\n")
    return "".join(lines).encode()


GROUPS = [(HOT, 40), (NORMAL, 10), (NEW, 12), (OUTSIDE, 30)]


def fake_fetch(url: str) -> bytes:
    if "J1_VIIRS" in url:
        return feed_csv("N20", GROUPS)
    if "J2_VIIRS" in url:
        return feed_csv("N21", GROUPS)
    if "SUOMI" in url:
        return HEADER.encode()          # empty S-NPP feed, as FIRMS serves it at times
    if "MODIS" in url:
        return b"latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,instrument,confidence\n"
    raise AssertionError(url)


@pytest.fixture()
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(live, "_download", fake_fetch)
    live._live_cache.clear()
    base = synthetic_baseline()
    live.set_baseline(base)
    yield base
    live.set_baseline(None)
    live._live_cache.clear()


def by_point(regs, lat, lon, res=1.0):
    rid = int(live.region_id(*cell(lat, lon), res))
    return next(r for r in regs if r["rid"] == rid)


def test_parse_and_empty_feed(setup):
    df, info = live.load_feeds("7d")
    i = {x["feed"]: x for x in info}
    assert i["N"]["empty"] and i["MODIS"]["empty"] and i["N20"]["detections"] == 92
    assert df["gx"].dtype == np.int64 and df["t"].notna().all()


def test_levels_and_no_baseline(setup):
    st = live.live_state(1.0)
    regs = st["regions"]
    assert st["obs_satellite"] == "N20"
    hot = by_point(regs, *HOT)
    assert hot["obs"] == 40 and hot["level"] == "critical" and hot["z"] >= 5 and not hot["out_of_season"]
    assert hot["years_used"] == 18 and hot["years_reconstructed"] == 4
    assert abs(hot["mean"] - 4.0) < 0.5 and hot["ratio"] > 5
    assert by_point(regs, *NORMAL)["level"] == "none"
    new = by_point(regs, *NEW)
    assert new["out_of_season"] and new["level"] in ("warning", "critical") and new["years_burned"] == 2
    out = by_point(regs, *OUTSIDE)
    assert out["baseline"] == "none" and out["z"] is None and out["level"] == "none"
    zs = [r["z"] for r in regs if r["z"] is not None]
    assert zs == sorted(zs, reverse=True)


def test_z_floor():
    assert live.z_score(0, 0, 0) == 0
    assert live.z_score(10, 0, 0) == 10          # floor sqrt(0)+1 = 1
    assert abs(live.z_score(130, 100, 1) - 1.0) < 1e-9   # floor 0.3*mean = 30


def test_endpoints(setup):
    c = TestClient(appmod.app)
    live.live_state(1.0)
    live.live_state(0.25)
    r = c.get("/live/summary")
    assert r.status_code == 200
    s = r.json()
    assert s["alerts"]["critical"] >= 1 and s["regions_no_baseline"] >= 1 and len(s["feeds"]) == 4
    r = c.get("/live/alerts", params={"level": "warning"})
    fc = r.json()
    assert fc["type"] == "FeatureCollection" and len(fc["features"]) == 2
    p = fc["features"][0]["properties"]
    assert p["level"] == "critical" and "headline" in p and fc["meta"]["obs_satellite"] == "N20"
    assert fc["features"][0]["geometry"]["type"] == "Polygon"
    r = c.get("/live/alerts", params={"bbox": "-60,-15,-45,-5", "level": "watch"})
    assert all(-60 <= f["properties"]["lon"] <= -45 for f in r.json()["features"])
    assert len(c.get("/live/alerts", params={"res": 0.25}).json()["features"]) >= 2
    assert c.get("/live/alerts", params={"level": "bogus"}).status_code == 400
    assert c.get("/live/alerts", params={"res": 0.5}).status_code == 400
    nb = c.get("/live/alerts", params={"include_no_baseline": True}).json()["features"]
    assert any(f["properties"]["baseline"] == "none" for f in nb)
    r = c.get("/live/alerts.cap")
    assert r.status_code == 200 and "cap+xml" in r.headers["content-type"]
    root = ET.fromstring(r.content)
    ns = {"c": live.CAP_NS}
    infos = root.findall("c:info", ns)
    assert len(infos) == 2 and root.find("c:status", ns).text == "Actual"
    assert {i.find("c:severity", ns).text for i in infos} <= {"Extreme", "Severe"}
    assert all(len(i.find("c:area/c:polygon", ns).text.split()) == 5 for i in infos)


def test_warming_up_returns_503(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "CACHE_DIR", tmp_path)
    live._live_cache.clear()
    monkeypatch.setattr(live, "get_live", lambda res: None)
    r = TestClient(appmod.app).get("/live/summary")
    assert r.status_code == 503


@pytest.mark.skipif(os.environ.get("LIVE") != "1", reason="set LIVE=1 to hit the real FIRMS feeds")
def test_real_feed():
    live._live_cache.clear()
    live.set_baseline(None)
    st = live.live_state(1.0)
    s = live.summary(st)
    assert s["obs_satellite"] in ("N20", "N", "N21") and s["regions_active"] > 100
    assert any(r["baseline"] == "ok" for r in st["regions"])
