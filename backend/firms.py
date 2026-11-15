"""NASA FIRMS API integration for EquiFire.

Fetches active fire observations from NASA LANCE / FIRMS using FIRMS_MAP_KEY from .env.
Supports near-real-time (NRT) satellite feeds (VIIRS NOAA-20, NOAA-21, SNPP, MODIS),
area queries (enforcing max 25,000 km²), and identification of the fire seen last
in each spot over the last 24 hours.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import math
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT_DIR / ".env"
CACHE_DIR = Path(__file__).resolve().parent / ".cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

FIRMS_BASE = "https://firms.modaps.eosdis.nasa.gov/api"
DEFAULT_SOURCE = "VIIRS_NOAA20_NRT"
EARTH_RADIUS_KM = 6371.0088
MAX_AREA_KM2 = 25000.0
CACHE_TTL_SECONDS = 900  # 15 minutes


def get_map_key() -> str:
    """Read FIRMS_MAP_KEY from environment or .env file."""
    key = os.environ.get("FIRMS_MAP_KEY")
    if key:
        return key.strip()
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                k, sep, v = line.partition("=")
                if sep and k.strip() == "FIRMS_MAP_KEY":
                    val = v.strip().strip("\"'")
                    os.environ["FIRMS_MAP_KEY"] = val
                    return val
    return ""


def geodesic_area_km2(west: float, south: float, east: float, north: float) -> float:
    """Calculate geodesic surface area of a bounding box on the WGS84 sphere in km²."""
    d_lon = (east - west) % 360.0
    d_lon_rad = math.radians(d_lon)
    sin_diff = abs(math.sin(math.radians(north)) - math.sin(math.radians(south)))
    return EARTH_RADIUS_KM * EARTH_RADIUS_KM * d_lon_rad * sin_diff


# Simple in-memory cache: key -> (timestamp, data)
_CACHE: dict[str, tuple[float, Any]] = {}


def _fetch_url(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "EquiFire/1.0 (NASA Space Apps)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def fetch_firms_csv(source: str, area_str: str, day_range: int = 1, date_str: str | None = None) -> str:
    """Fetch raw CSV from FIRMS API with caching."""
    key = get_map_key()
    if not key:
        raise RuntimeError("FIRMS_MAP_KEY is missing from .env and environment.")

    cache_key = f"{source}_{area_str}_{day_range}_{date_str or 'recent'}"
    now = time.time()
    if cache_key in _CACHE:
        ts, data = _CACHE[cache_key]
        if now - ts < CACHE_TTL_SECONDS:
            return data

    if date_str:
        url = f"{FIRMS_BASE}/area/csv/{key}/{source}/{area_str}/{day_range}/{date_str}"
    else:
        url = f"{FIRMS_BASE}/area/csv/{key}/{source}/{area_str}/{day_range}"

    try:
        csv_text = _fetch_url(url)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise RuntimeError(f"FIRMS API key invalid or unauthorized (HTTP {e.code})") from e
        raise RuntimeError(f"FIRMS API HTTP {e.code}: {e.reason}") from e
    except Exception as e:
        raise RuntimeError(f"Failed to reach NASA FIRMS: {e}") from e

    # Check for FIRMS error messages in body
    first_line = csv_text.split("\n", 1)[0].strip().lower()
    if any(err in first_line for err in ("invalid", "exceeded", "error", "no transaction")):
        raise RuntimeError(f"FIRMS API error: {csv_text[:200]}")

    _CACHE[cache_key] = (now, csv_text)
    return csv_text


def parse_fires_last_seen(csv_text: str, spot_grid_deg: float = 0.01) -> list[dict[str, Any]]:
    """Parse FIRMS active fire CSV and aggregate by spot to find the LAST detection in each spot.

    spot_grid_deg: grid cell size for identifying a unique 'spot' (~0.01 deg ≈ 1 km).
    Returns list of spots with last seen observation details and 24h summary.
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    spots: dict[tuple[float, float], dict[str, Any]] = {}
    now_utc = dt.datetime.now(dt.timezone.utc)

    for row in reader:
        try:
            lat = float(row["latitude"])
            lon = float(row["longitude"])
        except (KeyError, ValueError):
            continue

        spot_lat = round(lat / spot_grid_deg) * spot_grid_deg
        spot_lon = round(lon / spot_grid_deg) * spot_grid_deg
        spot_key = (round(spot_lat, 4), round(spot_lon, 4))

        acq_date = row.get("acq_date", "").strip()
        acq_time = str(row.get("acq_time", "")).strip().zfill(4)
        try:
            acq_dt = dt.datetime.strptime(f"{acq_date} {acq_time}", "%Y-%m-%d %H%M").replace(tzinfo=dt.timezone.utc)
            iso_time = acq_dt.isoformat()
            hours_ago = max(0.0, round((now_utc - acq_dt).total_seconds() / 3600.0, 1))
        except Exception:
            iso_time = f"{acq_date}T{acq_time[:2]}:{acq_time[2:]}:00Z"
            hours_ago = None

        try:
            frp = float(row.get("frp", 0.0) or 0.0)
        except ValueError:
            frp = 0.0

        try:
            bright_ti4 = float(row.get("bright_ti4", row.get("brightness", 0.0)) or 0.0)
        except ValueError:
            bright_ti4 = 0.0

        try:
            bright_ti5 = float(row.get("bright_ti5", 0.0) or 0.0)
        except ValueError:
            bright_ti5 = 0.0

        satellite_code = row.get("satellite", "").strip()
        sat_names = {
            "N20": "NOAA-20 (VIIRS)",
            "N21": "NOAA-21 (VIIRS)",
            "SNPP": "Suomi NPP (VIIRS)",
            "T": "Terra (MODIS)",
            "A": "Aqua (MODIS)",
            "1": "Terra (MODIS)",
            "2": "Aqua (MODIS)",
        }
        sat_name = sat_names.get(satellite_code, satellite_code or "Satellite")

        daynight = "Day" if row.get("daynight", "D").upper() == "D" else "Night"
        conf = row.get("confidence", "nominal")

        record = {
            "lat": round(lat, 5),
            "lon": round(lon, 5),
            "spot_lat": spot_key[0],
            "spot_lon": spot_key[1],
            "last_seen_time": iso_time,
            "hours_ago": hours_ago,
            "frp_mw": round(frp, 2),
            "brightness_k": round(bright_ti4, 1),
            "brightness_c": round(bright_ti4 - 273.15, 1) if bright_ti4 > 0 else None,
            "brightness_bg_k": round(bright_ti5, 1) if bright_ti5 > 0 else None,
            "satellite": sat_name,
            "daynight": daynight,
            "confidence": conf,
            "scan": float(row.get("scan", 0) or 0),
            "track": float(row.get("track", 0) or 0),
        }

        if spot_key not in spots:
            record["detections_24h"] = 1
            record["max_frp_24h"] = round(frp, 2)
            record["all_satellites"] = [sat_name]
            spots[spot_key] = record
        else:
            prev = spots[spot_key]
            prev["detections_24h"] += 1
            prev["max_frp_24h"] = max(prev["max_frp_24h"], round(frp, 2))
            if sat_name not in prev["all_satellites"]:
                prev["all_satellites"].append(sat_name)

            # If this record was acquired later than the stored last seen, update it
            if iso_time >= prev["last_seen_time"]:
                record["detections_24h"] = prev["detections_24h"]
                record["max_frp_24h"] = prev["max_frp_24h"]
                record["all_satellites"] = prev["all_satellites"]
                spots[spot_key] = record

    # Sort spots by recency (most recently seen first)
    result = list(spots.values())
    result.sort(key=lambda s: (s.get("last_seen_time") or "", s.get("frp_mw", 0)), reverse=True)
    return result


def get_last24h_geojson(
    bbox: tuple[float, float, float, float] | None = None,
    source: str = DEFAULT_SOURCE,
    max_area: float = MAX_AREA_KM2,
    date_str: str | None = None,
) -> dict[str, Any]:
    """Get fires seen last in each spot within bbox (or sample for world) in GeoJSON format."""
    if bbox is not None:
        west, south, east, north = bbox
        area_km2 = geodesic_area_km2(west, south, east, north)
        if area_km2 > max_area + 2.0:
            raise ValueError(
                f"Selected area is {area_km2:.1f} km², exceeding the maximum limit of {max_area:.0f} km². "
                f"Please select a smaller inspection box on the map or globe."
            )
        area_str = f"{west:.4f},{south:.4f},{east:.4f},{north:.4f}"
    else:
        area_km2 = 510_072_000.0  # Earth total surface area
        area_str = "world"

    csv_data = fetch_firms_csv(source=source, area_str=area_str, day_range=1, date_str=date_str)
    spots = parse_fires_last_seen(csv_data)

    features = []
    for s in spots:
        feat = {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [s["lon"], s["lat"]],
            },
            "properties": {
                "kind": "live_fire",
                "lat": s["lat"],
                "lon": s["lon"],
                "spot_lat": s["spot_lat"],
                "spot_lon": s["spot_lon"],
                "last_seen_time": s["last_seen_time"],
                "hours_ago": s["hours_ago"],
                "frp_mw": s["frp_mw"],
                "max_frp_24h": s["max_frp_24h"],
                "brightness_k": s["brightness_k"],
                "brightness_c": s["brightness_c"],
                "satellite": s["satellite"],
                "daynight": s["daynight"],
                "confidence": s["confidence"],
                "detections_24h": s["detections_24h"],
                "all_satellites": s["all_satellites"],
            },
        }
        features.append(feat)

    max_frp = max((s["frp_mw"] for s in spots), default=0.0)
    latest_time = max((s["last_seen_time"] for s in spots), default=None)

    return {
        "type": "FeatureCollection",
        "features": features,
        "summary": {
            "total_spots": len(spots),
            "max_frp_mw": max_frp,
            "latest_detection_utc": latest_time,
            "source": source,
            "date": date_str or dt.date.today().isoformat(),
            "area_km2": round(area_km2, 1),
            "bbox": list(bbox) if bbox else None,
            "is_live_24h": date_str is None,
        },
    }
