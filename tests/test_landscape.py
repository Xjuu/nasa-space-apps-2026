"""Tests for equifire.landscape. Pure functions only; the network test runs only with NET=1."""
import math
import os

import numpy as np
import pytest

from equifire import landscape as L


def _frac(**kw):
    v = np.zeros(len(L.WC_CODES))
    names = [L.WC_CLASSES[c][0] for c in L.WC_CODES]
    for k, x in kw.items():
        v[names.index(k)] = x
    return v


@pytest.mark.parametrize("kw,lat,fm,pc", [
    (dict(tree=1.0), 60, 10, 4),
    (dict(tree=1.0), 35, 9, 4),
    (dict(tree=1.0), 5, 8, 4),
    (dict(tree=0.5, grass=0.4, shrub=0.1), 5, 2, 2),
    (dict(shrub=0.7, grass=0.3), 30, 6, 3),
    (dict(grass=1.0), -18, 1, 1),
    (dict(grass=0.8, tree=0.2), -18, 2, 2),
    (dict(built=0.8, grass=0.2), 40, 91, 0),
    (dict(water=0.9, tree=0.1), 50, 98, 0),
    (dict(bare=1.0), 20, 99, 0),
    (dict(snow=1.0), 70, 92, 0),
    (dict(mangrove=1.0), 0, 98, 0),
    (dict(wetland=1.0), 50, 1, 1),        # weight 0.5 -> exactly burnable threshold
])
def test_fuel_mapping(kw, lat, fm, pc):
    f, p, crop, bf = L.fuel_from_fractions(_frac(**kw)[None], lat)
    assert int(f[0]) == fm and int(p[0]) == pc


def test_cropland_flag_and_nodata():
    f, p, crop, bf = L.fuel_from_fractions(np.stack([_frac(crop=0.9, grass=0.1), _frac()]), 45)
    assert f.tolist() == [1, 0] and crop.tolist() == [True, False]
    assert bf[0] == pytest.approx(1.0) and bf[1] == 0


def test_burnable_fraction_weights():
    _, _, _, bf = L.fuel_from_fractions(_frac(grass=0.5, water=0.25, moss=0.25)[None], 60)
    assert bf[0] == pytest.approx(0.5 + 0.125)


def test_slope_aspect_synthetic_plane():
    n, cell = 21, 100.0
    i, j = np.mgrid[0:n, 0:n]          # i = row (northward), j = col (eastward)
    z = 0.1 * j * cell                  # rises to the EAST at 10 %
    r = L.slope_aspect(z, cell)
    assert np.allclose(r["tan_slope"], 0.1, atol=1e-6)
    assert np.allclose(r["slope_deg"], math.degrees(math.atan(0.1)), atol=1e-4)
    assert np.allclose(r["upslope_deg"], 90, atol=1e-4)       # upslope = east
    assert np.allclose(r["aspect_deg"], 270, atol=1e-4)       # faces west (downslope)
    z2 = 0.2 * i * cell                 # rises to the NORTH -> faces south
    r2 = L.slope_aspect(z2, cell)
    assert np.allclose(r2["aspect_deg"], 180, atol=1e-4) and np.allclose(r2["upslope_deg"], 0, atol=1e-4)
    flat = L.slope_aspect(np.zeros((5, 5)), cell)
    assert np.isnan(flat["aspect_deg"]).all() and (flat["slope_deg"] == 0).all()


def test_grid_matches_physics_geometry():
    g = L.LGrid(48.9, -76.2, 20_000, 250)
    assert g.n == 161 and g.x0 == -80 * 250
    lon, lat = g.sub_axes(1)
    assert lon[80] == pytest.approx(-76.2) and lat[80] == pytest.approx(48.9)
    assert np.all(np.diff(lat) > 0) and np.all(np.diff(lon) > 0)   # row 0 south, col 0 west
    assert (lat[1] - lat[0]) / L.M_TO_DEG == pytest.approx(250)
    assert (lon[1] - lon[0]) * math.cos(math.radians(48.9)) / L.M_TO_DEG == pytest.approx(250)
    lon3, lat3 = g.sub_axes(3)
    assert lat3[3 * 80 + 1] == pytest.approx(48.9)                   # middle sub-sample = centre
    try:
        from backend.physics_spread import Grid
    except Exception:  # noqa: BLE001  physics module may be mid-edit
        return
    pg = Grid(48.9, -76.2, 20_000, 250)
    plon, plat = pg.centre_lonlat()
    assert pg.n == g.n and np.allclose(plon, lon) and np.allclose(plat, lat)


def test_rasterize_line_and_polygon():
    g = L.LGrid(0.0, 0.0, 2_000, 250)   # 17 x 17 cells
    lon, lat = g.sub_axes(1)
    k = 1 / L.M_TO_DEG
    # E-W road along the centre row, p = 0.6
    road = ("line", [(lon[0], lat[8]), (lon[-1], lat[8])], 0.6)
    # square lake covering cells rows 0..3, cols 0..3 (south-west corner)
    e = g.cell / 2 * L.M_TO_DEG
    x1, y1 = lon[3] + e, lat[3] + e
    x0, y0 = lon[0] - e, lat[0] - e
    lake = ("area", [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)], 1.0)
    lines, area = L.rasterize_features([road, lake], g)
    assert lines.shape == (g.n, g.n)
    assert np.allclose(lines[8], 0.6)                          # whole centre row
    assert lines[2].max() == 0 and lines[14].max() == 0
    assert np.allclose(area[:4, :4], 1.0) and area[5:, :].max() == 0 and area[:, 5:].max() == 0
    # two crossing line classes combine as 1-(1-p1)(1-p2)
    river = ("line", [(lon[4], lat[0]), (lon[4], lat[-1])], 0.5)
    lines2, _ = L.rasterize_features([road, river], g)
    assert lines2[8, 4] == pytest.approx(1 - 0.4 * 0.5)
    assert k > 0


def test_osm_parsing_and_width():
    js = {"elements": [
        {"type": "way", "tags": {"highway": "motorway"}, "geometry": [{"lat": 0, "lon": 0}, {"lat": 0, "lon": 0.01}]},
        {"type": "way", "tags": {"waterway": "river", "width": "60"}, "geometry": [{"lat": 0, "lon": 0}, {"lat": 0.01, "lon": 0}]},
        {"type": "way", "tags": {"natural": "water"}, "geometry": [{"lat": 0, "lon": 0}, {"lat": 0, "lon": 0.01},
                                                                   {"lat": 0.01, "lon": 0.01}, {"lat": 0, "lon": 0}]},
        {"type": "way", "tags": {"highway": "footway"}, "geometry": [{"lat": 0, "lon": 0}, {"lat": 0, "lon": 0.01}]},
    ]}
    feats, counts = L.osm_to_features(js)
    kinds = [(k, round(p, 3)) for k, _, p in feats]
    assert kinds == [("line", 0.85), ("line", round(min(0.9, 60 / 66), 3)), ("area", 1.0)]
    assert counts == {"highway=motorway": 1, "waterway=river": 1, "natural=water": 1}
    assert L.barrier_p("highway", "track", {}) == 0.15


def test_dryness_and_green():
    assert L.dryness_from_ndmi(0.4) == 0 and L.dryness_from_ndmi(-0.2) == 1
    assert L.dryness_from_ndmi(0.1) == pytest.approx(0.5)
    assert L.green_flag(0.6, 0.3) and not L.green_flag(0.6, 0.1) and not L.green_flag(0.3, 0.3)


def test_tile_names():
    assert L.wc_tile_name(48, -78) == "N48W078" and L.wc_tile_name(-21, 123) == "S21E123"
    assert L.dem_tile_name(-19, 124) == "Copernicus_DSM_COG_10_S19_00_E124_00_DEM"
    assert L._tiles(124.2, -18.5, 124.6, -18.1, 3) == [(-21, 123)]
    assert len(L._tiles(-0.5, -0.5, 0.5, 0.5, 1)) == 4


@pytest.mark.skipif(os.environ.get("NET") != "1", reason="network test; run with NET=1")
def test_network_small():
    r = L.get_landscape(-18.3, 124.4, 3, 250, date="2026-11-01", layers=("landcover", "terrain"))
    assert r["fuel_model"].shape == (25, 25)
    assert r["meta"]["layers"]["landcover"]["coverage_pct"] > 90
    assert np.isfinite(r["elevation"]).all() and 0 < np.nanmean(r["elevation"]) < 500
