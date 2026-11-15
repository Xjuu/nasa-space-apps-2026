"""Tests for backend/replay.py: scoring on synthetic cells, no-leakage guarantee, schema. No network, no GPU.

    .venv/bin/python -m pytest backend/test_replay.py -q
"""
from __future__ import annotations

import json
import os

os.environ.setdefault("EQUIFIRE_NO_LOAD", "1")

import numpy as np
import pandas as pd
import pytest
from scipy import ndimage

from backend import replay
from equifire import direct
from equifire.common import EPOCH, GRID, MODELS
from equifire.direct import S, W


# ------------------------------------------------------------------ helpers
def _m(cells):
    a = np.zeros((S, S), bool)
    for x, y in cells:
        a[x, y] = True
    return a


def _walk_forward_tol(p, t):  # verbatim formula of walk_forward._tol (importing walk_forward pulls in the trainer)
    if not p.any() and not t.any():
        return 1.0
    if not p.any() or not t.any():
        return 0.0
    k = np.ones((3, 3), bool)
    return ((p & ndimage.binary_dilation(t, k)).sum() + (t & ndimage.binary_dilation(p, k)).sum()) \
        / (p.sum() + t.sum())


# ------------------------------------------------------------------ scoring on synthetic cells
def test_strict_counts():
    pred = _m([(5, 5), (5, 6), (10, 10), (20, 20)])
    truth = _m([(5, 5), (10, 10), (30, 30)])
    s = replay.strict_counts(pred, truth)
    assert (s["hits"], s["misses"], s["false_alarms"]) == (2, 1, 2)
    assert s["predicted_cells"] == 4 and s["observed_cells"] == 3
    assert s["precision"] == 0.5 and s["recall"] == pytest.approx(0.6667, abs=1e-4)
    assert s["iou"] == pytest.approx(2 / 5, abs=1e-4)


def test_strict_empty_cases():
    e = _m([])
    assert replay.strict_counts(e, e)["iou"] == 1.0
    s = replay.strict_counts(e, _m([(1, 1)]))
    assert s["precision"] is None and s["recall"] == 0.0 and s["iou"] == 0.0
    s = replay.strict_counts(_m([(1, 1)]), e)
    assert s["recall"] is None and s["precision"] == 0.0 and s["false_alarms"] == 1


def test_tolerant_matches_walk_forward_formula():
    rng = np.random.default_rng(1)
    for _ in range(20):
        p = rng.random((S, S)) < 0.03
        t = rng.random((S, S)) < 0.03
        assert replay.tolerant_counts(p, t)["within_5km_score"] == pytest.approx(
            _walk_forward_tol(p, t), abs=1e-4)
    e = _m([])
    assert replay.tolerant_counts(e, e)["within_5km_score"] == 1.0
    assert replay.tolerant_counts(e, _m([(3, 3)]))["within_5km_score"] == 0.0


def test_tolerance_is_one_cell():
    t = _m([(10, 10)])
    near = replay.tolerant_counts(_m([(11, 11)]), t)       # diagonal neighbour: within 1 cell
    far = replay.tolerant_counts(_m([(12, 10)]), t)        # 2 cells away: outside
    assert near["predicted_matched"] == 1 and near["within_5km_score"] == 1.0
    assert far["predicted_matched"] == 0 and far["within_5km_score"] == 0.0
    assert replay.strict_counts(_m([(11, 11)]), t)["hits"] == 0


def test_hits_misses_false_alarms_partition():
    rng = np.random.default_rng(7)
    p, t = rng.random((S, S)) < 0.05, rng.random((S, S)) < 0.05
    s = replay.strict_counts(p, t)
    assert s["hits"] + s["misses"] == s["observed_cells"]
    assert s["hits"] + s["false_alarms"] == s["predicted_cells"]
    codes, tol = replay.classify(p, t)
    assert (codes == 1).sum() == s["hits"] and (codes == 2).sum() == s["misses"]
    assert (codes == 3).sum() == s["false_alarms"]
    assert (tol == 5).sum() + (tol == 1).sum() >= s["hits"]


def test_unverifiable_cells_not_counted_as_false_alarms_in_adjusted():
    pred = _m([(5, 5), (8, 8), (9, 9)])
    truth = _m([(5, 5)])
    unver = _m([(8, 8), (5, 5), (20, 20)])         # (5,5) is a hit even if obscured: stays a hit
    r = replay.score_day(pred, truth, unver)
    assert r["strict"]["false_alarms"] == 2          # headline keeps them conservative
    adj = r["adjusted"]
    assert adj["unverifiable_predicted_cells_strict"] == 1
    assert adj["strict"]["false_alarms"] == 1 and adj["strict"]["hits"] == 1
    s, _ = replay.classify(pred, truth, unver)
    assert s[8, 8] == 4 and s[9, 9] == 3 and s[5, 5] == 1
    assert replay.score_day(pred, truth)["adjusted"] is None


def test_tolerant_classes_near_hit():
    pred, truth = _m([(10, 10)]), _m([(11, 10)])
    s, tol = replay.classify(pred, truth)
    assert s[10, 10] == 3 and s[11, 10] == 2
    assert tol[10, 10] == 5 and tol[11, 10] == 5


def test_observable_scoring_masks_both_sets_and_keeps_full_scores():
    pred = _m([(5, 5), (8, 8), (9, 9)])
    truth = _m([(5, 5), (8, 8), (20, 20)])
    mask = np.ones((S, S), bool)
    mask[8, 8] = mask[20, 20] = False
    r = replay.score_day(pred, truth, ~mask, observable=mask, unknown=_m([]))
    assert r["strict"] == replay.strict_counts(pred, truth)
    assert r["observable"]["strict"]["hits"] == 1  # cloudy hit also excluded: no outcome-based exception
    assert r["observable"]["excluded"] == {"predicted_cells": 1, "observed_cells": 2}
    assert r["observable"]["coverage"]["observable_cells"] == S * S - 2
    reverse = replay.observable_scores(truth, pred, mask, unknown=_m([]))
    assert reverse["strict"]["iou"] == r["observable"]["strict"]["iou"]
    assert reverse["tolerant"]["within_5km_score"] == r["observable"]["tolerant"]["within_5km_score"]


def test_observable_tolerance_cannot_match_an_excluded_endpoint():
    pred, truth = _m([(10, 10)]), _m([(11, 10)])
    assert replay.tolerant_counts(pred, truth)["within_5km_score"] == 1.0
    for excluded in [(10, 10), (11, 10)]:
        mask = np.ones((S, S), bool)
        mask[excluded] = False
        conditional = replay.observable_scores(pred, truth, mask)
        assert conditional["tolerant"]["within_5km_score"] == 0.0
        assert conditional["tolerant"]["predicted_matched"] == 0
        assert conditional["tolerant"]["observed_matched"] == 0


def test_observable_missing_and_zero_coverage_do_not_claim_perfect_scores():
    empty = _m([])
    missing = replay.observable_scores(empty, empty)
    zero = replay.observable_scores(empty, empty, empty, unknown=np.ones((S, S), bool))
    assert missing["available"] is False and missing["coverage"]["observable_cells"] is None
    assert zero["available"] is True and zero["coverage"]["observable_cells"] == 0
    assert zero["coverage"]["unknown_cells"] == S * S
    for result in [missing, zero]:
        assert result["scored"] is False and result["strict"] is None and result["tolerant"] is None


def test_observable_mask_shape_and_unknown_overlap_rejected():
    empty = _m([])
    with pytest.raises(ValueError):
        replay.observable_scores(empty, empty, np.ones((2, 2), bool))
    with pytest.raises(ValueError):
        replay.observable_scores(empty, empty, np.ones_like(empty), unknown=np.ones_like(empty))


def test_observable_elsewhere_but_no_evaluable_cells_is_unscored():
    empty, eligible = _m([]), _m([(20, 20)])
    observable = np.ones((S, S), bool)
    observable[20, 20] = False
    result = replay.observable_scores(empty, empty, observable, eligible=eligible)
    assert result["coverage"]["observable_cells"] == S * S - 1
    assert result["coverage"]["observable_eligible_cells"] == 0
    assert result["scored"] is False and result["strict"] is None


def test_observation_mask_serialization_exact_and_corruption_unavailable():
    rng = np.random.default_rng(16)
    mask = rng.random((3, S, S)) > 0.5
    unknown = ~mask & (rng.random((3, S, S)) > 0.5)
    encoded = replay._encode_observation_masks(mask, unknown)
    decoded = replay._decode_observation_masks({"spatial_mask": encoded}, 3)
    assert np.array_equal(mask, decoded[0]) and np.array_equal(unknown, decoded[1])
    encoded["observable"][0] = "not-base64"
    assert replay._decode_observation_masks({"spatial_mask": encoded}, 3) == (None, None)


# ------------------------------------------------------------------ synthetic archive for leakage tests
T0_DATE = "2025-03-10"
CX, CY = 3000, 2000


def _t0():
    return int((np.datetime64(T0_DATE) - EPOCH).astype(int))


def _synthetic_rows(rng, future: str):
    """Fire index rows around (CX, CY). future in {'real', 'none', 'tampered'}."""
    t0 = _t0()
    rows = []
    for d in range(t0 - 12, t0 + 1):                                    # history + init day
        r = 2 + (d - (t0 - 12)) * 0.15
        for x in range(-6, 7):
            for y in range(-6, 7):
                if x * x + y * y <= r * r and rng.random() < 0.8:
                    rows.append((d, CX + x + (d - t0) // 4, CY + y, 20.0 + 3 * rng.random(), 3.0, 3))
    if future != "none":
        rng2 = np.random.default_rng(99 if future == "tampered" else 5)
        for d in range(t0 + 1, t0 + 8):
            r = 5 + (d - t0) * (1.5 if future == "real" else 0.4)
            for x in range(-14, 15):
                for y in range(-14, 15):
                    if x * x + y * y <= r * r and rng2.random() < (0.7 if future == "real" else 0.97):
                        rows.append((d, CX + x, CY + y, 30.0, 4.0, 3))
    return np.array(rows, float)


def _fire_index(rows):
    day, gx, gy, frp, n, dn = (rows[:, i] for i in range(6))
    return direct.FireIndex(_v=(day.astype(int), gx.astype(int), gy.astype(int), frp.astype(np.float32),
                                n.astype(np.float32), dn.astype(np.int8)),
                            _m=(day.astype(int)[::3], gx.astype(int)[::3], gy.astype(int)[::3],
                                frp.astype(np.float32)[::3]))


def _lat_lon():
    return CY * GRID - 90 + GRID / 2, CX * GRID - 180 + GRID / 2


def _stub_physics(fi, case, **kw):
    days = kw.get("days", 3)
    # depends only on fi rows <= t0 (like the real thing): fraction from the init-day footprint size
    t0, cx, cy = case
    n0 = len(fi.v.query(t0, t0, cx, cy))
    frac = np.zeros((3, days, S, S), np.float32)
    frac[:] = min(n0 / 400.0, 0.5)
    return {"meta": {"ok": True, "m": 1.0, "fuel": "grass", "cal_applied": False, "cal_confidence": "none"},
            "frac": frac, "cover": np.ones((S, S), np.float32), "arr_h": np.full((S, S), np.inf, np.float32)}


needs_models = pytest.mark.skipif(
    not (MODELS / "ensemble_blend_2025_meta.json").exists() or not (MODELS / "wf_2025.json").exists(),
    reason="fitted ML/blender artifacts not present")


@pytest.fixture(scope="module")
def indices():
    rng = np.random.default_rng
    return {k: _fire_index(_synthetic_rows(rng(3), k)) for k in ("real", "none", "tampered")}


def test_asof_index_physically_drops_future(indices):
    full = indices["real"]
    t0 = _t0()
    assert int(full.v.day.max()) > t0
    a = replay.asof_index(full, t0, CX, CY)
    assert int(a.v.day.max()) == t0 and int(a.m.day.max()) <= t0
    assert int(a.day.max()) == t0
    assert len(a.v.query(t0 + 1, t0 + 30, CX, CY)) == 0
    # history preserved exactly (same rows <= t0 in the window)
    for d in (t0 - 5, t0):
        assert sorted(a.v.gx[a.v.query(d, d, CX, CY)]) == sorted(full.v.gx[full.v.query(d, d, CX, CY)])


def test_asof_subcell_guard():
    class Sub:
        def lookup(self, day, gx, gy):
            return np.zeros(len(day)), np.zeros(len(day))
    g = replay.AsOfSubcell(Sub(), 100)
    g.lookup(np.array([100, 99]), None, None)
    with pytest.raises(AssertionError):
        g.lookup(np.array([101]), None, None)


@needs_models
def test_forecast_identical_without_future_observations(indices):
    """The core guarantee: the issued forecast is the same whether the future was observed, absent or garbage."""
    lat, lon = _lat_lon()
    t0 = _t0()
    out = {}
    for k, full in indices.items():
        asof = replay.asof_index(full, t0, CX, CY)
        r = replay.issue_forecast(lat, lon, T0_DATE, 3, "iou", "ml_physics", asof, physics_fn=_stub_physics)
        out[k] = r
    assert out["real"]["method"] == "ml_physics"
    ref = json.dumps({k: out["real"][k] for k in ("geojson", "model", "warnings", "cx", "cy")}, sort_keys=True)
    for k in ("none", "tampered"):
        got = json.dumps({x: out[k][x] for x in ("geojson", "model", "warnings", "cx", "cy")}, sort_keys=True)
        assert got == ref, f"issued forecast changed with future observations ({k})"
        for m in ("comb", "ml"):
            assert np.array_equal(out["real"]["sel"][m], out[k]["sel"][m])
        for m in ("ml", "comb"):
            assert np.array_equal(out["real"]["arr"][m], out[k]["arr"][m], equal_nan=True)
    assert len(out["real"]["geojson"]["features"]) > 0


@needs_models
def test_forecaster_receives_only_asof_data(indices):
    """Spy on the object handed to the predictor: it contains no row after the init day."""
    seen = {}

    def spy(lat, lon, date, days, objective, **kw):
        fi = kw["fi"]
        seen["max_v"], seen["max_m"] = int(fi.v.day.max()), int(fi.m.day.max())
        from backend import ensemble
        return ensemble.predict(lat, lon, date, days, objective, **kw)

    lat, lon = _lat_lon()
    t0 = _t0()
    asof = replay.asof_index(indices["real"], t0, CX, CY)
    replay.issue_forecast(lat, lon, T0_DATE, 3, "iou", "ml_physics", asof, predict_fn=spy,
                          physics_fn=_stub_physics)
    assert seen["max_v"] == t0 and seen["max_m"] <= t0


@needs_models
def test_run_replay_schema_and_reveal_after_forecast(indices):
    lat, lon = _lat_lon()
    res = replay.run_replay(lat, lon, T0_DATE, 3, "iou", "auto", full=indices["real"],
                            physics_fn=_stub_physics, with_quality=False)
    json.dumps(res)                                              # serialisable
    for key in ("schema", "query", "init", "centre", "method", "leakage_audit", "issued", "reveal",
                "limitations", "generated_utc"):
        assert key in res
    assert res["schema"] == replay.SCHEMA
    assert res["init"]["information_cutoff_utc"] == f"{T0_DATE}T23:59:59Z"
    assert res["leakage_audit"]["truth_used_in_forecast"] is False
    rv = res["reveal"]
    assert rv["revealed_through_day"] == 3 and len(rv["per_day"]) == 3
    assert rv["observation_quality"]["available"] is False
    for row in rv["per_day"]:
        assert row["scored"] and row["observed_new_cells"] is not None
        for name, sc in row["methods"].items():
            s = sc["strict"]
            assert s["hits"] + s["misses"] == s["observed_cells"] == row["observed_new_cells"]
            assert s["hits"] + s["false_alarms"] == s["predicted_cells"]
            assert 0 <= sc["tolerant"]["within_5km_score"] <= 1
        assert {"ml_physics", "ml", "baseline_ring"} <= set(row["methods"])
    # map-ready classes: every feature has gx/gy and one class per day per method
    feats = rv["geojson"]["features"]
    assert feats
    for f in feats:
        p = f["properties"]
        assert f["geometry"]["type"] == "Polygon" and "gx" in p and "gy" in p
        for m, z in p["classes"].items():
            assert len(z["strict"]) == 3 and len(z["tolerant"]) == 3
            assert set(z["strict"]) <= {"none", "hit", "miss", "false_alarm", "unverifiable"}
            assert set(z["tolerant"]) <= {"none", "hit", "miss", "false_alarm", "unverifiable", "near_hit"}
    d1 = rv["per_day"][0]["methods"]["ml_physics"]["strict"]
    cnt = {c: sum(1 for f in feats if f["properties"]["classes"]["ml_physics"]["strict"][0] == c)
           for c in ("hit", "miss", "false_alarm")}
    assert (cnt["hit"], cnt["miss"], cnt["false_alarm"]) == (d1["hits"], d1["misses"], d1["false_alarms"])
    # the reveal reflects the observed future; a forecast from the tampered future is the same but scored differently
    res2 = replay.run_replay(lat, lon, T0_DATE, 3, "iou", "auto", full=indices["tampered"],
                             physics_fn=_stub_physics, with_quality=False)
    assert json.dumps(res["issued"]["geojson"], sort_keys=True) == json.dumps(res2["issued"]["geojson"],
                                                                             sort_keys=True)
    assert res["reveal"]["per_day"][0]["observed_new_cells"] != res2["reveal"]["per_day"][0]["observed_new_cells"]


@needs_models
def test_no_later_observations_is_an_error(indices):
    lat, lon = _lat_lon()
    with pytest.raises(replay.NoObservations):
        replay.run_replay(lat, lon, T0_DATE, 3, "iou", "auto", full=indices["none"],
                          physics_fn=_stub_physics, with_quality=False)


def test_ml_only_for_years_without_blender(indices):
    """2023: no blender fitted before 2023 -> ML-only variant; forcing ml_physics is refused."""
    from backend import ensemble
    if not (MODELS / "wf_2023.json").exists():
        pytest.skip("wf_2023 missing")
    t0 = int((np.datetime64("2023-03-10") - EPOCH).astype(int))
    rows = _synthetic_rows(np.random.default_rng(3), "real")
    shift = t0 - _t0()
    rows[:, 0] += shift
    full = _fire_index(rows)
    lat, lon = _lat_lon()
    res = replay.run_replay(lat, lon, "2023-03-10", 3, "iou", "auto", full=full, with_quality=False)
    assert res["method"]["used"] == "ml" and "baseline_ring" in res["reveal"]["per_day"][0]["methods"]
    assert "ml_physics" not in res["reveal"]["per_day"][0]["methods"]
    with pytest.raises(ensemble.PredictorUnavailable):
        replay.run_replay(lat, lon, "2023-03-10", 3, "iou", "ml_physics", full=full, with_quality=False)


# ------------------------------------------------------------------ cloud proxy + curated incidents + router
def test_observation_quality_marks_cloudy_cells():
    import datetime as dt
    n = 5
    clat, clon = 10.0, -30.0

    def fetch(lats, lons, d1, d2, variables):
        hours = np.arange(np.datetime64(d1.isoformat(), "h"), np.datetime64((d2 + dt.timedelta(days=1)).isoformat(), "h"))
        cc = np.full((len(lats), len(hours)), 100.0, np.float32)     # fully overcast everywhere, all days
        cc[:, 48:] = 0.0                                              # day 3 clear
        return hours, {"cloud_cover": cc}

    q = replay.observation_quality(clat, clon, dt.date(2025, 3, 10), 3, int(np.floor((clon + 180) / GRID)),
                                   int(np.floor((clat + 90) / GRID)), fetch=fetch)
    assert q["available"] and q["_unverifiable"].shape == (3, S, S)
    assert q["_unverifiable"][0].all() and q["_unverifiable"][1].all() and not q["_unverifiable"][2].any()
    assert q["per_day"][0]["window_fraction_obscured"] == 1.0


def test_observation_quality_unavailable_on_error():
    import datetime as dt

    def boom(*a, **k):
        raise RuntimeError("429 Too Many Requests")

    q = replay.observation_quality(10.0, -30.0, dt.date(2025, 3, 10), 3, 3000, 2000, fetch=boom)
    assert q["available"] is False and "429" in q["error"]


def test_missing_cloud_pass_is_unknown_not_clear_or_obscured():
    import datetime as dt
    def fetch(lats, lons, d1, d2, variables):
        hours = np.arange(np.datetime64(d1.isoformat(), "h"), np.datetime64((d2 + dt.timedelta(days=1)).isoformat(), "h"))
        cc = np.full((len(lats), len(hours)), np.nan, np.float32)
        cc[:, :12] = 100.0  # at lon0, only the nominal night pass has data and it is cloudy
        return hours, {"cloud_cover": cc}
    q = replay.observation_quality(0.0, 0.0, dt.date(2025, 3, 10), 1, 3600, 1800, fetch=fetch)
    assert q["available"]
    assert not q["_observable"].any()
    assert q["_unknown"].all()
    json.dumps(replay._public(q), allow_nan=False)


def test_observable_mask_independent_of_fire_truth(indices):
    lat, lon = _lat_lon()
    mask = np.ones((3, S, S), bool)
    mask[:, :S//2] = False
    unknown = np.zeros_like(mask)
    masks_before = mask.copy()
    def quality(*args):
        return {"available": True, "_unverifiable": ~mask, "_observable": mask, "_unknown": unknown,
                "spatial_mask": replay._encode_observation_masks(mask, unknown)}
    if not (MODELS / "ensemble_blend_2025_meta.json").exists():
        pytest.skip("model absent")
    out = [replay.run_replay(lat, lon, T0_DATE, full=indices[k], physics_fn=_stub_physics, quality_fn=quality)
           for k in ["real", "tampered"]]
    assert out[0]["issued"] == out[1]["issued"]
    assert out[0]["reveal"]["observation_quality"] == out[1]["reveal"]["observation_quality"]
    assert np.array_equal(mask, masks_before)
    for replayed in out:
        for row in replayed["reveal"]["per_day"]:
            coverages = [v["observable"]["coverage"] for v in row["methods"].values()]
            assert all(c == coverages[0] for c in coverages)


def _cached_example():
    pred, truth = _m([(20, 19)]), _m([(20, 21)])
    initial = _m([(20, 20)])
    ring = replay._eligible(ndimage.distance_transform_cdt(~initial, metric="chessboard"), 1)
    mask = np.ones((1, S, S), bool); mask[0, 20, 21] = False
    cx, cy = CX, CY
    feats = [{"properties": {"gx": cx - W + 20, "gy": cy - W + y, "observed_first_day": first,
              "classes": {"ml": {"strict": [cls], "tolerant": [cls]}}}} for y, first, cls in
             [(19, None, "false_alarm"), (21, 1, "miss")]]
    res = {"centre": {"gx": cx, "gy": cy, "lat": 10.0, "lon": -30.0}, "init": {"date": T0_DATE},
           "issued": {"example": "unchanged"}, "reveal": {"observation_quality": {"available": True,
           "spatial_mask": replay._encode_observation_masks(mask, np.zeros_like(mask))},
           "geojson": {"features": feats}, "burning_at_init": {"features": [{"geometry": replay._poly(cx, cy)}]},
           "per_day": [{"day": 1, "scored": True, "methods": {"ml": replay.score_day(pred, truth),
                       "baseline_ring": replay.score_day(ring, truth)}}]}}
    return res


def test_cached_upgrade_no_network_no_forecast_changes_and_same_mask(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("upgrade must not call HTTP or a forecast model")
    monkeypatch.setattr(replay, "issue_forecast", forbidden)
    monkeypatch.setattr("requests.get", forbidden)
    saved = _cached_example()
    before = json.loads(json.dumps(saved))
    replay.add_cached_observability(saved)
    assert saved["issued"] == before["issued"]
    for name, scored in saved["reveal"]["per_day"][0]["methods"].items():
        for original in ["strict", "tolerant", "adjusted"]:
            assert scored[original] == before["reveal"]["per_day"][0]["methods"][name][original]
        assert scored["observable"]["available"]
        assert scored["observable"]["excluded"]["observed_cells"] == 1
    assert saved["reveal"]["per_day"][0]["methods"]["ml"]["observable"]["coverage"] == saved["reveal"]["per_day"][0]["methods"]["baseline_ring"]["observable"]["coverage"]


def test_cached_upgrade_missing_mask_never_uses_aggregates_or_network(monkeypatch):
    monkeypatch.setattr("requests.get", lambda *a, **k: pytest.fail("no network allowed"))
    monkeypatch.setattr(replay, "_cached_cloud_hourly", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    saved = _cached_example()
    saved["reveal"]["observation_quality"] = {"available": True, "per_day": [{"window_fraction_unverifiable_cumulative": 0.0}]}
    replay.add_cached_observability(saved)
    result = saved["reveal"]["per_day"][0]["methods"]["ml"]["observable"]
    assert result["available"] is False and result["strict"] is None


def test_cached_upgrade_corrupt_cell_sets_unavailable_even_with_mask():
    saved = _cached_example()
    saved["reveal"]["geojson"]["features"] = []
    replay.add_cached_observability(saved)
    result = saved["reveal"]["per_day"][0]["methods"]["ml"]["observable"]
    assert result["available"] is False and "do not reproduce" in result["reason"]


def test_curated_incidents_file_schema():
    incs = replay.load_incidents()
    assert 3 <= len(incs) <= 6
    ids = set()
    for i in incs:
        for k in ("id", "name", "lat", "lon", "date", "init_time_utc", "description", "why_chosen"):
            assert k in i, (i.get("id"), k)
        assert i["id"] not in ids
        ids.add(i["id"])
        if int(i["date"][:4]) < 2024:
            assert i["expected_replay_mode"] == "ml"       # ensemble.py refuses ML+physics before 2024
        av = i.get("availability")
        assert av and av["ok"] and min(av["viirs_cells_active_days_after"]) > 0
        assert av["observed_new_cells_cumulative"][-1] > 0


def test_router_endpoints():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(replay.router)
    c = TestClient(app)
    r = c.get("/replay/incidents")
    assert r.status_code == 200 and r.json()["incidents"]
    assert c.get("/replay/run?incident=nope").status_code == 404
    assert c.get("/replay/run").status_code == 400
    assert c.get("/replay/run?lat=1&lon=1&date=2025-01-01&cached_only=1").status_code == 404
    first = r.json()["incidents"][0]["id"]
    if replay.cache_path(first, "iou").exists():
        j = c.get(f"/replay/run?incident={first}&cached_only=1")
        assert j.status_code == 200 and j.json()["cached"] is True and j.json()["schema"] == replay.SCHEMA
        assert j.json()["reveal"]["revealed_through_day"] >= 1


@needs_models
def test_negative_control_leaky_component_is_neutralised_by_asof(indices):
    """A deliberately leaky physics stub reads future detections. Handed the raw archive it changes the forecast
    (so the test can detect leaks); handed the as-of index it cannot."""
    def leaky(fi, case, **kw):
        out = _stub_physics(fi, case, **kw)
        t0, cx, cy = case
        out["frac"] = out["frac"] + min(len(fi.v.query(t0 + 1, t0 + 3, cx, cy)) / 500.0, 0.4)
        return out

    lat, lon = _lat_lon()
    t0 = _t0()
    res = {}
    for name, full in (("real", indices["real"]), ("none", indices["none"])):
        raw = replay.issue_forecast(lat, lon, T0_DATE, 3, "iou", "ml_physics", full, physics_fn=leaky)
        safe = replay.issue_forecast(lat, lon, T0_DATE, 3, "iou", "ml_physics",
                                     replay.asof_index(full, t0, CX, CY), physics_fn=leaky)
        res[name] = (raw, safe)
    assert not np.array_equal(res["real"][0]["arr"]["comb"], res["none"][0]["arr"]["comb"], equal_nan=True)
    assert np.array_equal(res["real"][1]["arr"]["comb"], res["none"][1]["arr"]["comb"], equal_nan=True)
