"""Feedback loop: store ISSUED forecasts, score them once the satellites have observed the horizon, report running
accuracy, and learn only what is defensible. This is a feedback record, not retraining: models/*.json are never
modified and /predict, /simulate, /predict/simulate are untouched.

    POST /feedback/record            store the issued forecast (body = a /predict or /predict/simulate response, or
                                     {lat, lon, date, days}); idempotent (one record per fire cell / UTC date / horizon)
    GET  /feedback/analyse?id=|lat&lon&date   score the days that can already be observed, return the reveal; for days
                                     not yet observable return when each can be scored (status 'pending')
    GET  /feedback/summary           running accuracy per day / method / region vs the "mark every eligible cell"
                                     baseline, pending count, adjustments (what was learned, active or not)
    GET  /feedback/forecasts         stored forecasts with scoring status and the refitted spread multiplier
    GET  /feedback/adjusted-selection?id=   cells the ACTIVE region rule would select (read-only preview)

Truth (scoring) definition
--------------------------
Same as equifire.direct.case_truth: a cell counts as newly burned on forecast day k if it was not burning on the
initialization day, was an eligible cell of the issued forecast for that day (within k cells of the burning area) and
VIIRS (375 m, 0.05 deg cells) detected fire there on any day <= k. Strict = same cell; within-5 km = within one cell
(replay.tolerant_counts, the walk_forward._tol score). Approximations (reported in every response):
  * Live forecasts are scored from the FIRMS NRT 7-day feed (VIIRS NOAA-20/21/S-NPP; MODIS rows when the ignition came
    from MODIS) instead of the archive; NRT can differ slightly from standard processing, and the archive's
    sub-cell/day-night flags are not used. A UTC day is scorable 3 h after it ended, only while it is still inside the
    7-day NRT window (or covered by the archive).
  * Eligible cells are the issued cells of that day (all cells with a finite probability are kept in the record).
  * 'physics' scoring marks cells with physics_burned_fraction >= 0.5 (physics has no selection rule of its own).

What is learned (and when it is allowed to change anything)
-----------------------------------------------------------
(a) per fire: a spread multiplier m refitted on the observed growth (approximate, no re-simulation: ratio of cells the
    physics burned >= 5% vs cells observed, shrunk with the physics_spread calibration constants). Reported only.
(b) per region (country box, 1-degree box, or all regions pooled): with >= 30 scored non-curated forecasts, the best
    cell-selection rule among equifire.direct.RULES, chosen by 5-fold cross-validation. It is marked active ONLY if the
    held-out gain in the within-5 km score is >= +1.0 point and the bootstrap 95% CI of the gain is above 0. Otherwise
    active=false ('not enough evidence, no change applied'). Earlier online-learning tests moved accuracy by about
    +0.1 points (reports/walk_forward.json), so activation is expected to be rare.
Curated-replay records are shown separately and never count towards the 30-forecast threshold unless include_curated=1.

Env: EQUIFIRE_NO_FEEDBACK=1 disables the background scorer (also off with EQUIFIRE_LITE / EQUIFIRE_NO_LOAD);
EQUIFIRE_FEEDBACK_DB overrides the database path; EQUIFIRE_FEEDBACK_INTERVAL_S (default 3600).
"""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import math
import os
import sqlite3
import threading
import zlib
from pathlib import Path

import numpy as np

try:
    from equifire.common import CACHE, EPOCH, GRID
except Exception:  # pragma: no cover
    CACHE, EPOCH, GRID = Path("cache"), np.datetime64("2000-01-01"), 0.05

SCHEMA = "equifire.feedback/1"
DB_DEFAULT = CACHE / "feedback" / "feedback.db"
LATENCY_H = 3                 # a UTC day is scorable this many hours after it ended (NRT latency)
NRT_DAYS = 7                  # FIRMS NRT feed window
PHYS_THR = 0.5                # physics "selection": burned fraction >= this
PHYS_CELL_THR = 0.05          # refit of m: a cell counts as simulated-burned above this fraction
MIN_FORECASTS = 30            # per region, non-curated
MIN_GAIN_PTS = 1.0            # held-out within-5 km gain (points) required to activate a rule
BOOT = 2000
CURATED = "curated replay"
BASELINE = "baseline_all_eligible"
METHODS = ("ml_physics", "ml", "physics", BASELINE)
HONEST = [
    "This is a feedback record, not retraining: the main models are not modified.",
    "Earlier tests: learning from the model's own predictions changed accuracy by about +0.1 points.",
    "The ML + physics blend does not beat ML alone; physics and combined numbers are experimental and uncalibrated.",
]
TRUTH_NOTE = ("Observed new cells: VIIRS 0.05 deg cells burning after the issue day, inside the issued eligible area "
              "(same rule as the walk-forward truth). Live forecasts are scored from the FIRMS NRT feed, which can "
              "differ slightly from the standard-processing archive. Absence of detection (cloud, gaps) is not proof "
              "of no fire.")
_FIVE = {   # approximate boxes (minlon, minlat, maxlon, maxlat); US limited to the lower 48 so Canada wins above 49 N
    "Brazil": (-74.0, -34.0, -34.0, 5.5), "Australia": (112.0, -44.0, 154.0, -10.0),
    "India": (68.0, 6.0, 98.0, 36.0), "United_States": (-125.0, 24.5, -66.9, 49.0),
    "Canada": (-141.0, 41.7, -52.0, 83.5)}

_now = lambda: dt.datetime.now(dt.timezone.utc)  # noqa: E731  (tests monkeypatch)


# ============================================================== helpers
def clean(o):
    """JSON-safe copy: non-finite floats -> None, numpy scalars -> python."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return float(o) if math.isfinite(float(o)) else None
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_date(s) -> dt.date:
    return dt.date.fromisoformat(str(s)[:10])


def day_index(d: dt.date) -> int:
    return int((np.datetime64(d.isoformat()) - EPOCH).astype(int))


def region_of(lat: float, lon: float) -> str:
    for name, (x0, y0, x1, y1) in _FIVE.items():
        if x0 <= lon <= x1 and y0 <= lat <= y1:
            return name
    la, lo = math.floor(lat), math.floor(lon)
    return f"1deg {abs(la)}{'N' if la >= 0 else 'S'} {abs(lo)}{'E' if lo >= 0 else 'W'}"


def _z(o) -> bytes:
    return zlib.compress(json.dumps(o, separators=(",", ":")).encode(), 6)


def _unz(b: bytes):
    return json.loads(zlib.decompress(b).decode())


# ============================================================== maturity (pure)
def day_bounds(t0: dt.date, k: int):
    start = dt.datetime.combine(t0 + dt.timedelta(days=k), dt.time(0, 0), tzinfo=dt.timezone.utc)
    return start, start + dt.timedelta(days=1)


def maturity(t0: dt.date, days: int, now: dt.datetime, scored=(), archive_last_day: int | None = None,
             live: bool = True) -> list[dict]:
    """Per forecast day: scored | ready | pending | expired | unobservable and the exact times.
    scorable_from = end of that UTC day + LATENCY_H. NRT keeps a day for NRT_DAYS after it started; the archive
    (archive_last_day = day index) can cover it instead."""
    out = []
    d0 = day_index(t0)
    for k in range(1, days + 1):
        start, end = day_bounds(t0, k)
        from_ = end + dt.timedelta(hours=LATENCY_H)
        expires = start + dt.timedelta(days=NRT_DAYS)
        in_archive = archive_last_day is not None and d0 + k <= archive_last_day
        if k in scored:
            status = "scored"
        elif in_archive:
            status = "ready"
        elif now < from_:
            status = "pending"
        elif now < expires:
            status = "ready"
        else:
            status = "expired" if live else "unobservable"
        out.append({"day": k, "date": (t0 + dt.timedelta(days=k)).isoformat(), "day_ends_utc": iso(end),
                    "scorable_from_utc": iso(from_), "nrt_available_until_utc": iso(expires),
                    "covered_by_archive": bool(in_archive), "status": status})
    return out


# ============================================================== storage
SQL = """
CREATE TABLE IF NOT EXISTS forecasts (
  id TEXT PRIMARY KEY, created_utc TEXT NOT NULL, source TEXT NOT NULL, mode TEXT NOT NULL,
  lat REAL, lon REAL, t0_date TEXT NOT NULL, t0_utc TEXT NOT NULL, days INTEGER NOT NULL, objective TEXT,
  cx INTEGER, cy INTEGER, region TEXT, methods TEXT, ignition_source TEXT, physics TEXT, query TEXT,
  cells BLOB NOT NULL, cells_sha TEXT NOT NULL, n_cells INTEGER, incident TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS outcomes (
  forecast_id TEXT NOT NULL, day INTEGER NOT NULL, method TEXT NOT NULL, scored_utc TEXT, truth_source TEXT,
  observed_new_cells INTEGER, strict TEXT, tolerant TEXT, hits INTEGER, misses INTEGER, false_alarms INTEGER,
  within_5km REAL, strict_iou REAL, PRIMARY KEY (forecast_id, day, method));
CREATE TABLE IF NOT EXISTS truth (
  forecast_id TEXT NOT NULL, day INTEGER NOT NULL, source TEXT, scored_utc TEXT, cells BLOB,
  PRIMARY KEY (forecast_id, day));
CREATE TABLE IF NOT EXISTS adjustments (
  id INTEGER PRIMARY KEY AUTOINCREMENT, region TEXT NOT NULL, kind TEXT NOT NULL, params TEXT, n INTEGER,
  fitted_utc TEXT, validated_gain REAL, ci_lo REAL, ci_hi REAL, active INTEGER DEFAULT 0, note TEXT,
  UNIQUE (region, kind));
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


class Store:
    """SQLite (WAL) behind one lock. Issued forecasts are insert-only: there is no UPDATE path for them."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        self.db.row_factory = sqlite3.Row
        with self.lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
            self.db.executescript(SQL)
            self.db.commit()

    def close(self):
        with self.lock:
            self.db.close()

    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def x(self, sql, args=()):
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur

    # ---- forecasts
    def insert_forecast(self, fc: dict) -> bool:
        """True if created, False if the id already existed (idempotent; the stored forecast is never changed)."""
        cells = _z(fc["cells"])
        row = (fc["id"], fc["created_utc"], fc["source"], fc["mode"], fc["lat"], fc["lon"], fc["t0_date"],
               fc["t0_utc"], fc["days"], fc["objective"], fc["cx"], fc["cy"], fc["region"],
               json.dumps(fc["methods"]), fc["ignition_source"], json.dumps(clean(fc["physics"])),
               json.dumps(clean(fc["query"])), cells, hashlib.sha1(cells).hexdigest(), len(fc["cells"]["gx"]),
               fc.get("incident"), fc.get("note"))
        with self.lock:
            cur = self.db.execute("INSERT OR IGNORE INTO forecasts VALUES (" + ",".join("?" * 22) + ")", row)
            self.db.commit()
            return cur.rowcount == 1

    def get_forecast(self, fid: str, with_cells=True) -> dict | None:
        r = self.q("SELECT * FROM forecasts WHERE id=?", (fid,))
        return self._decode(r[0], with_cells) if r else None

    @staticmethod
    def _decode(r: dict, with_cells=True) -> dict:
        blob = r.pop("cells")
        r["methods"] = json.loads(r["methods"] or "[]")
        r["physics"] = json.loads(r["physics"] or "null")
        r["query"] = json.loads(r["query"] or "null")
        if with_cells:
            r["cells"] = _unz(blob)
            if hashlib.sha1(blob).hexdigest() != r["cells_sha"]:
                raise RuntimeError("stored forecast was modified (hash mismatch)")
        return r

    def list_forecasts(self, limit=200, source=None) -> list[dict]:
        sql = "SELECT * FROM forecasts" + (" WHERE source=?" if source else "") + " ORDER BY created_utc DESC LIMIT ?"
        return [self._decode(r, False) for r in self.q(sql, ((source, limit) if source else (limit,)))]

    def find_forecast(self, lat, lon, date, days=None, tol=0.3):
        sql = "SELECT id FROM forecasts WHERE t0_date=? AND abs(lat-?)<=? AND abs(lon-?)<=? AND source!=?"
        args = [str(date), lat, tol, lon, tol, CURATED]
        if days:
            sql += " AND days=?"
            args.append(days)
        r = self.q(sql + " ORDER BY abs(lat-?)+abs(lon-?) LIMIT 1", args + [lat, lon])
        return r[0]["id"] if r else None

    # ---- truth / outcomes
    def put_truth(self, fid, k, source, cells, when):
        self.x("INSERT OR REPLACE INTO truth VALUES (?,?,?,?,?)", (fid, k, source, when, _z(cells)))

    def truth(self, fid) -> dict[int, dict]:
        return {r["day"]: {"source": r["source"], "scored_utc": r["scored_utc"], "cells": _unz(r["cells"])}
                for r in self.q("SELECT * FROM truth WHERE forecast_id=? ORDER BY day", (fid,))}

    def put_outcomes(self, rows):
        with self.lock:
            self.db.executemany("INSERT OR REPLACE INTO outcomes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            self.db.commit()

    def outcomes(self, fid=None) -> list[dict]:
        r = self.q("SELECT * FROM outcomes" + (" WHERE forecast_id=?" if fid else ""), (fid,) if fid else ())
        for x in r:
            x["strict"], x["tolerant"] = json.loads(x["strict"]), json.loads(x["tolerant"])
        return r

    def scored_days(self, fid) -> set[int]:
        return {r["day"] for r in self.q("SELECT day FROM truth WHERE forecast_id=?", (fid,))}

    # ---- adjustments
    def put_adjustment(self, region, kind, params, n, gain, ci, active, note):
        self.x("INSERT INTO adjustments (region, kind, params, n, fitted_utc, validated_gain, ci_lo, ci_hi, active, note)"
               " VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(region, kind) DO UPDATE SET params=excluded.params,"
               " n=excluded.n, fitted_utc=excluded.fitted_utc, validated_gain=excluded.validated_gain,"
               " ci_lo=excluded.ci_lo, ci_hi=excluded.ci_hi, active=excluded.active, note=excluded.note",
               (region, kind, json.dumps(clean(params)), n, iso(_now()), gain, ci[0] if ci else None,
                ci[1] if ci else None, int(bool(active)), note))

    def adjustments(self, kind=None) -> list[dict]:
        r = self.q("SELECT * FROM adjustments" + (" WHERE kind=?" if kind else "") + " ORDER BY region", (kind,) if kind else ())
        for x in r:
            x["params"] = json.loads(x["params"] or "null")
            x["active"] = bool(x["active"])
        return r

    def meta(self, key, default=None):
        r = self.q("SELECT value FROM meta WHERE key=?", (key,))
        return r[0]["value"] if r else default

    def set_meta(self, key, value):
        self.x("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, str(value)))


_STORES: dict[str, Store] = {}
_STORES_LOCK = threading.Lock()


def get_store(path=None) -> Store:
    p = str(path or os.environ.get("EQUIFIRE_FEEDBACK_DB") or DB_DEFAULT)
    with _STORES_LOCK:
        if p not in _STORES:
            _STORES[p] = Store(p)
        return _STORES[p]


# ============================================================== record: normalise a /predict (/simulate) response
def _cell_xy(geom) -> tuple[int, int]:
    x0, y0 = geom["coordinates"][0][0]
    return int(round((x0 + 180) / GRID)), int(round((y0 + 90) / GRID))


def extract_cells(features) -> dict:
    """Column store of every issued cell: gx, gy, day, ml, ph, cb (probabilities / burned fraction, null if absent),
    sel (bit 1 = combined selected, bit 2 = ML selected)."""
    cols = {"gx": [], "gy": [], "day": [], "ml": [], "ph": [], "cb": [], "sel": []}
    for f in features:
        p = f.get("properties", {})
        if p.get("day") is None or not f.get("geometry"):
            continue
        gx, gy = _cell_xy(f["geometry"])
        has_comb = p.get("combined_probability") is not None
        sel = (1 if (p.get("selected") and has_comb) else 0) | (2 if (p.get("ml_selected") or (not has_comb and p.get("selected"))) else 0)
        cols["gx"].append(gx), cols["gy"].append(gy), cols["day"].append(int(p["day"]))
        cols["ml"].append(None if p.get("ml_probability") is None else round(float(p["ml_probability"]), 5))
        cols["ph"].append(None if p.get("physics_burned_fraction") is None else round(float(p["physics_burned_fraction"]), 5))
        cols["cb"].append(round(float(p["combined_probability"]), 5) if has_comb else None)
        cols["sel"].append(sel)
    return cols


def forecast_id(mode, cx, cy, t0_date, days, objective, source) -> str:
    return hashlib.sha1(f"{source}|{mode}|{cx}|{cy}|{t0_date}|{days}|{objective}".encode()).hexdigest()[:16]


def normalise(body: dict, source=None, now=None) -> dict:
    """Turn a /predict response, a /predict/simulate response (guidance = /predict) into a forecast record."""
    g = body.get("guidance") if isinstance(body.get("guidance"), dict) else body
    feats = (g.get("features") or []) if isinstance(g, dict) else []
    if not feats or "query" not in g or "centre" not in g:
        raise ValueError("body must be a /predict or /predict/simulate response (features, query, centre)")
    q, c = g["query"], g["centre"]
    hist = g.get("history") or {}
    mode = "live" if hist.get("reduced") else "archive"
    cells = extract_cells(feats)
    if not cells["gx"]:
        raise ValueError("forecast has no cells")
    days = int(q.get("days") or max(cells["day"]))
    objective = q.get("objective") or "iou"
    t0_date = parse_date(q["date"])
    cx, cy = int(math.floor((c["lon"] + 180) / GRID)), int(math.floor((c["lat"] + 90) / GRID))
    src = source or mode
    methods = ["ml"] + (["ml_physics", "physics"] if any(v is not None for v in cells["cb"]) else [])
    phys = g.get("physics") or {}
    keep = ("m", "fuel", "cal_applied", "cal_confidence", "cal_reason", "burned_km2", "initial_km2", "hours",
            "radius_km", "start_utc", "weather_fallback", "ok")
    as_of = hist.get("as_of_utc")
    return {"id": forecast_id(mode, cx, cy, t0_date, days, objective, src), "created_utc": iso(now or _now()),
            "source": src, "mode": mode, "lat": float(c["lat"]), "lon": float(c["lon"]), "t0_date": t0_date.isoformat(),
            "t0_utc": as_of or f"{t0_date.isoformat()}T00:00:00Z", "days": days, "objective": objective,
            "cx": cx, "cy": cy, "region": region_of(float(c["lat"]), float(c["lon"])), "methods": methods,
            "ignition_source": hist.get("ignition_source") or "VIIRS",
            "physics": {k: phys.get(k) for k in keep if k in phys}, "query": q, "cells": cells,
            "note": "; ".join(g.get("warnings", [])[:2]) or None}


# ============================================================== scoring (pure, uses backend.replay read-only)
class Case:
    """Issued forecast rasterised on a bbox grid: per forecast day, masks / probabilities (NaN outside eligible)."""

    def __init__(self, cells: dict, days: int, pad=2):
        gx, gy = np.asarray(cells["gx"], int), np.asarray(cells["gy"], int)
        self.x0, self.y0 = int(gx.min()) - pad, int(gy.min()) - pad
        self.nx, self.ny = int(gx.max()) - self.x0 + pad + 1, int(gy.max()) - self.y0 + pad + 1
        self.days = days
        day = np.asarray(cells["day"], int)
        sel = np.asarray(cells["sel"], int)
        f = lambda key: np.array([np.nan if v is None else v for v in cells[key]], float)  # noqa: E731
        ml, ph, cb = f("ml"), f("ph"), f("cb")
        self.has_comb, self.has_phys = bool(np.isfinite(cb).any()), bool(np.isfinite(ph).any())
        z = lambda: np.zeros((days, self.nx, self.ny), bool)  # noqa: E731
        n = lambda: np.full((days, self.nx, self.ny), np.nan)  # noqa: E731
        self.elig, self.sel_comb, self.sel_ml = z(), z(), z()
        self.p_ml, self.p_phys, self.p_comb = n(), n(), n()
        for k in range(1, days + 1):
            m = day == k
            ix, iy = gx[m] - self.x0, gy[m] - self.y0
            self.elig[k - 1][ix, iy] = True
            self.sel_comb[k - 1][ix, iy] = (sel[m] & 1) > 0
            self.sel_ml[k - 1][ix, iy] = (sel[m] & 2) > 0
            self.p_ml[k - 1][ix, iy], self.p_phys[k - 1][ix, iy], self.p_comb[k - 1][ix, iy] = ml[m], ph[m], cb[m]
        self.cells_day = [(gx[day == k], gy[day == k]) for k in range(1, days + 1)]

    def truth_masks(self, det_by_day: dict[int, list], through: int) -> np.ndarray:
        """Cumulative truth [through, nx, ny]: OR over j <= k of (detected on day j) & eligible on day j."""
        out = np.zeros((through, self.nx, self.ny), bool)
        cum = np.zeros((self.nx, self.ny), bool)
        for k in range(1, through + 1):
            d = np.zeros((self.nx, self.ny), bool)
            for gx, gy in det_by_day.get(k, []):
                x, y = gx - self.x0, gy - self.y0
                if 0 <= x < self.nx and 0 <= y < self.ny:
                    d[x, y] = True
            cum |= d & self.elig[k - 1]
            out[k - 1] = cum
        return out

    def predicted(self, method: str, k: int):
        i = k - 1
        if method == "ml_physics":
            return self.sel_comb[i] if self.has_comb else None
        if method == "ml":
            return self.sel_ml[i]
        if method == "physics":
            return (np.nan_to_num(self.p_phys[i], nan=0.0) >= PHYS_THR) & self.elig[i] if self.has_phys else None
        return self.elig[i]  # baseline: mark every eligible cell


def score_case(case: Case, det_by_day: dict[int, list], through: int, classes=False) -> dict:
    """Per day, per method: strict + tolerant counts (replay.score_day). Optionally per-cell classes."""
    from . import replay
    truth = case.truth_masks(det_by_day, through)
    per_day, cell_cls = [], {}
    for k in range(1, case.days + 1):
        row = {"day": k, "scored": k <= through, "observed_new_cells": int(truth[k - 1].sum()) if k <= through else None,
               "methods": {}}
        if k <= through:
            for m in METHODS:
                pred = case.predicted(m, k)
                if pred is None:
                    continue
                row["methods"][m] = replay.score_day(pred, truth[k - 1])
                if classes and m in ("ml_physics", "ml"):
                    s, tol = replay.classify(pred, truth[k - 1])
                    for x, y in zip(*np.nonzero((s > 0) | (tol > 0))):
                        c = cell_cls.setdefault((int(x), int(y)), {}).setdefault(
                            m, {"strict": ["none"] * case.days, "tolerant": ["none"] * case.days})
                        c["strict"][k - 1] = replay.CLASS_NAMES[int(s[x, y])]
                        c["tolerant"][k - 1] = replay.CLASS_NAMES[int(tol[x, y])]
        per_day.append(row)
    out = {"per_day": per_day, "truth": truth}
    if classes:
        from . import geo
        feats = []
        for (x, y), c in sorted(cell_cls.items()):
            gx, gy = case.x0 + x, case.y0 + y
            feats.append({"type": "Feature", "geometry": geo.cell_polygon(gx, gy), "properties": {
                "kind": "reveal_cell", "gx": gx, "gy": gy, "classes": c}})
        out["geojson"] = {"type": "FeatureCollection", "features": feats}
    return out


def outcome_rows(fid, per_day, truth_meta, when) -> list[tuple]:
    rows = []
    for row in per_day:
        if not row["scored"]:
            continue
        tm = truth_meta.get(row["day"], {})
        for m, s in row["methods"].items():
            st, tl = s["strict"], s["tolerant"]
            rows.append((fid, row["day"], m, tm.get("scored_utc") or when, tm.get("source"), row["observed_new_cells"],
                         json.dumps(clean(st)), json.dumps(clean(tl)), st["hits"], st["misses"], st["false_alarms"],
                         tl["within_5km_score"], st["iou"]))
    return rows


# ============================================================== learning (a): per-fire spread multiplier
def fit_physics_m(case: Case, truth: np.ndarray, m_old: float | None) -> dict | None:
    """Approximate refit of the physics spread multiplier on observed growth, WITHOUT re-simulating:
    residual = log((cells the physics burned >= 5%) / (observed new cells)); log m moves by -w * residual / p with the
    shrinkage and bounds of physics_spread.calibrate (p ~ d log area / d log m). Reported only, never applied."""
    if not case.has_phys or truth.shape[0] == 0:
        return None
    try:
        from . import physics_spread as ps
        P0, SP, SM, LO, HI = ps.CAL_P0, ps.CAL_SIGMA_PRIOR, ps.CAL_SIGMA_MODEL, *ps.CAL_BOUNDS
    except Exception:  # pragma: no cover
        P0, SP, SM, LO, HI = 1.5, math.log(3.0), math.log(3.0), 0.1, 5.0
    res, n_obs = [], 0
    for k in range(truth.shape[0]):
        sim = int((np.nan_to_num(case.p_phys[k], nan=0.0) >= PHYS_CELL_THR).sum())
        obs = int(truth[k].sum())
        res.append(math.log((sim + 0.5) / (obs + 0.5)))
        n_obs = obs
    if not any(abs(r) > 1e-9 for r in res):
        return None
    r = float(np.mean(res))
    var_e = SM ** 2 + 1.0 / (n_obs + 1)
    w = P0 ** 2 * SP ** 2 / (P0 ** 2 * SP ** 2 + var_e)
    m0 = float(m_old or 1.0)
    m_new = float(np.clip(m0 * math.exp(-w * r / P0), LO, HI))
    return {"m_issued": round(m0, 4), "m_refit": round(m_new, 4), "weight": round(w, 3), "days_used": int(truth.shape[0]),
            "mean_log_residual": round(r, 3), "applied": False,
            "note": "approximate refit from observed growth (cells), no re-simulation; reported only, /simulate and "
                    "/predict/simulate still use their own setting"}


# ============================================================== learning (b): per-region selection rule
def _boot_ci(x: np.ndarray, n=BOOT, seed=0):
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(x), size=(n, len(x)))
    means = x[idx].mean(1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def evaluate_rule_gain(S: np.ndarray, base: np.ndarray, folds=5, min_n=MIN_FORECASTS, min_gain_pts=MIN_GAIN_PTS,
                       seed=0) -> dict:
    """S[f, r] = within-5 km score (0..1) of forecast f under candidate rule r; base[f] = score of the issued rule.
    Rules are chosen on the training folds and scored on the held-out fold; the gain (points) is the held-out mean
    of score(chosen) - base, with a bootstrap 95% CI. active only if n >= min_n, gain >= min_gain_pts, CI low > 0."""
    S, base = np.asarray(S, float), np.asarray(base, float)
    n = len(base)
    res = {"n": int(n), "folds": folds, "active": False, "gain_pts": None, "ci_pts": None, "best_rule_index": None}
    if n == 0 or S.shape[1] == 0:
        res["reason"] = "no scored forecasts"
        return res
    res["best_rule_index"] = int(np.argmax(S.mean(0)))
    if n < min_n:
        res["reason"] = f"{n} of {min_n} scored forecasts needed"
        return res
    fold = np.arange(n) % folds
    gains = np.zeros(n)
    for f in range(folds):
        te, tr = fold == f, fold != f
        if not te.any() or not tr.any():
            continue
        best = int(np.argmax(S[tr].mean(0)))
        gains[te] = S[te, best] - base[te]
    lo, hi = _boot_ci(gains, seed=seed)
    res["gain_pts"], res["ci_pts"] = round(100 * float(gains.mean()), 3), [round(100 * lo, 3), round(100 * hi, 3)]
    res["active"] = bool(100 * gains.mean() >= min_gain_pts and lo > 0)
    res["reason"] = ("held-out gain >= %.1f point and the bootstrap interval is above 0" % min_gain_pts if res["active"]
                     else "not enough evidence: held-out gain below %.1f point or the interval includes 0" % min_gain_pts)
    return res


def _rule_matrix(items) -> tuple[np.ndarray, np.ndarray, list]:
    from equifire import direct
    from . import replay
    rules = list(direct.RULES)
    S, base = [], []
    for case, truth in items:
        T = truth.shape[0]
        sc = np.zeros((T, len(rules)))
        b = np.zeros(T)
        for k in range(T):
            P = case.p_comb[k] if case.has_comb else case.p_ml[k]
            for j, rule in enumerate(rules):
                pred = direct.select(P, rule) & case.elig[k]
                sc[k, j] = replay.tolerant_counts(pred, truth[k])["within_5km_score"]
            issued = case.sel_comb[k] if case.has_comb else case.sel_ml[k]
            b[k] = replay.tolerant_counts(issued, truth[k])["within_5km_score"]
        S.append(sc.mean(0))
        base.append(b.mean())
    return np.array(S), np.array(base), rules


def load_scored(store: Store, include_curated=False):
    """[(forecast row, Case, truth masks)] for forecasts with >= 1 scored day (stored truth)."""
    out = []
    for fc in store.list_forecasts(10000):
        if fc["source"] == CURATED and not include_curated:
            continue
        tr = store.truth(fc["id"])
        if not tr:
            continue
        full = store.get_forecast(fc["id"])
        case = Case(full["cells"], full["days"])
        through = 0
        for k in range(1, full["days"] + 1):
            if k in tr:
                through = k
            else:
                break
        if through:
            det = {k: [tuple(c) for c in tr[k]["cells"]] for k in range(1, through + 1)}
            out.append((fc, case, case.truth_masks(det, through)))
    return out


def refresh_adjustments(store: Store, include_curated=False) -> list[dict]:
    scored = load_scored(store, include_curated)
    groups = {"all regions": scored}
    for item in scored:
        groups.setdefault(item[0]["region"], []).append(item)
    for region, items in groups.items():
        n = len(items)
        if n < MIN_FORECASTS:
            store.put_adjustment(region, "selection_rule", {"rule": None}, n, None, None, False,
                                 f"Learning: {n} of {MIN_FORECASTS} forecasts needed in this region; no change applied")
            continue
        S, base, rules = _rule_matrix([(c, t) for _, c, t in items])
        ev = evaluate_rule_gain(S, base)
        rule = list(rules[ev["best_rule_index"]])
        store.put_adjustment(region, "selection_rule", {"rule": rule, "folds": ev["folds"]}, n, ev["gain_pts"],
                             ev["ci_pts"], ev["active"],
                             ("Active: " if ev["active"] else "Not enough evidence, no change applied: ") + ev["reason"])
    return store.adjustments("selection_rule")


# ============================================================== truth providers
def truth_from_feed(df, fc: dict, k: int) -> list[list[int]]:
    """Cells detected on forecast day k in the NRT feed within the forecast window (VIIRS; MODIS if the ignition was
    MODIS)."""
    d = day_index(parse_date(fc["t0_date"])) + k
    modis = str(fc.get("ignition_source", "")).startswith("MODIS")
    m = (df["day"] == d) & ((df["sat"] == "MODIS") if modis else (df["sat"] != "MODIS"))
    m &= (df["gx"] - fc["cx"]).abs() <= 25
    m &= (df["gy"] - fc["cy"]).abs() <= 25
    sub = df.loc[m, ["gx", "gy"]].drop_duplicates()
    return [[int(a), int(b)] for a, b in zip(sub["gx"], sub["gy"])]


def truth_from_archive(full, fc: dict, k: int) -> list[list[int]]:
    d = day_index(parse_date(fc["t0_date"])) + k
    ids = full.v.query(d, d, fc["cx"], fc["cy"])
    gx, gy = np.asarray(full.v.gx[ids]).astype(int), np.asarray(full.v.gy[ids]).astype(int)
    return sorted({(int(a), int(b)) for a, b in zip(gx, gy)}.__iter__().__class__ and [[int(a), int(b)] for a, b in set(zip(gx, gy))])


class Sources:
    """Where truth comes from. All three are injectable (tests). archive() returns the fire index or None;
    feed() returns the NRT detections DataFrame or None."""

    def __init__(self, feed=None, archive=None, allow_archive_load=True):
        self._feed_fn, self._archive_fn, self.allow = feed, archive, allow_archive_load
        self._df, self._df_done, self._full, self._full_done = None, False, None, False

    def feed(self):
        if not self._df_done:
            self._df_done = True
            try:
                fn = self._feed_fn
                if fn is None:
                    from .live import load_feeds
                    fn = lambda: load_feeds("7d")[0]  # noqa: E731
                self._df = fn()
            except Exception:
                self._df = None
        return self._df

    def archive(self):
        if not self._full_done:
            self._full_done = True
            try:
                if self._archive_fn is not None:
                    self._full = self._archive_fn()
                elif self.allow or self._loaded():
                    from . import ensemble
                    self._full = ensemble.archive_index()
            except Exception:
                self._full = None
        return self._full

    @staticmethod
    def _loaded():
        try:
            from . import ensemble
            return ensemble.archive_index.cache_info().currsize > 0
        except Exception:
            return False

    def archive_last_day(self):
        full = self.archive()
        try:
            return None if full is None else int(full.v.day.max())
        except Exception:
            return None


def score_forecast(store: Store, fid: str, now: dt.datetime, src: Sources, refit=True) -> dict:
    """Fetch the truth of every newly scorable day (in order: scoring is cumulative), store it, rescore, refit m.
    Never modifies the stored forecast."""
    fc = store.get_forecast(fid)
    if fc is None:
        raise KeyError(fid)
    t0 = parse_date(fc["t0_date"])
    have = store.scored_days(fid)
    new = []
    for k in range(1, fc["days"] + 1):
        if k in have:
            continue
        start, end = day_bounds(t0, k)
        when = iso(now)
        full = src.archive() if fc["mode"] == "archive" or now >= start + dt.timedelta(days=NRT_DAYS) else None
        alast = None if full is None else int(full.v.day.max())
        cells, source = None, None
        if alast is not None and day_index(t0) + k <= alast:
            cells, source = truth_from_archive(full, fc, k), "VIIRS archive"
        elif now >= end + dt.timedelta(hours=LATENCY_H) and now < start + dt.timedelta(days=NRT_DAYS):
            df = src.feed()
            if df is not None and len(df) and pd_max(df) >= end.replace(tzinfo=None):
                cells = truth_from_feed(df, fc, k)
                source = "FIRMS NRT " + ("MODIS (VIIRS-equivalent)" if str(fc["ignition_source"]).startswith("MODIS") else "VIIRS")
        if cells is None:
            break
        store.put_truth(fid, k, source, cells, when)
        new.append(k)
    if new and refit:
        recompute(store, fid, now)
    return {"id": fid, "newly_scored_days": new}


def pd_max(df):
    import pandas as pd
    return pd.Timestamp(df["t"].max()).to_pydatetime()


def recompute(store: Store, fid: str, now: dt.datetime):
    fc = store.get_forecast(fid)
    tr = store.truth(fid)
    through = 0
    for k in range(1, fc["days"] + 1):
        if k in tr:
            through = k
        else:
            break
    if not through:
        return None
    case = Case(fc["cells"], fc["days"])
    det = {k: [tuple(c) for c in tr[k]["cells"]] for k in range(1, through + 1)}
    sc = score_case(case, det, through)
    store.put_outcomes(outcome_rows(fid, sc["per_day"], tr, iso(now)))
    fit = fit_physics_m(case, sc["truth"], (fc.get("physics") or {}).get("m"))
    if fit:
        store.put_adjustment(f"fire:{fid}", "physics_m", fit, through, None, None, False,
                             "per-fire refit; reported only, not applied to any forecast")
    return sc


# ============================================================== curated replay seed
def seed_curated(store: Store, replay_dir: Path | None = None) -> int:
    """The saved curated replays (cache/replay/<incident>__iou.json) as clearly marked records with their reveal."""
    if store.meta("curated_seeded") == "1" and replay_dir is None:
        return 0
    try:
        from . import replay as rp
        d = replay_dir or rp.CACHE_DIR
    except Exception:
        return 0
    n = 0
    for path in sorted(Path(d).glob("*__iou.json")):
        try:
            r = json.loads(path.read_text())
        except Exception:
            continue
        inc = r.get("incident")
        if not inc:
            continue
        cells = extract_cells(r["issued"]["geojson"]["features"])
        if not cells["gx"]:
            continue
        q, c = r["query"], r["centre"]
        fid = "curated_" + inc["id"]
        fc = {"id": fid, "created_utc": r.get("generated_utc") or iso(_now()), "source": CURATED, "mode": "archive",
              "lat": c["lat"], "lon": c["lon"], "t0_date": q["date"], "t0_utc": f"{q['date']}T00:00:00Z",
              "days": q["days"], "objective": q.get("objective", "iou"), "cx": c["gx"], "cy": c["gy"],
              "region": region_of(c["lat"], c["lon"]), "methods": r["issued"].get("methods_available", ["ml"]),
              "ignition_source": "VIIRS", "physics": (r["method"].get("physics") or {}), "query": q, "cells": cells,
              "incident": inc["id"], "note": "curated replay (only cells with probability >= 0.02 or selected were kept)"}
        if store.insert_forecast(fc):
            n += 1
        rows = []
        for row in r["reveal"]["per_day"]:
            if not row.get("scored"):
                continue
            for m, s in row["methods"].items():
                m2 = BASELINE if m == "baseline_ring" else m
                rows.append((fid, row["day"], m2, r.get("generated_utc"), "VIIRS archive (curated replay)",
                             row["observed_new_cells"], json.dumps(s["strict"]), json.dumps(s["tolerant"]),
                             s["strict"]["hits"], s["strict"]["misses"], s["strict"]["false_alarms"],
                             s["tolerant"]["within_5km_score"], s["strict"]["iou"]))
        store.put_outcomes(rows)
    if replay_dir is None:
        store.set_meta("curated_seeded", "1")
    return n


# ============================================================== record / analyse / summary
def status_of(mat: list[dict]) -> str:
    s = [m["status"] for m in mat]
    if all(x == "scored" for x in s):
        return "scored"
    if any(x == "scored" for x in s):
        return "partial"
    if any(x in ("pending", "ready") for x in s):
        return "pending"
    return "unobservable"


def forecast_maturity(store: Store, fc: dict, now: dt.datetime, src: Sources | None = None) -> list[dict]:
    alast = src.archive_last_day() if (src is not None and fc["mode"] == "archive") else None
    return maturity(parse_date(fc["t0_date"]), fc["days"], now, store.scored_days(fc["id"]), alast,
                    live=fc["mode"] == "live")


def pending_message(mat) -> str:
    parts = [f"day {m['day']} on {m['scorable_from_utc'].replace('T', ' ').replace('Z', ' UTC')}"
             for m in mat if m["status"] in ("pending", "ready")]
    return ("Saved. Can be scored: " + ", ".join(parts) + ".") if parts else "Saved."


def record(store: Store, body: dict, now: dt.datetime | None = None, source=None) -> dict:
    now = now or _now()
    fc = normalise(body, source=source, now=now)
    created = store.insert_forecast(fc)
    mat = maturity(parse_date(fc["t0_date"]), fc["days"], now, store.scored_days(fc["id"]), None,
                   live=fc["mode"] == "live")
    return {"schema": SCHEMA, "id": fc["id"], "status": "created" if created else "exists", "mode": fc["mode"],
            "days": fc["days"], "t0_date": fc["t0_date"], "region": fc["region"], "ignition_source": fc["ignition_source"],
            "n_cells": len(fc["cells"]["gx"]), "maturity": mat, "message": pending_message(mat),
            "honest_note": HONEST[0]}


def analyse(store: Store, fid: str, now: dt.datetime | None = None, src: Sources | None = None, refresh=False) -> dict:
    now = now or _now()
    src = src or Sources()
    fc = store.get_forecast(fid)
    if fc is None:
        raise KeyError(fid)
    if fc["source"] == CURATED:
        return analyse_curated(store, fc, now)
    if refresh:
        store.x("DELETE FROM truth WHERE forecast_id=?", (fid,))
        store.x("DELETE FROM outcomes WHERE forecast_id=?", (fid,))
    score_forecast(store, fid, now, src)
    mat = forecast_maturity(store, fc, now, src)
    tr = store.truth(fid)
    through = 0
    for k in range(1, fc["days"] + 1):
        if k in tr:
            through = k
        else:
            break
    out = {"schema": SCHEMA, "id": fid, "status": status_of(mat), "mode": fc["mode"], "source": fc["source"],
           "query": fc["query"], "centre": {"lat": fc["lat"], "lon": fc["lon"], "gx": fc["cx"], "gy": fc["cy"]},
           "t0_date": fc["t0_date"], "region": fc["region"], "maturity": mat, "revealed_through_day": through,
           "truth_definition": TRUTH_NOTE, "honest_notes": HONEST,
           "legend_colours_colourblind_safe": {"hit": "#0072B2", "near_hit": "#56B4E9", "miss": "#E69F00",
                                               "false_alarm": "#CC79A7", "unverifiable": "#999999"}}
    if through:
        case = Case(fc["cells"], fc["days"])
        det = {k: [tuple(c) for c in tr[k]["cells"]] for k in range(1, through + 1)}
        sc = score_case(case, det, through, classes=True)
        per_day = []
        for row, m in zip(sc["per_day"], mat):
            r = {"day": row["day"], "date": m["date"], "scored": row["scored"], "status": m["status"],
                 "observed_new_cells": row["observed_new_cells"], "truth_source": (tr.get(row["day"]) or {}).get("source"),
                 "methods": {}}
            for name, s in row["methods"].items():
                st, tl = s["strict"], s["tolerant"]
                r["methods"][name] = {"hits": st["hits"], "misses": st["misses"], "false_alarms": st["false_alarms"],
                                      "predicted_cells": st["predicted_cells"], "strict_iou": st["iou"],
                                      "within_5km": tl["within_5km_score"], "tolerant_misses": tl["misses"],
                                      "tolerant_false_alarms": tl["false_alarms"], "strict": st, "tolerant": tl}
            per_day.append(r)
        out["per_day"] = per_day
        out["reveal"] = {"geojson": sc["geojson"], "class_definitions": CLASS_DEFS}
        adj = store.adjustments("physics_m")
        out["learned"] = next((a["params"] for a in adj if a["region"] == f"fire:{fid}"), None)
    else:
        out["per_day"] = [{"day": m["day"], "date": m["date"], "scored": False, "status": m["status"]} for m in mat]
    out["message"] = (pending_message(mat) if out["status"] in ("pending", "partial") else
                      "Nothing can be scored: the observation window has passed and the archive does not cover it."
                      if out["status"] == "unobservable" else "All forecast days scored.")
    refresh_adjustments(store)
    return out


CLASS_DEFS = {
    "hit": "predicted and observed new burning cell (strict, same 0.05 deg cell)",
    "miss": "observed new burning cell that was not predicted",
    "false_alarm": "predicted cell with no observed burning (cloud or gaps can hide a fire: not proof of no fire)",
    "near_hit": "tolerant view: matched with an observed/predicted cell within 1 cell (~5.5 km)",
    "unverifiable": "predicted, not observed, and no usable overpass (not scored here)"}


def analyse_curated(store: Store, fc: dict, now) -> dict:
    mat = [{"day": k, "date": (parse_date(fc["t0_date"]) + dt.timedelta(days=k)).isoformat(), "status": "scored"}
           for k in range(1, fc["days"] + 1)]
    per_day = {}
    for o in store.outcomes(fc["id"]):
        r = per_day.setdefault(o["day"], {"day": o["day"], "date": mat[o["day"] - 1]["date"], "scored": True,
                                          "status": "scored", "observed_new_cells": o["observed_new_cells"],
                                          "truth_source": o["truth_source"], "methods": {}})
        r["methods"][o["method"]] = {"hits": o["hits"], "misses": o["misses"], "false_alarms": o["false_alarms"],
                                     "strict_iou": o["strict_iou"], "within_5km": o["within_5km"],
                                     "predicted_cells": o["strict"]["predicted_cells"], "strict": o["strict"],
                                     "tolerant": o["tolerant"]}
    reveal = None
    try:
        from . import replay as rp
        p = rp.cache_path(fc["incident"], "iou")
        if p.exists():
            r = json.loads(p.read_text())["reveal"]
            reveal = {"geojson": r["geojson"], "class_definitions": r.get("class_definitions", CLASS_DEFS)}
    except Exception:
        pass
    return {"schema": SCHEMA, "id": fc["id"], "status": "scored", "mode": "archive", "source": CURATED,
            "query": fc["query"], "centre": {"lat": fc["lat"], "lon": fc["lon"], "gx": fc["cx"], "gy": fc["cy"]},
            "t0_date": fc["t0_date"], "region": fc["region"], "maturity": mat, "revealed_through_day": fc["days"],
            "per_day": [per_day[k] for k in sorted(per_day)], "reveal": reveal, "learned": None,
            "truth_definition": TRUTH_NOTE, "honest_notes": HONEST,
            "message": "Curated replay (saved reveal from the Replay tab); not counted towards learning."}


def _mean(v):
    v = [x for x in v if x is not None]
    return round(float(np.mean(v)), 4) if v else None


def summary(store: Store, include_curated=False, now: dt.datetime | None = None, src: Sources | None = None) -> dict:
    now = now or _now()
    seed_curated(store)
    fcs = {f["id"]: f for f in store.list_forecasts(10000)}
    outs = [o for o in store.outcomes()]
    use = lambda o: include_curated or fcs.get(o["forecast_id"], {}).get("source") != CURATED  # noqa: E731
    outs = [o for o in outs if use(o) and o["forecast_id"] in fcs]
    # accuracy: forecast-level mean per (day, method)
    def table(rows):
        by = {}
        for o in rows:
            by.setdefault((o["day"], o["method"]), []).append(o)
        res = {}
        for (k, m), v in sorted(by.items()):
            res.setdefault(m, []).append({"day": k, "n": len(v), "within_5km": _mean([x["within_5km"] for x in v]),
                                          "strict_iou": _mean([x["strict_iou"] for x in v]),
                                          "hits": sum(x["hits"] for x in v), "misses": sum(x["misses"] for x in v),
                                          "false_alarms": sum(x["false_alarms"] for x in v)})
        return res
    overall = table(outs)
    regions = []
    for reg in sorted({fcs[o["forecast_id"]]["region"] for o in outs}):
        rows = [o for o in outs if fcs[o["forecast_id"]]["region"] == reg]
        t = table(rows)
        regions.append({"region": reg, "n_forecasts": len({o["forecast_id"] for o in rows}),
                        "n_non_curated": len({o["forecast_id"] for o in rows if fcs[o["forecast_id"]]["source"] != CURATED}),
                        "accuracy": t})
    scored_ids = {o["forecast_id"] for o in outs}
    n_curated = len({o["forecast_id"] for o in store.outcomes() if fcs.get(o["forecast_id"], {}).get("source") == CURATED})
    n_scored = len({i for i in scored_ids if fcs[i]["source"] != CURATED})
    # pending
    pend_f, pend_d, nxt = 0, 0, None
    for f in fcs.values():
        if f["source"] == CURATED:
            continue
        m = forecast_maturity(store, f, now, src)
        p = [x for x in m if x["status"] in ("pending", "ready")]
        if p:
            pend_f += 1
            pend_d += len(p)
            t = min(x["scorable_from_utc"] for x in p)
            nxt = t if nxt is None or t < nxt else nxt
    adj = refresh_adjustments(store, include_curated)
    last = max([o["scored_utc"] for o in outs if o["scored_utc"]] or [None], default=None) if outs else None
    fires = [a for a in store.adjustments("physics_m")]
    return {"schema": SCHEMA, "generated_utc": iso(now), "include_curated": include_curated, "n_forecasts": len(fcs),
            "n_forecasts_non_curated": sum(1 for f in fcs.values() if f["source"] != CURATED),
            "n_scored": n_scored, "n_scored_curated": n_curated,
            "pending": {"forecasts": pend_f, "days": pend_d, "next_scorable_utc": nxt}, "last_update_utc": last,
            "accuracy": {"methods": overall,
                         "baseline": "baseline_all_eligible marks every eligible cell (comparator, not a forecast)"},
            "regions": regions,
            "adjustments": {"selection_rule": [dict(a, text=_adj_text(a)) for a in adj],
                            "physics_m": [{"fire": a["region"][5:], **(a["params"] or {}), "days": a["n"]} for a in fires],
                            "min_forecasts": MIN_FORECASTS, "min_gain_points": MIN_GAIN_PTS},
            "honest_notes": HONEST,
            "worker": WORKER.status() if WORKER else {"running": False, "reason": "disabled"}}


def _adj_text(a: dict) -> str:
    if a["active"]:
        return (f"Active in {a['region']}: held-out within-5 km gain {a['validated_gain']:+.1f} points "
                f"(95% CI {a['ci_lo']:+.1f} to {a['ci_hi']:+.1f}), n = {a['n']}")
    if a["n"] < MIN_FORECASTS:
        return f"Learning: {a['n']} of {MIN_FORECASTS} forecasts needed in this region; no change applied"
    return (f"Not enough evidence, no change applied (n = {a['n']}, held-out gain "
            f"{a['validated_gain']:+.1f} points, 95% CI {a['ci_lo']:+.1f} to {a['ci_hi']:+.1f})")


def adjusted_selection(store: Store, fid: str) -> dict:
    """Cells the active rule of this forecast's region (or 'all regions') would select. Read-only preview."""
    from equifire import direct
    fc = store.get_forecast(fid)
    if fc is None:
        raise KeyError(fid)
    adj = {a["region"]: a for a in store.adjustments("selection_rule")}
    a = adj.get(fc["region"]) if (adj.get(fc["region"]) or {}).get("active") else adj.get("all regions")
    if not a or not a["active"]:
        return {"id": fid, "active": False, "message": "No active adjustment: not enough evidence, no change applied.",
                "cells": None}
    rule = (a["params"]["rule"][0],) + tuple(float(v) for v in a["params"]["rule"][1:])
    case = Case(fc["cells"], fc["days"])
    cells = []
    for k in range(1, fc["days"] + 1):
        P = case.p_comb[k - 1] if case.has_comb else case.p_ml[k - 1]
        sel = direct.select(P, rule) & case.elig[k - 1]
        cells += [{"day": k, "gx": case.x0 + int(x), "gy": case.y0 + int(y)} for x, y in zip(*np.nonzero(sel))]
    return {"id": fid, "active": True, "region": a["region"], "rule": list(rule), "cells": cells,
            "note": "preview only; /predict still returns the issued selection"}


# ============================================================== background scorer
class Worker:
    def __init__(self, store_fn=get_store, interval=None, initial_delay=None, src_fn=None):
        self.store_fn = store_fn
        self.interval = float(interval or os.environ.get("EQUIFIRE_FEEDBACK_INTERVAL_S", 3600))
        self.delay = float(initial_delay if initial_delay is not None else os.environ.get("EQUIFIRE_FEEDBACK_INITIAL_DELAY_S", 90))
        self.src_fn = src_fn or (lambda: Sources(allow_archive_load=False))
        self.stop_ev = threading.Event()
        self.thread = None
        self.last = {"last_run_utc": None, "last_scored": 0, "last_error": None, "runs": 0}

    def run_once(self, now=None) -> int:
        now = now or _now()
        store = self.store_fn()
        src = self.src_fn()
        n = 0
        for f in store.list_forecasts(2000):
            if f["source"] == CURATED:
                continue
            mat = forecast_maturity(store, f, now, src)
            if any(m["status"] == "ready" for m in mat):
                try:
                    n += len(score_forecast(store, f["id"], now, src)["newly_scored_days"])
                except Exception as e:  # never raise into the app
                    self.last["last_error"] = f"{type(e).__name__}: {e}"[:200]
        if n:
            refresh_adjustments(store)
        self.last.update(last_run_utc=iso(now), last_scored=n, runs=self.last["runs"] + 1)
        return n

    def _loop(self):
        if self.stop_ev.wait(self.delay):
            return
        while not self.stop_ev.is_set():
            try:
                self.run_once()
            except Exception as e:  # noqa: BLE001
                self.last["last_error"] = f"{type(e).__name__}: {e}"[:200]
            if self.stop_ev.wait(self.interval):
                return

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_ev.clear()
        self.thread = threading.Thread(target=self._loop, name="feedback-scorer", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_ev.set()

    def status(self):
        return {"running": bool(self.thread and self.thread.is_alive()), "interval_s": self.interval, **self.last}


WORKER: Worker | None = None


def enabled() -> bool:
    return (os.environ.get("EQUIFIRE_NO_FEEDBACK") != "1" and os.environ.get("EQUIFIRE_LITE") != "1"
            and os.environ.get("EQUIFIRE_NO_LOAD") != "1")


def install(app) -> None:
    """Start the hourly scorer with the app (lifespan hook, same pattern as prewarm). The router is included
    separately; this only adds the thread."""
    global WORKER
    if not enabled():
        return
    WORKER = Worker()
    inner = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(a):
        try:
            WORKER.start()
        except Exception as e:  # noqa: BLE001
            print(f"[feedback] could not start: {e}", flush=True)
        try:
            async with inner(a) as state:
                yield state
        finally:
            WORKER.stop()

    app.router.lifespan_context = lifespan


# ============================================================== FastAPI router
def make_router():
    from fastapi import APIRouter, Body, HTTPException, Query
    r = APIRouter(prefix="/feedback", tags=["feedback"])

    @r.post("/record")
    def rec(body: dict = Body(...)) -> dict:
        """Store the issued forecast (a /predict or /predict/simulate response, or {lat, lon, date, days})."""
        store = get_store()
        try:
            if "features" not in body and "guidance" not in body and {"lat", "lon", "date"} <= set(body):
                from . import ensemble
                body = ensemble.predict(float(body["lat"]), float(body["lon"]), str(body["date"]), int(body.get("days", 3)))
            return clean(record(store, body))
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        except Exception as e:
            if type(e).__name__ in ("NoIgnition", "PredictorUnavailable"):
                raise HTTPException(404 if type(e).__name__ == "NoIgnition" else 503, str(e)) from None
            raise

    @r.get("/analyse")
    def ana(id: str | None = None, lat: float | None = Query(None, ge=-90, le=90),
            lon: float | None = Query(None, ge=-180, le=180), date: str | None = None,
            days: int = Query(3, ge=1, le=3), refresh: bool = False) -> dict:
        store = get_store()
        seed_curated(store)
        fid = id
        if not fid:
            if lat is None or lon is None or not date:
                raise HTTPException(400, "give id=<forecast id> or lat, lon and date")
            fid = store.find_forecast(lat, lon, date, days)
            if fid is None:
                try:
                    from . import ensemble
                    fid = record(store, ensemble.predict(lat, lon, date, days))["id"]
                except Exception as e:
                    code = 404 if type(e).__name__ == "NoIgnition" else 503 if type(e).__name__ == "PredictorUnavailable" else 400
                    raise HTTPException(code, f"no stored forecast and none could be issued: {e}") from None
        try:
            return clean(analyse(store, fid, refresh=refresh))
        except KeyError:
            raise HTTPException(404, f"unknown forecast id {fid!r}; POST /feedback/record first") from None

    @r.get("/summary")
    def summ(include_curated: bool = False) -> dict:
        return clean(summary(get_store(), include_curated))

    @r.get("/forecasts")
    def lst(limit: int = Query(50, ge=1, le=500), include_curated: bool = True) -> dict:
        store = get_store()
        seed_curated(store)
        now = _now()
        fit = {a["region"][5:]: a["params"] for a in store.adjustments("physics_m")}
        rows = []
        for f in store.list_forecasts(limit):
            if f["source"] == CURATED and not include_curated:
                continue
            mat = forecast_maturity(store, f, now) if f["source"] != CURATED else []
            rows.append({k: f[k] for k in ("id", "created_utc", "source", "mode", "lat", "lon", "t0_date", "days",
                                           "region", "ignition_source", "n_cells", "incident")}
                        | {"methods": f["methods"], "physics_m_issued": (f.get("physics") or {}).get("m"),
                           "physics_m_refit": (fit.get(f["id"]) or {}).get("m_refit"),
                           "scored_days": len(store.scored_days(f["id"])) if f["source"] != CURATED else f["days"],
                           "status": status_of(mat) if mat else "scored", "maturity": mat})
        return clean({"schema": SCHEMA, "forecasts": rows})

    @r.get("/adjusted-selection")
    def adj(id: str) -> dict:
        try:
            return clean(adjusted_selection(get_store(), id))
        except KeyError:
            raise HTTPException(404, f"unknown forecast id {id!r}") from None

    return r


try:  # importable without fastapi (scripts/tests)
    router = make_router()
except ImportError:  # pragma: no cover
    router = None
