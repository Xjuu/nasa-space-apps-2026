# EquiFire — status, how it works, and what is left

NASA Space Apps 2026, challenge "Harmonization of MODIS and VIIRS Hot Spots".
Project goals are in `HAKATON.pdf` (Polish). This file is kept up to date as work progresses.

---

## 1. How the whole system works

### 1.1 Data

| Step | What | Where |
|---|---|---|
| Download | FIRMS per-country yearly CSVs (no API key needed): MODIS 2000–2025, VIIRS S-NPP 2012–2025, VIIRS NOAA-20 2018–2025 | `download_country.py` → `data_country/<sensor>/<year>/` |
| Aggregate | Every detection is put on a 0.05° grid (~5.5 km). One row = one satellite overpass over one cell: count, FRP, pixel footprint, brightness, confidence, time | `equifire/collect.py` → `cache/cells/*.parquet` |
| Fast cache | All cells combined + a daily VIIRS "burning cell-day" table, rebuilt automatically when new files arrive | `equifire/fastcache.py` → `cache/cells_all.parquet`, `cache/viirs_daily.parquet` |
| Weather | Daily ERA5 (Open-Meteo archive, no key) on 2° tiles for the busiest fire areas: wind, temperature, humidity, rain | `equifire/weather.py` → `cache/weather/` |
| Orbits | Current TLEs for Terra, Aqua, S-NPP, NOAA-20, NOAA-21 | `aux/tle/` |

Coverage today: Brazil, Australia, India, United States, Canada. All 176 FIRMS countries are downloading (~43 GB).

### 1.2 Models

**A. Fire-size harmonization (MODIS → VIIRS-equivalent).**
When MODIS and VIIRS see the same cell within ±30 minutes, the VIIRS reading is used as the truth for the MODIS one. A gradient-boosting model learns to turn a MODIS observation (count, FRP, scan/track, brightness, neighbours, recent activity) into what VIIRS would have measured: burning footprint (km²) and FRP (MW).
Code: `equifire/datasets.py: build_size`, `equifire/train.py: train_size` → `models/size_area.joblib`, `models/size_frp.joblib`.
Result on unseen years (2023+): fire-size error about 69% lower than raw MODIS and 24% lower than a straight-line correction.

**B. Fire spread — direct multi-day model (main model).**
For a fire on start day t0, take a 41×41-cell window (~225 km). For every cell and every horizon k = 1…28 days it predicts "will this cell newly burn within k days?". It uses only what is known on t0:
- distance to the fire front, burning cells and FRP in rings around the cell, recent days' activity, the cell's own burn history,
- direction from the fire centre, recent growth direction, wind alignment,
- fire size and FRP trend, location, season, weather.

Then for each horizon it chooses which cells to call "will burn". The choice is either a probability threshold or the expected-overlap rule, whichever works best on held-out training-period fires.
Code: `equifire/direct.py` → `models/spread_direct.json`. Train with `python main.py direct`.
Feature set v1 has 41 inputs. v2 (84 inputs) adds MODIS detections, the day/night split, fire-front shape, growth rate and 365-day burn history.

**C. Old spread model (day-by-day simulation).** It predicts one day ahead and feeds its own prediction back in. Errors compound, so it is kept only for comparison (`forecast.py --compare`).

**D. Online learning.** `live_server.py` and `walk_forward.py` predict a fire, wait until its outcome is known, then add a few trees trained on that outcome. A replay sample of older fires is mixed in so one fire cannot distort the model.

### 1.3 How accuracy is measured (honestly)

- **Truth:** a cell counts as "newly burned by day k" if VIIRS detects it on some day j ≤ k, it is at most j cells from the start-day fire, and it was not burning on t0.
- **Strict accuracy:** overlap (IoU) of predicted and real newly burned cells. If both are empty, the score is 100%.
- **"Within 5 km":** same, but a prediction 1 cell off still counts.
- **No leakage:** the main split trains on years before 2023 and tests on 2023–2025. In walk-forward, the model for year Y is trained only on fires before Y. Online learning only uses outcomes that are already in the past.

Why short horizons score lower: day 1 targets are tiny (often 1–5 cells), and only ~29% of burning cells are seen again the next day (clouds, smoke, overpass timing). Over longer periods those misses average out.

### 1.4 Programs

| Command | What it does |
|---|---|
| `python main.py` | collect + train size models, old spread model, direct model |
| `python main.py direct --direct-cases 6000 --use-gpu` | train only the direct spread model |
| `python main.py train --parallel` | GPU + CPU lanes at once, RAM capped at 18 GB |
| `python forecast.py --runs 100 --days 7 --parallel` | backtest on unseen 2023–2025 fires |
| `python forecast.py --walk-forward --runs 100 --days 28` | per-year retraining + predict-then-learn (base models reused when settings match; `--retrain` forces new ones) |
| `python live_server.py` | live predict → reveal → learn dashboard (port 8088) |
| `python pool_server.py` | volunteer training pool (disclaimer + download, hyperparameter search) |
| `uvicorn backend.app:app --host 0.0.0.0 --port 8000` | REST API: `/fires`, `/forecast`, `/forecast.cap`, `/anomaly`, `/overpass`, `/health` |
| `python -m http.server 5173 -d frontend` | simple web app (map + forecast + anomaly + overpasses) |

---

## 2. Current results (spread)

| Model | Test | Strict day 1 | Strict day 3 | Strict mean | Within 5 km day 3 |
|---|---|---|---|---|---|
| Old day-by-day | 300 fires, 28 days | 18.3% | 11.4% | 11.2% | — |
| Neighbour rule (baseline) | same | 13.3% | 8.8% | 7.2% | — |
| Direct v1 (600 fires) | 15 fires, 7 days | 26.7% | 14.4% | 19.0% | — |
| Direct v2, walk-forward smoke test (300 fires) | 15 fires 2023, 7 days | 51.8% | 33.3% | 31.6% | 63.4% |
| **Direct v2, walk-forward (3000 fires/year)** | **100 fires 2023, 28 days** | **23.6%** | **19.6%** | **27.2%** | **56.1%** (mean 69.2%) |
| **Direct v2, walk-forward** | **100 fires 2024, 28 days** | **23.0%** | **18.2%** | **27.1%** | **57.5%** (mean 71.0%) |

Online learning from its own predictions: no gain (−0.1 to −1 point). Yearly retraining with all earlier fires is what matters.
Day-3 accuracy within 5 km is above 50%. Strict cell-exact accuracy is about 19%.

---

## 3. In progress

- [ ] **All-country download** (176 countries, 43.2 GB, about 4 MB/s from FIRMS, about 2 h). Then collect into the cache and measure RAM. A background agent is handling this.
- [ ] **MODIS-era reconstruction (2000–2011).** Learn on 2012–2025, where both sensors exist, what VIIRS would have seen given only MODIS: which cells burn, their FRP and their footprint. Apply it to 2000–2011 to get a VIIRS-equivalent archive (`cache/viirs_equiv_daily.parquet`, `harmonize.py`). Measure on held-out years against real VIIRS, then check how much spread accuracy is lost when old fires are predicted from reconstructed data. A background agent is handling this.
- [x] **Walk-forward run**: 2023 and 2024 done (above); 2025 running.

## 4. To do / needs improvement

**Accuracy**
- [ ] Validate feature set v2 at scale (6000+ fires) against v1 (`models/spread_direct_v1_6000.json`, val AUC 0.856), and keep the winner as `spread_direct.json`.
- [ ] Retrain on all countries once the download is done. More fire types (savanna, boreal, agricultural) should help generalization.
- [ ] Use the reconstructed 2000–2011 data as extra training input for spread (more years of fires), if the reconstruction is good enough.
- [ ] Terrain slope and land cover (SRTM/Copernicus DEM, ESA WorldCover). PDF goal 7, not done yet.
- [ ] Wider weather coverage: only about 5% of candidate cells currently have weather. Fetch more tiles, or use gridded ERA5 directly.
- [ ] Tune the cell selection for fire size as well as overlap (direct v1 under-predicts size).
- [ ] Online learning currently gives about ±1 point. Try larger or more frequent updates, and per-region recalibration.
- [ ] Bowtie / scan-angle correction with historical TLEs (Space-Track account needed) — PDF goal 2.

**Speed / resources**
- [ ] Walk-forward predict-then-learn is sequential by design. Batch the predictions of fires whose outcomes don't affect each other.
- [ ] RAM: `load_cells()` on all countries may need 15+ GB. Switch training to per-year or per-country loading.
- [ ] Download speed is limited by the FIRMS server (about 4 MB/s).

**Product (PDF goals)**
- [ ] Live NRT feed: needs a working FIRMS MAP_KEY (the current one returned 401).
- [ ] 3/6/12-hour risk cone (current horizons are whole days).
- [ ] Cloud/dead-zone overlay from real cloud data (currently a heuristic: cells that burned yesterday but are unseen today).
- [ ] Refresh TLEs automatically (they go stale in a few days).
- [ ] Frontend: test in a real browser and on a phone, and add the reconstructed archive to the map.

## 5. Known limitations

- Truth is VIIRS detections, which miss fires under clouds and smoke. "Not detected" is not the same as "not burning".
- The grid is 5.5 km and the time step is 1 day. Spread inside a cell or within a day is not modelled.
- Only cached weather is used. Future weather in backtests is the real observed weather, as if the weather forecast were perfect.
- MODIS-era (pre-2012) results are reconstructions and should always be labelled as such.

## 6. 1 km high-confidence alerts (done, `alerts1km.py`)

Trained on 2012–2020, alert threshold chosen on 2021–2022, tested on 2023–2025 (4,500 fire-days, 5 countries).
"Alert" = a ~1 km cell predicted to get new fire within ±1 km.

| Horizon | Threshold (chosen on validation) | Test precision | Test recall | Alerts per fire-day | Baseline best precision |
|---|---|---|---|---|---|
| next day | 0.56 | 63.2% | 9.2% | 1.8 | 33.8% |
| 3 days | 0.58 | 65.7% | 15.1% | 7.1 | 42.7% |

At threshold 0.65 test precision reaches 69–72%, with about 3–8% recall. Precision is about double the baseline, but alerts cover only a small part of the new fire.
Usage: `from equifire.alerts1km import predict_alerts`. Files: `equifire/alerts1km.py`, `models/alerts1km*.json`.


## 7. Three-kilometre alert experiment

`alerts1km.py` now defaults to `--radius-km 3`: train labels, validation precision,
and test precision/recall match cell centres using great-circle kilometres. This
is a location tolerance on the ~1 km grid, not the coarse spread model's IoU and
not overall classification accuracy. Legacy model behaviour stays available.
Supported horizons remain 1 and 3 days, for spread near an existing detected fire.

Threshold selection searches all distinct validation scores (including ties),
maximizing supported validation hits at the requested precision, with at least
50 validation alerts. If no threshold meets the target, the report says so and
uses the best supported validation precision. Test outcomes never set thresholds.
The saved report includes target-met flags, recall, alert counts and country results.
A 70% precision target does not guarantee 70% recall or performance in each country.

Train a separate candidate, keeping deployed models intact:

```sh
.venv/bin/python alerts1km.py --radius-km 3 --target-precision .70 --output-dir models/alerts3km
```

Use the candidate explicitly with
`predict_alerts(lat, lon, date, h=3, model_dir="models/alerts3km")`.
Inspect `models/alerts3km/alerts1km_meta.json` before deployment.
Geometry, recall consistency and threshold selection checks:
`.venv/bin/python -m unittest discover -s tests -v`.

Full five-country run completed (6,000 held-out fire-days, 2023–2025):
- 1 day(s): precision 67.6%, recall 40.3% within 3 km; 70% target met: False.
- 3 day(s): precision 68.0%, recall 49.8% within 3 km; 70% target met: False.

## 7. Goal 4: routine agricultural burning vs fire anomaly (done, `classify.py`, `equifire/seasonal.py`)

A deterministic engine for each 0.25° region (~28 km) and date. The calendar baseline is built from all *other* years (leave-one-year-out), using VIIRS 2012–2025. It looks at seven pieces of evidence:
1. **Season frequency:** in what share of other years is this week ±1 active?
2. **z-score:** this week's activity compared with the usual level. The spread is floored at 30% of the mean, because year-to-year variability is large.
3. **Field recurrence:** do today's burning cells also burn within ±14 days of this date in at least 2 other years? This picks out the same fields burned every year.
4. **One-day spike:** today's burning cells compared with a usual day in this window.
5. **FRP per detection:** compared with the region's typical value.
6. **Largest connected burning cluster.**
7. **Persistence:** the longest run of consecutive burning days.

Size and persistence are discounted where the place and time are a recurring burning calendar. Dense stubble burning is large but routine.
Each case gets a label ("routine seasonal burning" / "elevated seasonal activity" / "anomaly"), a routine score, an anomaly score, and plain-language reasons.

| Check | Correct |
|---|---|
| 22 reference events (rules designed while looking at these, so optimistic) | 21/22 |
| **12 independent events (added after the rules were fixed)** | **10/12** (anomalies 5/6, routine 5/6) |

Misses: Lytton 2021 (the snap point landed on a small recurring fire), and Haryana stubble burning 2021 ("elevated", because that season was 7σ above usual).
API: `GET /classify?lat=&lon=&date=`. The web app shows the label, scores and reasons, and falls back to `/anomaly`.
Run: `python classify.py --check` / `--holdout` / `--lat 30.6 --lon 75.6 --date 2023-11-05`.
Next steps:
- [ ] Add a cropland mask (ESA WorldCover) to tell agricultural fires from managed savanna fires.
- [ ] Extend the calendar to 2000–2011 using the MODIS reconstruction.
- [ ] Evaluate on a larger labelled set.

## 8. Notes / incidents
- The 5 km v2 production model training (5000 fires) was killed at the 12 GB RAM cap while other jobs filled memory. The backend keeps v1 (`spread_direct.json`, val AUC 0.856), because the backend's fire index is VIIRS-only and v2 needs MODIS inputs (`FireIndex.from_cells`). The walk-forward v2 models (`models/wf_<year>.json`) are the best-tested ones.



## 8. Weather-aware fine-grid model (offline supported)

Add `--weather` to train with cached weather. No API request occurs unless
`--weather-tiles N` is explicitly supplied. The optional archive downloader uses
Open-Meteo's free non-commercial endpoint, stops on rate limits, and caches replies.
It is not unlimited. No payment keys or subscription endpoints are used.

Inputs: previous completed UTC day's maximum wind speed (km/h), direction,
maximum temperature (C), minimum RH (%), rainfall (mm), preceding seven days'
rainfall, wind alignment/push from the nearest burning cell, a vapour-pressure
 deficit proxy, and a weather-availability flag. Weather can raise or lower learned
risk. No future observed weather is used. The VPD proxy combines daily extremes
and is not a simultaneous measured VPD. Weather tiles are 2 degrees wide;
weather coverage is recorded in the model report. Missing values remain missing.

```sh
.venv/bin/python alerts1km.py --weather --output-dir models/alerts3km_weather
```

For API-independent operation, supply `--weather-csv /absolute/path/weather.csv`.
Columns: `date,latitude,longitude,wind_speed_10m_max,wind_direction_10m_dominant,temperature_2m_max,relative_humidity_2m_min,precipitation_sum`.
Wind direction is degrees FROM north. Each coordinate identifies a 2-degree tile.
CSV rows override cached data; the CSV path is saved for prediction. Use permanently
stored weather files from any source with compatible units and rights.
This needs disk/compute, but no paid weather service or online request at runtime.
For recent predictions, refresh local weather first; the historical cache does not
supply today's weather automatically. Future forecast-weather inputs are not trained yet.

Inference: `predict_alerts(lat, lon, date, h=3, model_dir="models/alerts3km_weather")`.
An explicit `weather=AlertWeather(weather_csv=...)` can override the saved source.

## 9. MODIS → VIIRS-equivalent reconstruction (v1 done, `harmonize.py`, `equifire/harmonize.py`)

The model is trained on 2012–2019, validated on 2020, and tested on 2021–2025 (never seen, 8.5M real VIIRS cell-days). It uses only MODIS-derived inputs (39 features). It predicts which 0.05° cells VIIRS would see burning, plus their detections, FRP and area.

| Held-out 2021–2025 | Precision | Recall | IoU | Predicted/true cells | Total FRP ratio | Total detections ratio |
|---|---|---|---|---|---|---|
| Model | 0.467 | 0.345 | **0.248** | 0.74 | 1.05 | 1.00 |
| Raw MODIS | 0.724 | 0.189 | 0.177 | 0.26 | 0.60 | 0.11 |

Scored against S-NPP-only truth, which matches what exists before 2012, IoU is 0.256 vs 0.208 for raw MODIS.
Per-cell FRP is not better than raw MODIS. The gains are in which cells burn and in the totals.
Output: 3.34M VIIRS-equivalent cell-days for 2008–2011 (`cache/viirs_equiv_daily.parquet`). Use `load_harmonized_daily(2008)`: reconstructed rows before 2012-01-20, real VIIRS after, with a `source` column.

Effect on spread forecasts for old fires (200 fires, scored against real VIIRS, mean IoU days 1–7):
- real VIIRS input: 0.209
- reconstructed input: 0.114
- raw MODIS input: 0.103

So old-fire forecasts are about 45% weaker.
Limit: only about 61% of VIIRS cell-days lie near any MODIS fire, so small fires MODIS never saw cannot be recovered.
- [ ] `apply --years 2000 … 2007` once the MODIS 2000–2007 download and collect are done.
- [ ] Re-run `apply` for 2008–2011 (the calibration scale was applied afterwards there).
- [ ] Retrain the spread model on reconstructed input, to close part of the 0.209 → 0.114 gap.
- [ ] Extend the goal-4 calendar to 2000–2011 with these rows.

Weather experiment completed on the same 6,000 held-out fire-days:
- 1 day(s): 67.5% precision, 40.3% recall within 3 km.
- 3 day(s): 67.9% precision, 49.5% recall within 3 km.
Weather coverage: {'train': 0.06668999791145325, 'validation': 0.148445725440979, 'test': 0.11719904094934464}. Full comparison: `models/alerts3km_weather/comparison.json`. Existing models were not replaced.

## 10. Disk-backed fire index (done, `equifire/fireindex_mm.py`)
The fire index used by spread training and forecasting (VIIRS day/night + MODIS cell-days) is saved once as `.npy` column files in `cache/fireindex/`. Programs memory-map it, so the OS loads only the pages a query touches and all programs share one copy. Results are identical to the in-RAM index (tested on 10 fires: cubes and extras equal).
- `direct.train`, `walk_forward.py` and `forecast.py` use `get_fire_index()`. They no longer need `load_cells()`, which took about 8–15 GB.
- The index rebuilds automatically when new cell files arrive. The first build reads only 7 columns of `cells_all.parquet`.
- [ ] Backend: use `get_fire_index()` plus DuckDB on the parquet files for `/fires`, instead of in-RAM tables.
- [ ] Seasonal calendar (goal 4): build from the memory-mapped arrays instead of a pickled DataFrame.

## 9. Accuracy experiment v2

Changes under test (all computed offline, no extra API):
- Fire-front and previous-day distances in kilometres, correcting longitude scale.
- Distance changes, seven-day front activity, local intensity trends and persistence.
- Nearest fire intensity/confidence/night fraction, longitude, and 365-day recurrence.
- Twice as many sampled training fire-days (600 per country-year).
- Inverse inclusion weights correct candidate subsampling within each sampled fire-day.
- Three tree settings selected using 2021 only; 2022 exclusively sets alert thresholds.
- Calibration aims at 73% precision for the 70% target, allowing a 3-point buffer.
  This trades recall for precision; it is not by itself evidence of better ranking.
- Historical test years are unchanged. These are retrospective comparisons on the
  existing benchmark, not independent prospective validation.

```sh
.venv/bin/python alerts1km.py --enhanced --weather --sampling-weights \
  --n-train 600 --trials 3 --calibration-year 2022 --precision-buffer .03 \
  --device cuda --output-dir models/alerts3km_v2
```

Model metadata stores feature version so existing and enhanced models use their
own input schema at prediction time. Existing candidate models remain available.

Further accuracy work, in priority order after evaluating v2:
1. Expand locally stored weather coverage, add forecast weather with issue times
   so backtests only use forecasts available when predictions were issued.
2. Add local terrain slope/aspect and vegetation/fuel cover, with observation dates.
3. Validate on separately held-out geographic events and future fire seasons;
   adjacent tiles/days may belong to the same event.
4. Evaluate spatially distinct alerts to reduce redundant warnings, reporting
   precision and coverage at fixed alert budgets.
5. Obtain independent burned-area/cloud masks to distinguish non-detection from
   extinguished fire. VIIRS detections alone are incomplete ground truth.

Product guidance retained from the challenge brief: serve responders, scientists,
land managers and firefighters; enable map area drawing or lat/lon input; use an
accessible colour ramp; consider a burning calendar, historical anomalies against
multi-year averages, and detection counts. Label satellite detections separately
from unique fire events, and show prediction precision together with missed-fire
coverage and the weather/data coverage of the selected area.


Fair comparison (same 2022 precision goal for every model):

```sh
.venv/bin/python compare_alerts.py --models models/alerts3km models/alerts3km_weather models/alerts3km_v2
```

This recalibrates thresholds on 2022, evaluates the same test cases and records
average precision (ranking quality) as well as spatial alert precision and coverage.
It also reports the models at their original saved thresholds. Comparison does not
modify model files. GPU training is optional; saved models predict on CPU.

## 11. History dashboard (done)
- **Backend:** `GET /stats?bbox=…|lat&lon&size_deg` and `POST /stats` (GeoJSON polygon), in `backend/stats.py`.
  - Returns a weekly calendar per year for VIIRS and MODIS (cell-days, detections, harmonized FRP) and yearly totals per sensor.
  - For the chosen year it gives weekly z-scores against the other years (mean ±1 std) and key figures: total, % vs average, z, routine/anomaly label, anomalous weeks and peak week.
  - A 10×10° area takes 0.1–0.4 s.
- **Frontend "📊 History" tab:**
  - Choose an area by drawing a rectangle or polygon (also works by tapping on a phone), with lat/lon + size, with a bbox, or with the current map view.
  - Big-number tiles, plus a burning calendar heatmap (weeks × years, viridis colours, or a blue-orange anomaly scale, with anomalous weeks marked).
  - A weekly chart of this year vs the mean ±1 std, and MODIS vs VIIRS yearly bars.
  - JSON/CSV download. Colourblind-safe colours, with shapes as well as colour.
- Tested in headless Chromium at desktop and 390 px width, and backend tests pass (31). Not yet tested with the full backend running, because RAM was low.
- [ ] Add the 2000–2011 reconstructed (MODIS-era) rows to the History calendar.

## 12. In progress (agents)
- [ ] **Goal 2, bowtie / scan-geometry correction** (`equifire/geometry.py`, `bowtie.py`, `/geometry`, `/clusters`).
  - SGP4 + TLE gives satellite position, view zenith, scan angle and slant range for each near-real-time detection.
  - For archive detections, the scan angle is recovered from the FIRMS scan/track pixel sizes, because current TLEs are wrong for old dates.
  - Clustering radius adapts to the pixel footprint: narrow at nadir, wide at swath edges.
  - Evaluation: clusters per fire vs scan angle, fixed vs adaptive radius, using MODIS–VIIRS coincident overpasses.
- [ ] **Goal 3, real-time anomaly early warning** (`backend/live.py`, `/live/alerts`, `/live/summary`, `/live/alerts.cap`, a "Live alerts" panel).
  - Inputs: FIRMS NRT global 24h/7d CSVs (public, no key) for NOAA-20, NOAA-21 and MODIS.
  - Baseline: weekly per 0.25° and 1° grid cell from the harmonized 2008–2025 archive (VIIRS + reconstructed MODIS era).
  - Live z-score; alert levels watch (z≥2), warning (z≥3), critical (z≥5), plus an "out of season" flag.
  - CAP export.

V2 completed: 27,000 training cases; results on 6,000 historical test fire-days:

| Days ahead | Alert precision | New fire cells covered | Coverage gain vs recalibrated v1 |
|---|---|---|---|
| 1 | 72.3% | 34.8% | +1.6 pp |
| 3 | 71.1% | 49.4% | +3.7 pp |

Model card: `models/alerts3km_v2/README.md`; full comparison: `reports/alerts_comparison.json`.
13 tests passed. Enhanced inference uses fixed training-aligned tiles; real-model
overlapping-window probability checks passed at both horizons.

## 13. Status 16:15
- **Goal 2, bowtie (done).** Clusters per single VIIRS fire at the MODIS swath edge (48–56°): fixed 1 km radius 1.339 (26% of fires split into duplicates); adaptive 1.007 (0.7%). Edge/nadir ratio: adaptive 0.95, fixed 1.27. Cost: at the edge, nearby fires that VIIRS separates get merged (93% vs 61%). `/geometry` uses SGP4 only within 3 days of the TLE epoch, otherwise the scan angle is recovered from pixel size. `/clusters` returns adaptive clusters as GeoJSON. `bowtie.py eval`.
- **Goal 3, live alerts (done).** Uses the FIRMS NRT 7-day feeds (no key) and an 18-year baseline (2012–2025 VIIRS S-NPP plus 2008–2011 reconstructed ×1.339). Areas with no archive fire within ~5 km are marked "no baseline". Today: 102 critical, 155 warning and 172 watch alerts at 1°; the top one is Western Australia at z=20, 26× normal. Endpoints: `/live/alerts`, `/live/summary`, `/live/alerts.cap`. The frontend has a "⚠ Alerts" tab. Baseline covers only the 5 fully archived countries.
- **Sub-cell inputs (v3).** Cache built for all countries (122M cell-days). A/B on 2024: day 1 within 5 km 57.9% vs 56.3% (+1.6, inside the noise).

## 14. Orbital Fire Watch integrated (done) + lighter backend
- `orbital/` contains the React UI (built into `orbital/frontend/dist`, 356 KB) and its services. The UI is served by the EquiFire backend at `/orbital/`, and its API at `/api/*` (fires, overpasses, satellites, track, spread cones). One server, no separate backend.
- Fires come from the EquiFire live NRT feed (no key), with the EquiFire live anomaly context (alert level, z) per fire once the live baseline is computed.
- Removed: the hardcoded FIRMS key (backend config and frontend default), and the invented "fallback" sample fires that were shown as real.
- Lighter backend:
  - `EQUIFIRE_LITE=1` = live features only (live fires, alerts, overpasses, Orbital). The archive is not loaded; peak about 0.7 GB in the test.
  - The `/classify` calendar is now loaded on first use; set `EQUIFIRE_PRELOAD_CALENDAR=1` to preload it.
- Rebuild the UI: `cd orbital/frontend && npm install && npx vite build` (node_modules was deleted to save 87 MB).

## 15. Claude continuation — ML + physics and hourly forecast weather

- [x] Combined daily predictor API and application selector; matching fitted artifacts and cutoff checks.
- [x] Per-cell fuel/terrain/barrier/dryness integration; archive-coordinate fix; hourly weather timeline and changing wind/rain.
- [x] Keyless cached NOAA GFS fallback after real Open-Meteo rate limits; explicit provenance and interpolation resolution.
- [x] Opt-in reconstructed MODIS-era training and small paired pilot; no gain, no model promotion.
- [x] Existing 2024/2025 ensemble runs completed, 132 checks passed, local backend updated.
- [ ] Refit/evaluate the blend on richer landscape physics and weather forecasts as issued. Current matched comparison is roughly flat; day-1 tolerant and day-3 exact-cell targets remain unmet.

Detailed current results supersede older status sections where they differ: reports/CLAUDE_CONTINUATION.md.

## 15. New backend features (2026-11-15), all mounted in backend/app.py
- `/replay/incidents`, `/replay/run` (honest replay, 6 curated incidents, cached): backend/replay.py
- `/nextobs`, `/blindspots`, `/scenarios`: backend/nextobs.py
- `/exposure` (GET/POST), `/brief`, `/brief.html`, `/brief.json`, `/brief.pdf`: backend/exposure.py, backend/brief.py
- `/predict`, `/predict/simulate` (ML + physics; ML+physics blend does not beat ML alone, see reports/ensemble_ab_*.json): backend/ensemble.py (Codex)
- `/harmonized/series`: harmonization headline (agent still working)
- Fix: backend/brief.py routes had an unresolvable `-> Response` annotation that made /openapi.json (and /docs) return 500; removed.
- Frontend UI for these is the open task (Codex owns frontend/): see COORDINATION.md requests.
