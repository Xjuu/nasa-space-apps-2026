# FireSpot API (backend/)

FastAPI service on top of the `equifire` package: harmonized MODIS + VIIRS fire cells, anomaly
Z-scores, spread forecasts, satellite overpass countdown, and GeoJSON / CAP 1.2 export.

## Run

```bash
.venv/bin/pip install fastapi uvicorn sgp4 httpx pytest   # once
ss -ltn | grep :8080 || echo "8080 free"
.venv/bin/python -m uvicorn backend.app:app --host 0.0.0.0 --port 8080
# interactive docs: http://localhost:8080/docs
```

Startup returns right away. A background thread loads the spread model and the data, and
`GET /health` shows `"status": "loading" | "ready" | "error"`. Endpoints that need data answer
`503` (with a `Retry-After` header) until it is ready. `/overpass` works straight away.

The **first** start builds `backend/.cache/daily.parquet` from `cache/cells/*.parquet`. This takes
several minutes, because it harmonizes every MODIS overpass with the size models. It reads one year
at a time to keep RAM low. Later starts read the cache in a few seconds. The cache rebuilds itself
when the cell files change. Delete `backend/.cache/` to force a rebuild.

Tests use small synthetic data and the real spread model. They do not need the dataset:

```bash
.venv/bin/python -m pytest backend/test_app.py -q
EQUIFIRE_SLOW=1 .venv/bin/python -m pytest backend/test_app.py -q   # + real dataset load
```

## Endpoints

All dates are `YYYY-MM-DD` (UTC). Grid cells are 0.05 deg (about 5.5 km). Bad input returns
`400`/`422`, a date outside the data range or no fire nearby returns `404`, and data still loading
returns `503`.

| endpoint | what it returns |
|---|---|
| `GET /health` | load status, whether the model is loaded, data date range, last log lines |
| `GET /fires?date=&bbox=minlon,minlat,maxlon,maxlat[&limit=20000][&gaps=true]` | GeoJSON cell polygons burning that day (any sensor), sorted by FRP. Properties: `detections_viirs/modis`, `frp_viirs_mw`, `frp_modis_raw_mw`, `frp_modis_harmonized_mw` (MODIS converted to VIIRS-equivalent by `models/size_frp.joblib`), `frp_equiv_mw` (mean VIIRS-equivalent FRP per overpass), `fire_area_km2`. With `gaps=true` it also returns `kind: "gap"` cells: they burned yesterday but are not seen today (possible cloud/smoke dead zone). |
| `GET /forecast?lat=&lon=&date=&days=3[&search_deg=0.5]` | Snaps to the nearest VIIRS-burning cell within `search_deg`, then runs `equifire.direct`. Features: `burning` (day 0), `predicted` (first day `k` the cell is selected, `probability`, `p_max`), `risk_area` (convex hull of burning + predicted cells by day `k`, MultiPolygon) and, for historical dates, `observed` (real VIIRS newly-burning cells, same truth rule as training). `summary.per_day` has predicted/observed counts, km2 and IoU per day, and the ERA5 wind used. |
| `GET /forecast.cap?...` | The same forecast as a CAP 1.2 alert: one `<area>` per day with lat,lon polygons, severity from the predicted new area (Minor < 100 km2 <= Moderate < 300 <= Severe < 1000 <= Extreme). `status` is `Exercise` for historical replays. |
| `GET /anomaly?lat=&lon=&date=[&z_threshold=2]` | Burning VIIRS cell-days in the 1-deg cell during the ISO week, compared with the same week of every other complete VIIRS year (a missing year counts as 0). `z = (x - mean) / max(std, sqrt(mean))`. `label` is one of `no fire activity`, `routine seasonal burning`, `anomaly` (z >= threshold), `no data for this week`. `seasonal_frequency` = share of baseline years with fire in that week. |
| `GET /stats?bbox=minlon,minlat,maxlon,maxlat[&year=][&metric=viirs_cell_days][&z_threshold=2]` (or `lat=&lon=&size_deg=`), `POST /stats` with `{"geometry": GeoJSON Polygon/MultiPolygon/Feature, "year", "metric"}` | Area statistics over the 0.05 deg cells whose centre is inside the area (up to 40 x 40 deg; ~0.1-0.4 s for 10 x 10 deg). `calendar[metric][year][week]` (ISO weeks 1-53, `null` = no data for that sensor) for `viirs_cell_days`, `viirs_detections`, `modis_cell_days`, `modis_detections`, `any_cell_days`, `frp_equiv_mw` (harmonized FRP); `per_year` totals by sensor with coverage days; `selected.weeks` (value, leave-one-year-out mean/std, z, flag `high`/`low`) and `selected.key` (year total, % vs mean, z, label `routine` / `anomaly: above normal` / `unusually low`, anomalous weeks). Code: `stats.py`. |
| `GET /overpass?lat=&lon=[&hours=24][&at=ISO8601]` | Next passes of Terra and Aqua (MODIS) and Suomi NPP, NOAA-20 and NOAA-21 (VIIRS), from `aux/tle/*.tle` + SGP4. A pass is counted while the point is inside the swath (scan angle <= 55 / 56.3 deg, roughly elevation above 20-25 deg). Each pass has start/end/best time, `minutes_until`, max elevation, min scan angle, estimated pixel size (km), and day/night. `next` = the soonest pass. |

CORS allows all origins, for the frontend.

## Files

- `app.py`: routes, input checks, anomaly
- `data.py`: one-time build of the daily cell table (MODIS harmonization), cache, in-memory `State`, background `Store` loader
- `spread.py`: forecast around a fire with `equifire.direct` (`predict_case`, `select`, `rule_for`, `case_truth`)
- `cap.py`: CAP 1.2 XML
- `orbits.py`: TLE parsing, SGP4 propagation, swath/elevation pass finder
- `geo.py`: cell polygons, hull rings
- `test_app.py`: tests

## Limits / not done yet

- No live NRT FIRMS feed: `/fires`, `/forecast` and `/anomaly` work on the archived data only, so
  "today" returns 404 / `no data for this week`.
- The data covers only the downloaded countries (Australia, Brazil, Canada, India, United States).
- Cloud / dead-zone hints are only a heuristic (`gaps=true`). No cloud-cover data is used yet.
- The forecast uses weather only where ERA5 tiles are cached (`cache/weather`). Elsewhere the model
  runs without wind (`summary.wind.source` says so). No terrain slope yet.
- The forecast horizon is days (1-28), not the 3/6/12-hour cone from the concept.
- Overpass: spherical-Earth pixel size estimate. Polar motion is ignored. The TLEs should be
  refreshed every few days (`tle_age_days` is reported).
- The MODIS harmonization neighbour features do not look across 1 January (each year is built
  separately).


## Combined predictor and hourly physics

`GET /predict?lat=-3.925&lon=-53.825&date=2024-01-01&days=3` serves the experimental ML + physics blend with all component values and training/source notes. `objective=iou` selects exact cells; `objective=tol` uses one-cell-tolerance rules. Missing compatible artifacts return 503. This does not replace `/forecast`.

`GET /simulate?lat=-18.3&lon=124.4&hours=6&step_min=180&ignition=point&weather=grid&landscape=on&fwi=false` uses hourly forecast conditions, optional gridded weather, and mapped fuel/terrain/barriers/dryness. `start` selects UTC initialization; `step_min` changes playback, while weather always changes hourly. Output includes `weather_timeline`, `weather`, `landscape` and explicit fallback metadata. The recent-weather fallback is public regional NOAA GFS GRIB, with cached 3-hour source slices interpolated hourly. `rasterio` is required for GFS and landscape raster decoding.

Measured results, limitations and checks: [Claude continuation report](../reports/CLAUDE_CONTINUATION.md). The blend does not establish higher accuracy than equally retuned ML; the richer landscape simulation requires separate validation.


## Standard application forecast

`GET /predict/simulate?lat=-3.925&lon=-53.825&start=2024-01-01T12:00&hours=3&step_min=180&ignition=archive` returns one `prediction_mode: ml_physics` result containing fitted blend guidance and physical playback frames. Hours are limited to 1–72; ignition must be live or archive. Both components are required; missing ML or satellite ignition does not fall back to standalone physics. Native daily guidance horizons are attached to each playback frame, without inventing hourly ML probabilities. `/predict`, `/forecast` and `/simulate` remain available for API compatibility and experiments; the application uses the combined route for every forecast.

## Combined forecast speed

`/predict/simulate` finishes both daily ML + physics guidance and physical playback
frames before responding. The pipelines now overlap after ignition selection.
The standard combined route uses cached OpenStreetMap barriers; uncached barriers
are optional and prepared separately. Returned `landscape.layers.osm` records
coverage and `optional`/`cache_only`; the website displays missing coverage.
Weather, fuels, terrain and Sentinel imagery remain in the forecast calculation.
The prewarmer prepares 72 hours of conditions. Measured complete 72-hour runs were
21.6–24.6 seconds in the latest tests; external services/load prevent a universal
completion-time guarantee. Evidence and limits: `reports/FORECAST_SPEED.md`.
