"""Daily weather (ERA5 via Open-Meteo archive, no key) on a coarse 2-degree tile grid.

Only the busiest fire tiles are fetched, to stay inside Open-Meteo free limits.
Rows outside fetched tiles get NaN; the gradient-boosting model handles missing values.
"""
import json
import time
import urllib.error
import urllib.request

import numpy as np
import pandas as pd

from .common import CACHE, EPOCH

TILE = 2.0
VARS = ["wind_speed_10m_max", "wind_direction_10m_dominant", "temperature_2m_max",
        "relative_humidity_2m_min", "precipitation_sum"]
URL = ("https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lon}"
       "&start_date={start}&end_date={end}&daily={vars}&timezone=GMT")
WDIR = CACHE / "weather"


def _tile(lat, lon):
    return np.floor(np.asarray(lat) / TILE).astype(int), np.floor(np.asarray(lon) / TILE).astype(int)


def _fetch(ty, tx, start, end):
    f = WDIR / f"{ty}_{tx}_{start}_{end}.json"
    if f.exists():
        return json.loads(f.read_text())
    u = URL.format(lat=(ty + 0.5) * TILE, lon=(tx + 0.5) * TILE, start=start, end=end, vars=",".join(VARS))
    with urllib.request.urlopen(u, timeout=120) as r:
        data = json.loads(r.read())
    f.write_text(json.dumps(data))
    return data


def attach_weather(X, meta, n_tiles=40):
    """Add weather columns + wind/spread-direction alignment to spread features X."""
    WDIR.mkdir(parents=True, exist_ok=True)
    ty, tx = _tile(meta["lat"].values, meta["lon"].values)
    counts = pd.Series(1, index=pd.MultiIndex.from_arrays([ty, tx])).groupby(level=[0, 1]).size()
    top = counts.sort_values(ascending=False).index[:n_tiles]
    dmin, dmax = int(meta["day"].min()), int(meta["day"].max())
    start = str(EPOCH + np.timedelta64(dmin, "D"))
    end = str(min(EPOCH + np.timedelta64(dmax, "D"), np.datetime64("today") - np.timedelta64(7, "D")))

    frames = []
    for i, (a, b) in enumerate(top, 1):
        try:
            d = _fetch(a, b, start, end)
        except urllib.error.HTTPError as e:
            print(f"[weather] HTTP {e.code} at tile {i}; continuing with {len(frames)} tiles")
            break
        except Exception as e:
            print(f"[weather] tile {i} failed ({e}); skipping")
            continue
        w = _frame(d, a, b)
        frames.append(w)
        covered = counts.loc[list(top[:i])].sum() / counts.sum()
        print(f"[weather] tile {i}/{len(top)} lat {a * TILE:+.0f} lon {b * TILE:+.0f}: {len(w):,} days | "
              f"covers {covered:.0%} of candidate cells", flush=True)
        time.sleep(0.5)

    return join_weather(X, meta, frames)


def _frame(d, a, b):
    daily = d["daily"]
    w = pd.DataFrame({v: daily[v] for v in VARS})
    w["day"] = (np.array(daily["time"], dtype="datetime64[D]") - EPOCH).astype(int)
    w["ty"], w["tx"] = a, b
    return w


def cached_frames():
    """All weather already downloaded during training (no network)."""
    frames = []
    for f in sorted(WDIR.glob("*.json")):
        a, b = map(int, f.stem.split("_")[:2])
        frames.append(_frame(json.loads(f.read_text()), a, b))
    return frames


def join_weather(X, meta, frames, verbose=True):
    ty, tx = _tile(meta["lat"].values, meta["lon"].values)
    key = pd.DataFrame({"day": meta["day"].values, "ty": ty, "tx": tx})
    if frames:
        W = pd.concat(frames, ignore_index=True).drop_duplicates(["day", "ty", "tx"])
        key = key.merge(W, on=["day", "ty", "tx"], how="left")
    else:
        for v in VARS:
            key[v] = np.nan

    X = X.copy()
    X["wind_speed"] = key["wind_speed_10m_max"].values
    X["temp_max"] = key["temperature_2m_max"].values
    X["rh_min"] = key["relative_humidity_2m_min"].values
    X["precip"] = key["precipitation_sum"].values
    # Wind blows TO (from + 180). Fire push vector points from burning neighbours to this cell.
    wind_to = np.radians((key["wind_direction_10m_dominant"].values + 180) % 360)
    push_bearing = np.arctan2(X["push_x"].values, X["push_y"].values)  # x = east, y = north
    align = np.cos(wind_to - push_bearing)
    X["wind_align"] = align
    X["wind_push"] = align * X["wind_speed"].values
    if verbose:
        print(f"[weather] weather attached to {np.isfinite(X['wind_speed']).mean():.0%} of candidate cells")
    return X


class AlertWeather:
    """Cached observations through t0-1 only; no future observed weather in backtests."""
    def __init__(self, weather_csv=None):
        frames = cached_frames()
        self.rows = {}
        if frames:
            w = pd.concat(frames, ignore_index=True).drop_duplicates(["day", "ty", "tx"], keep="last")
            self.rows = dict(zip(zip((w.day + int((EPOCH - np.datetime64("1970-01-01")).astype(int))).astype(int), w.ty.astype(int), w.tx.astype(int)),
                                 w[VARS].to_numpy(dtype=float)))

        if weather_csv is not None:
            self.load_csv(weather_csv)

    def load_csv(self, path):
        """Offline daily weather: date, latitude, longitude plus VARS in archive units.

        Wind speed km/h, direction degrees FROM north, temperature C, RH percent,
        precipitation mm. Coordinates select 2-degree tiles. CSV overrides cache.
        """
        w = pd.read_csv(path)
        required = ["date", "latitude", "longitude"] + VARS
        missing = set(required) - set(w.columns)
        if missing:
            raise ValueError(f"weather CSV missing columns: {sorted(missing)}")
        day = (pd.to_datetime(w["date"]).to_numpy().astype("datetime64[D]")
               - np.datetime64("1970-01-01")).astype(int)
        ty, tx = _tile(w["latitude"].to_numpy(), w["longitude"].to_numpy())
        self.rows.update(zip(zip(day, ty, tx), w[VARS].to_numpy(dtype=float)))

    def get(self, day, lat, lon):
        return self.rows.get((int(day), int(np.floor(lat / TILE)), int(np.floor(lon / TILE))),
                             np.full(5, np.nan))

    def features(self, day, lat, lon, north, east):
        """One row per candidate; wind alignment is from nearest burning cell."""
        lat, lon = np.asarray(lat), np.asarray(lon)
        tiles = np.column_stack(_tile(lat, lon))
        unique, inverse = np.unique(tiles, axis=0, return_inverse=True)
        daily, rain7 = [], []
        for ty, tx in unique:
            history = np.array([self.get(day - lag, (ty + .5) * TILE,
                                         (tx + .5) * TILE) for lag in range(1, 8)])
            daily.append(history[0])
            rain7.append(history[:, 4].sum())  # missing days remain NaN
        values = np.asarray(daily)[inverse]
        speed, direction, temp, rh, rain = values.T
        angle = np.deg2rad(direction + 180)
        length = np.hypot(north, east)
        align = (np.cos(angle) * north + np.sin(angle) * east) / np.maximum(length, 1e-6)
        # Saturation vapour pressure (kPa) times relative moisture deficit.
        vpd = .6108 * np.exp(17.27 * temp / (temp + 237.3)) * (1 - rh / 100)
        return np.column_stack((speed, temp, rh, rain, np.asarray(rain7)[inverse],
                                np.sin(angle), np.cos(angle), align, align * speed,
                                vpd, np.isfinite(values).all(axis=1))).astype(np.float32)


ALERT_WEATHER_FEATURES = ["wx_wind_speed", "wx_temp_max", "wx_rh_min", "wx_rain",
                          "wx_rain7", "wx_wind_east", "wx_wind_north", "wx_wind_align",
                          "wx_wind_push", "wx_vpd_proxy", "wx_available"]


def prepare_alert_weather(countries, train_years, end_year, n_tiles):
    """Choose weather locations using training detections only, then cache archive data."""
    from collections import Counter
    from .alerts1km import load_country_year, TILE as FIRE_TILE
    counts = Counter()
    for country in countries:
        for year in train_years:
            rec = load_country_year(country, year)
            if rec is None:
                continue
            tiles, n = np.unique(rec["tile"], return_counts=True)
            for tile, count in zip(tiles, n):
                lat = ((tile // 100000) * FIRE_TILE + FIRE_TILE / 2) / 100 - 90
                lon = ((tile % 100000) * FIRE_TILE + FIRE_TILE / 2) / 100 - 180
                counts[(int(np.floor(lat / TILE)), int(np.floor(lon / TILE)))] += int(count)
    WDIR.mkdir(parents=True, exist_ok=True)
    start, end = f"{min(train_years)}-01-01", f"{end_year}-12-31"
    for i, ((ty, tx), _) in enumerate(counts.most_common(n_tiles), 1):
        try:
            _fetch(ty, tx, start, end)
            print(f"[alert weather] {i}/{n_tiles}: {ty}, {tx}", flush=True)
        except Exception as exc:
            print(f"[alert weather] stopping downloads: {exc}; using available cache", flush=True)
            break
        time.sleep(.5)
