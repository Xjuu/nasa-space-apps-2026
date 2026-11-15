"""Detection latency measured by OUR poller (idea #17).

Every POLL_S seconds the poller downloads the public FIRMS near-real-time 24 h CSVs and records, for each detection
it has not seen before, the time we first saw it (`first_seen`). Latency = first_seen - acquisition time (acq_date+acq_time).

What this is: how long after the satellite observed a fire it appeared in the public feed, as seen by this poller, with a
resolution of one polling interval. What this is not: FIRMS' internal processing time (observations that appeared before our
first poll are excluded, and anything that appeared between two polls is only bounded), and not the latency of a direct-broadcast
receiver. No claim about FIRMS' official targets is made here.

Endpoints: GET /latency/stats
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import APIRouter

from . import live as _live

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "cache" / "latency" / "state.json"
POLL_S = 300
WINDOW = "24h"
MAX_RECORDS = 30000
_lock = threading.Lock()
_state = {"started_utc": None, "polls": 0, "last_poll_utc": None, "last_error": None, "poll_interval_s": POLL_S,
          "records": [], "seen": {}, "baseline_done": False}
_thread = None


def _load():
    try:
        s = json.loads(STATE.read_text())
        if isinstance(s, dict) and "records" in s:
            _state.update(s)
            _state["poll_interval_s"] = POLL_S
    except Exception:  # noqa: BLE001
        pass


def _save():
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_state))
        tmp.replace(STATE)
    except Exception:  # noqa: BLE001
        pass


def poll_once(fetch=None, now: dt.datetime | None = None) -> int:
    """One polling round. Returns the number of new detections recorded."""
    now = now or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    new = 0
    fetch = fetch or _live._download
    errs = []
    baseline = not _state["baseline_done"]
    seen = _state["seen"]
    cutoff = (now - dt.timedelta(hours=48)).isoformat()
    for name in _live.FEEDS:
        try:
            raw = fetch(f"{_live.BASE_URL}/{_live.FEEDS[name][0].format(w=WINDOW)}")
            df = _live.parse_feed(raw, name)
        except Exception as e:  # noqa: BLE001
            errs.append(f"{name}: {type(e).__name__}")
            continue
        if df.empty:
            continue
        key = (df["sat"].astype(str) + "|" + df["lat"].round(4).astype(str) + "|" + df["lon"].round(4).astype(str) + "|"
               + pd.to_datetime(df["t"]).dt.strftime("%Y%m%d%H%M"))
        for k, t, sat in zip(key.tolist(), pd.to_datetime(df["t"]).tolist(), df["sat"].astype(str).tolist()):
            if k in seen:
                continue
            seen[k] = now.isoformat()
            if baseline:
                continue          # present at our first poll: first_seen unknown, excluded from statistics
            lag = (now - t.to_pydatetime()).total_seconds() / 60.0
            _state["records"].append([sat, round(lag, 1), now.isoformat(), name])
            new += 1
    if baseline and not errs:
        _state["baseline_done"] = True
    for k in [k for k, v in seen.items() if v < cutoff]:
        del seen[k]
    if len(_state["records"]) > MAX_RECORDS:
        _state["records"] = _state["records"][-MAX_RECORDS:]
    _state["polls"] += 1
    _state["last_poll_utc"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    _state["last_error"] = "; ".join(errs) or None
    return new


def stats() -> dict:
    with _lock:
        recs = list(_state["records"])
        out = {k: _state[k] for k in ("started_utc", "polls", "last_poll_utc", "last_error", "poll_interval_s", "baseline_done")}
    out["n_detections"] = len(recs)
    by: dict[str, list[float]] = {}
    for sat, lag, _, feed in recs:
        by.setdefault(f"{feed}", []).append(float(lag))
    per = {}
    for feed, v in sorted(by.items()):
        a = np.array(v)
        per[feed] = {"n": int(a.size), "median_min": round(float(np.median(a)), 1), "p90_min": round(float(np.percentile(a, 90)), 1),
                     "min_min": round(float(a.min()), 1), "max_min": round(float(a.max()), 1)}
    out["per_feed"] = per
    if recs:
        a = np.array([float(r[1]) for r in recs])
        out["all"] = {"median_min": round(float(np.median(a)), 1), "p90_min": round(float(np.percentile(a, 90)), 1)}
    out["how_to_read"] = ("Latency = our first_seen minus the satellite acquisition time, measured by this server's poller every %d s (resolution %d min). "
                          "Detections already in the feed at our first poll are excluded. It is not FIRMS' internal processing time and not a "
                          "direct-broadcast latency." % (POLL_S, POLL_S // 60))
    if not recs:
        out["note"] = "No statistics yet: the poller needs at least two polls (first poll only builds the baseline)."
    return out


def _loop():
    while True:
        try:
            with _lock:
                poll_once()
                _save()
        except Exception as e:  # noqa: BLE001
            _state["last_error"] = f"{type(e).__name__}: {e}"[:200]
        time.sleep(POLL_S)


def start_poller():
    global _thread
    if _thread is not None or "pytest" in sys.modules:
        return
    _load()
    _state["started_utc"] = _state["started_utc"] or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _thread = threading.Thread(target=_loop, name="latency-poller", daemon=True)
    _thread.start()


router = APIRouter(tags=["latency"])


@router.get("/latency/stats", summary="Detection latency seen by our own poller (first_seen minus acquisition time)")
def latency_stats() -> dict:
    return stats()
