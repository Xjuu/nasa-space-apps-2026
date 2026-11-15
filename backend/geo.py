"""GeoJSON helpers for 0.05 degree grid cells."""
from __future__ import annotations

import math

import numpy as np
from scipy import ndimage

from equifire.common import GRID

KM_PER_DEG = 111.32


def lonlat_to_cell(lat: float, lon: float) -> tuple[int, int]:
    return int(math.floor((lon + 180) / GRID)), int(math.floor((lat + 90) / GRID))


def cell_bounds(gx: int, gy: int) -> tuple[float, float, float, float]:
    lon0, lat0 = gx * GRID - 180, gy * GRID - 90
    return round(lon0, 6), round(lat0, 6), round(lon0 + GRID, 6), round(lat0 + GRID, 6)


def cell_center(gx: int, gy: int) -> tuple[float, float]:
    """(lat, lon) of the cell centre."""
    return round(gy * GRID - 90 + GRID / 2, 6), round(gx * GRID - 180 + GRID / 2, 6)


def cell_area_km2(gy: int) -> float:
    lat = gy * GRID - 90 + GRID / 2
    return (GRID * KM_PER_DEG) ** 2 * math.cos(math.radians(lat))


def cell_polygon(gx: int, gy: int) -> dict:
    x0, y0, x1, y1 = cell_bounds(gx, gy)
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


def feature(geometry: dict, **props) -> dict:
    return {"type": "Feature", "geometry": geometry, "properties": props}


def collection(features: list[dict], **extra) -> dict:
    return {"type": "FeatureCollection", "features": features, **extra}


def _hull(points: np.ndarray) -> np.ndarray:
    """Convex hull (monotone chain) of 2-D points, counter-clockwise, not closed."""
    pts = np.unique(points, axis=0)
    if len(pts) <= 2:
        return pts
    pts = pts[np.lexsort((pts[:, 1], pts[:, 0]))]

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(tuple(p))
    for p in pts[::-1]:
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(tuple(p))
    return np.array(lower[:-1] + upper[:-1])


def hull_rings(cells: list[tuple[int, int]]) -> list[list[list[float]]]:
    """One closed [lon, lat] ring per 8-connected group of grid cells: convex hull of the cell corners."""
    if not cells:
        return []
    c = np.array(cells)
    x0, y0 = c.min(0)
    grid = np.zeros(tuple(c.max(0) - (x0, y0) + 1), bool)
    grid[c[:, 0] - x0, c[:, 1] - y0] = True
    lab, n = ndimage.label(grid, structure=np.ones((3, 3)))
    rings = []
    for i in range(1, n + 1):
        xs, ys = np.nonzero(lab == i)
        gx, gy = xs + x0, ys + y0
        corners = np.concatenate([np.c_[gx + dx, gy + dy] for dx in (0, 1) for dy in (0, 1)])
        h = _hull(corners)
        ring = [[round(float(x) * GRID - 180, 6), round(float(y) * GRID - 90, 6)] for x, y in h]
        ring.append(ring[0])
        rings.append(ring)
    return rings
