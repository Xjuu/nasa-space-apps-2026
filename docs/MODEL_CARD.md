# FireSpot model cards

NASA Space Apps 2026, challenge "Harmonization of MODIS and VIIRS Hot Spots". Project FireSpot.

**Read this first.** Everything below is a retrospective research benchmark on satellite detections. FireSpot is **not** validated for operational decisions (evacuation, dispatch, insurance pricing, safety of life). Every number is copied from the repository's own reports (`TODO.md`, `COORDINATION.md`, `reports/*.json|md|txt`); nothing was rounded up. Where a sample is small the sample size is stated next to the number. The "truth" in almost every test is a **VIIRS satellite detection**, which misses fires under cloud and smoke, so "not detected" is not "not burning".

Common conventions

- Grid: detections are aggregated onto a 0.05 degree grid (about 5.5 km). One "cell-day" = one cell with at least one detection on one day.
- Strict score: IoU (overlap) of predicted vs real *newly burned* cells. If both are empty the score is 100%.
- "Within 5 km" (tolerance) score: the same, but a prediction one cell off still counts. It is a spatial overlap score, **not** classification accuracy or precision.
- Walk-forward: the model for year Y is trained only on fires before Y (the base-fit/calibration year ordering is stated per model). No learning from a fire before it is predicted.
- Archive coverage: MODIS 2000-2025 and VIIRS S-NPP 2012-2025, NOAA-20 2018-2025 downloaded for 176 countries; **fully processed (baselines, spread training) for 5 countries only: Brazil, Australia, India, United States, Canada**.

| # | Method | Code | Status |
|---|---|---|---|
| 1 | MODIS to VIIRS-equivalent size/FRP harmonization | `equifire/datasets.py`, `equifire/train.py` | validated (held-out years) |
| 2 | MODIS-era (2008-2011) VIIRS reconstruction | `harmonize.py`, `equifire/harmonize.py` | validated (held-out years) |
| 3 | Direct multi-day spread model (ML, XGBoost) | `equifire/direct.py`, `walk_forward.py` | validated (100 fires/year) |
| 4 | ML + physics blend (experimental) | `equifire/ensemble_core.py`, `backend/ensemble.py` | validated, **no gain over ML** |
| 5 | Physics spread simulator + landscape/hourly weather | `backend/physics_spread.py`, `equifire/landscape.py` | validated on 20 fires only; over-predicts |
| 6 | 1 km high-confidence alerts | `alerts1km.py`, `equifire/alerts1km.py` | validated (test 2023-2025) |
| 7 | Routine burning vs anomaly classifier | `classify.py`, `equifire/seasonal.py` | 10/12 independent events |
| 8 | Bow-tie / scan-geometry adaptive clustering | `equifire/geometry.py`, `equifire/clusters.py`, `bowtie.py` | validated (pooled, 3 countries) |
| 9 | Live anomaly early warning (z-score) | `backend/live.py` | implemented, **no lead-time / false-alarm validation** |
| 10 | Satellite overpass countdown | `backend/orbits.py` | SGP4 geometry, no accuracy study |

---

## 1. MODIS to VIIRS-equivalent harmonization (fire size and FRP)

- **Purpose.** Express a MODIS observation (1 km pixel) as what VIIRS (375 m) would have measured in the same cell: burning footprint (km2) and fire radiative power (MW). Removes the artificial step between sensor families in multi-decade trends (challenge goal 1).
- **Inputs.** MODIS count, FRP, scan/track, brightness, neighbours and recent activity in the cell (gradient-boosting regressors, `models/size_area.joblib`, `models/size_frp.joblib`).
- **Training data.** Cells where MODIS and VIIRS observed the same cell within +-30 minutes; VIIRS is the label.
- **Evaluation.** Unseen years 2023 and later.
- **Validated numbers.** Fire-size error 69% lower than raw MODIS and 24% lower than a straight-line correction; FRP error 18% lower than raw MODIS (`reports/EquiFire_status.txt`, section 2a). The number of coincident pairs behind these figures is not recorded in the status files.
- **Failure modes.** Needs a coincident VIIRS overpass to learn from, so it generalises less well to regions/fire types with few coincident pairs. Per-cell FRP of the *reconstruction* model (next card) is not better than raw MODIS.
- **Intended use.** Trend analysis and visual comparison of MODIS-era vs VIIRS-era fire size. **Not for** regulatory emissions accounting.

## 2. MODIS-era reconstruction, 2008-2011 (`harmonize.py`, v1)

- **Purpose.** Build a VIIRS-equivalent archive for years when only MODIS existed, so the calendar baselines can start before 2012.
- **Inputs.** 39 MODIS-derived features only (`models/harmonize_meta.json`). Predicts which 0.05 degree cells VIIRS would see burning, plus detections, FRP and area.
- **Training data and years.** Trained 2012-2019 (8,520,134 rows), validated 2020 (threshold chosen there), tested 2021-2025 on 8.5M real VIIRS cell-days (never seen).
- **Validated numbers (held-out 2021-2025).**

| | Precision | Recall | IoU | Predicted/true cells | Total FRP ratio | Total detections ratio |
|---|---|---|---|---|---|---|
| Reconstruction model | 0.467 | 0.345 | **0.248** | 0.74 | 1.05 | **1.00** |
| Raw MODIS | 0.724 | 0.189 | 0.177 | 0.26 | 0.60 | **0.11** |

  Against S-NPP-only truth (what exists before 2012): IoU 0.256 vs 0.208 for raw MODIS. Output: 3.34M reconstructed cell-days for 2008-2011 (`cache/viirs_equiv_daily.parquet`, `load_harmonized_daily()`, with a `source` column).
- **Effect on spread forecasts for old fires** (200 fires, scored against real VIIRS, mean IoU days 1-7): real VIIRS input 0.209; reconstructed input 0.114; raw MODIS input 0.103. Old-fire forecasts are about 45% weaker.
- **Known failure modes.** Only about 61% of VIIRS cell-days lie near any MODIS fire, so small fires MODIS never saw cannot be recovered. Precision is lower than raw MODIS (it trades precision for recall). Per-cell FRP is not better than raw MODIS: gains are in *which* cells burn and in the totals. 2000-2007 has not been applied yet.
- **Intended use.** Multi-year counts, calendars, baselines. Reconstructed rows must always be labelled as such. **Not for** individual-fire reconstruction.
- **Opt-in training pilot.** Adding reconstructed 2008-2011 cases to spread training (20 extra synthetic cases at half weight, 15 test cases) did not improve results (tolerant day 1 53.53% to 52.76%, exact day 3 24.77% to 24.41%; `reports/modis_era_pilot.json`). Not promoted.

## 3. Direct multi-day spread model (ML)

- **Purpose.** For a detected fire on start day t0, predict which 0.05 degree cells newly burn within k = 1..28 days (served: 1-3 days in the app).
- **Inputs.** Only what is known at t0, in a 41x41-cell window (about 225 km): distance to the front, burning cells and FRP in rings, recent days' activity, cell burn history (up to 365 days), direction/wind alignment, fire size and FRP trend, location, season, weather; v2 adds MODIS detections, day/night split, front shape, growth; v3 adds 375 m sub-cell positions. Selection rule per horizon (probability threshold or expected-overlap rule) fixed on held-out earlier fires.
- **Training data.** Per-year walk-forward models (`models/wf_<year>.json`) trained on fires before the test year (about 3,000 fires/year for the first reported runs; 2,000 for the later A/B runs). The backend `/forecast` endpoint serves the v1 model `models/spread_direct.json` (val AUC 0.856, trained before 2023; `/health` reports `test_from: 2023`).
- **Evaluation protocol.** Walk-forward, 100 unseen sampled fires per test year, 3-day horizon, models reused when settings match. Sampled cases can overlap the same real incident, so the effective independent sample is smaller. With 100 fires the noise is about +-3 points.
- **Validated numbers** (`reports/EquiFire_status.txt`, section 3):

| Test year (100 fires) | Strict day 1 | Strict day 3 | Within 5 km day 1 | Within 5 km day 3 |
|---|---|---|---|---|
| 2023 | 17.8% | 24.7% | 52.5% | 69.2% |
| 2024 | 22.5% | 30.7% | 59.3% | 75.6% |
| 2025 | 17.6% | 27.3% | 52.1% | 71.9% |

  28-day forecasts (300 fires): within 5 km mean over all days 69-75%; strict mean 27-30% (old day-by-day model: 11% strict). **The project target (within-5 km day 1 above 70%) is not met**: day 1 is 52-59%, exact-cell day 3 is 25-31%.
- **What did not help** (all inside the noise of 100 fires): sub-cell 375 m inputs, day 1 within 5 km 57.9% vs 56.3% (+1.6 points); physics + weather features v4 (58.2 vs 57.9, paired bootstrap interval includes zero); online learning (+0.1 points); short-horizon/equal-event-weight models (day-1 tolerant 60.0 / 65.5 / 61.8%, strict day 3 25.5 / 30.4 / 27.9%, a research candidate that is not wired into the app); NOAA reanalysis weather for full coverage (59.4 / 65.7 / 61.8% day-1 tolerant); neighbourhood-label surrogate. `reports/coarse_accuracy_experiments.md`.
- **Known failure modes.** Day-1 targets are tiny (often 1-5 cells) and only about 29% of burning cells are detected again the next day (cloud, smoke, overpass timing). Truth is incomplete. Weather in backtests is observed reanalysis, as if the forecast were perfect, and only about 5% of candidate cells had weather in the original runs. The model over-predicts area in the demo event (see `docs/DEMO_SCRIPT.md`). Probabilities are **not calibrated**.
- **Intended use.** Research and situational awareness: "where is the front likely to be in 1-3 days, roughly". **Not for** operational decisions or any use that needs a cell-exact position.

## 4. ML + physics blend (experimental, the app's default forecast)

- **Purpose.** The app always runs ML guidance and hourly physics together (`/predict`, `/predict/simulate`); component failure is an error, there is no silent standalone fallback.
- **Inputs.** The ML probability per cell plus physics burned fraction/arrival from `equifire/physcells.py` (archive weather, flat terrain), fed to a small XGBoost blender.
- **Training data/years.** Blender fitted on 150 earlier-year cases (fit year = test year - 1; `ensemble_blend_2024` is fit on 2023 with ML `wf_2024`, `ensemble_blend_2025` on 2024). The API refuses dates the blender was not trained before (e.g. no blender for 2023 dates).
- **Evaluation.** 100 test fires per year, one-cell tolerance rules chosen before the test year.
- **Validated numbers** (`reports/ensemble_ab_2024.json`, `reports/ensemble_ab_2025.json`):

| Year | Method | Day 1 tolerance | Day 3 tolerance | Day 3 exact IoU |
|---|---|---|---|---|
| 2024 | ML (rules retuned on same earlier cases) | 58.89% | 75.87% | 30.78% |
| 2024 | ML + physics blend | 58.68% | 76.07% | 30.72% |
| 2025 | ML (retuned) | 57.09% | 73.42% | 28.48% |
| 2025 | ML + physics blend | 56.82% | 73.48% | 28.31% |

  **The blend did not improve on a fairly retuned ML baseline.** Splits and bootstrap group fire cases, not independently verified incidents.
- **Known failure modes.** The fitted blender uses the version-2 physics (point archive weather, flat terrain). The richer landscape/hourly pipeline is displayed beside it and was **not** used to refit the blender and has no accuracy claim. Backtests use observed future weather, not forecasts as issued. Real-time mode has only 7 days of history (flagged "reduced history").
- **Intended use / not for.** Exploration and demonstration. Colours show timing, not confidence; low/high outlines are weather-sensitivity scenarios, not confidence intervals.

## 5. Physics spread simulator (Rothermel/Anderson fuels, hourly weather, landscape)

- **Purpose.** 3-72 h perimeter simulation around active detections with wind, slope, fuel moisture and barriers (challenge goal 7): `GET /simulate`.
- **Inputs.** Open-Meteo hourly weather (or cached NOAA GFS fallback), ESA WorldCover fuel groups, Copernicus GLO-30 slope, OSM roads/rivers as firebreaks, Sentinel-2 dryness, optional GOES pixels, optional calibration multiplier fitted on the fire's own previous 1-3 days.
- **Training data.** None (physical model); only the optional spread multiplier is fitted per fire, using detections up to the start time.
- **Evaluation protocol.** Validation set A: 10 fires selected by a fixed rule (seed 2026); set B: 10 new fires (seed 7), nothing tuned on B. Truth proxy: unique 375 m VIIRS pixels newly detected after start. **20 fires in total** (`reports/physics_validation.json`, `reports/physics_validation_v2.json`).
- **Validated numbers.** Median simulated / observed new-area ratio, uncalibrated start, days 1/2/3: 2.04 / 1.40 / 3.24 (set A) and 2.83 / 4.91 / 7.19 (set B). With calibration on (start + 1 day): 2.72 / 3.27 / 3.10 (A) and 4.39 / 6.33 / 4.63 (B). Within a factor of 2 only 0-50% of fires depending on protocol. In short: **the simulator over-predicts, typically 3-6x with calibration and up to about 19x in the worst uncalibrated protocol.** Calibration is a partial fix only (some day-1 overlap got worse on set B).
- **Known failure modes.** Over-predicts savanna/shrub strongly; forest under-predicted in the first validation. The truth proxy (375 m active pixels) underestimates burned area, so part of the "over-prediction" is a proxy artefact, but not all of it. Historical tests use observed (reanalysis) weather, not issued forecasts. Overpass/OSM data can be missing; the response reports fallbacks and partial coverage. The landscape-enabled pipeline has not been validated against fire outcomes.
- **Intended use.** Illustrative "which way would it go" scenarios. **Not for** predicting area burned, structure threat or timing of arrival.

## 6. 1 km high-confidence alerts (`alerts1km.py`)

- **Purpose.** Flag about 1 km cells likely to get new fire within +-1 km of the prediction, near an existing detected fire, horizons 1 and 3 days.
- **Inputs.** Local fire history around the cell, distance to the front, season; optional weather.
- **Training data/years.** Trained 2012-2020; threshold chosen on 2021-2022; tested 2023-2025 (4,500 fire-days, 5 countries).
- **Validated numbers.** Next day: threshold 0.56, **precision 63.2%, recall 9.2%**, 1.8 alerts per fire-day (best simple baseline precision 33.8%). 3 days: threshold 0.58, **precision 65.7%, recall 15.1%**, 7.1 alerts per fire-day (baseline 42.7%). At threshold 0.65, precision 69-72% with about 3-8% recall.
  Experimental variants with a 3 km match radius (6,000 held-out fire-days; a looser tolerance, not comparable): precision 67.6% / recall 40.3% (1 day), 68.0% / 49.8% (3 days); v2 candidate 72.3% / 34.8% (1 day), 71.1% / 49.4% (3 days). The 70% precision target was not met by the earlier models; v2 candidates were not deployed in place of the 1 km models.
- **Known failure modes.** Alerts cover only a small part of the new fire (recall 9-15% at 1 km). Adjacent tiles/days can belong to the same event, so test cases are correlated. Weather coverage in training was 7-15% of tiles.
- **Intended use.** "Which few cells are most likely next", precision-first. **Not for** a complete picture of where fire will spread.

## 7. Routine agricultural burning vs fire anomaly (`classify.py`)

- **Purpose.** Per 0.25 degree region (about 28 km) and date, separate recurring seasonal burning (stubble, prescribed, savanna) from an anomaly (challenge goal 4). Deterministic rules, not a trained model.
- **Inputs.** VIIRS 2012-2025 calendar built from all **other** years (leave-one-year-out). Seven evidence pieces: season frequency, z-score (spread floored at 30% of the mean), field recurrence within +-14 days in at least 2 other years, one-day spike, FRP per detection, largest connected cluster, persistence.
- **Evaluation.** 22 reference events (rules designed while looking at them, **optimistic**): 21/22 correct. **12 independent events** (added after the rules were fixed): **10/12 correct** (anomalies 5/6, routine 5/6). Misses: Lytton 2021 (the snap point landed on a small recurring fire) and Haryana stubble burning 2021 (labelled "elevated" because the season was 7 sigma above usual). `reports/classify_known_events.csv`, `reports/classify_holdout_events.csv`.
- **Caveats.** 12 events is a tiny sample and the events were chosen by the team. Reference points are approximate and the evaluator snaps to the strongest burning cell within 1 degree and +-5 days; the live `/classify` call does **not** snap, so clicking beside a fire returns z = 0 and a baseline of 0. The baseline includes years after the event date (retrospective, not a causal real-time baseline). Only the 5 processed countries have a baseline. No cropland mask yet.
- **Intended use.** A triage hint with plain-language reasons. **Not for** deciding that a fire can be ignored.

## 8. Bow-tie / scan-geometry adaptive clustering (`equifire/clusters.py`)

- **Purpose.** Stop duplicate clusters at the swath edge, where MODIS pixels stretch (goal 2). Cluster radius scales with the pixel footprint (narrow at nadir, wide at the edge). Geometry from TLE + SGP4 within 3 days of the TLE epoch; for archive detections the scan angle is recovered from FIRMS scan/track pixel sizes.
- **Evaluation.** `python bowtie.py eval`: Aqua vs VIIRS +-30 min, Brazil + Bolivia + Australia 2023, pooled (`reports/bowtie_eval.json`).
- **Validated numbers.** MODIS clusters per single VIIRS fire at the swath edge (48-56 degrees): fixed 1 km radius **1.339** (26% of fires split into duplicates), adaptive **1.007** (0.7% split). Edge cluster count -39%. Edge/nadir ratio: adaptive 0.95, fixed 1.27.
- **Known failure modes.** Cost of the adaptive radius: at the edge, 93% of boxes with at least two VIIRS fires merge into one MODIS cluster (61% with the fixed radius), so nearby separate fires get merged. Only three countries evaluated.
- **Intended use.** De-duplicating counts and drawing clusters (GeoJSON/CAP). **Not for** counting separate ignitions.

## 9. Live anomaly early warning (`backend/live.py`)

- **Purpose.** Compare the FIRMS NRT 7-day feeds with the same-week, same-grid history; alert levels watch (z >= 2), warning (z >= 3), critical (z >= 5), plus an out-of-season flag; CAP 1.2 export (goal 3).
- **Inputs.** NOAA-20 / NOAA-21 / MODIS NRT feeds (keyless); 18-year baseline: 2012-2025 VIIRS S-NPP plus 2008-2011 reconstructed (scale 1.339).
- **Evaluation.** **None** beyond functional checks: no lead-time or false-alarm study exists. The status files only give example outputs (for instance 2026-11-14: 102 critical, 155 warning, 172 watch regions at 1 degree in the first status run; the live count changes with every refresh).
- **Known failure modes.** Baseline is S-NPP only while the observation is NOAA-20 (documented); FIRMS states S-NPP no longer delivers new data, which affects fallback behaviour and how the baseline can be extended. Regions with no archive fire within about 5 km are marked "no baseline". Baseline covers only the 5 processed countries. Large z values come from tiny historical standard deviations.
- **Intended use.** Prioritising regions to look at. **Not for** issuing public warnings.

## 10. Satellite overpass countdown

SGP4 propagation from current TLEs for Terra, Aqua, S-NPP, NOAA-20, NOAA-21. Reports the next pass, scan angle and pixel size. No accuracy study. TLEs go stale in days (the orbital service refreshes them from CelesTrak in the background); results beyond a few days from the TLE epoch are unreliable. Intended for planning when the next thermal confirmation could arrive, not for pass timing to the second.

---

## Claims we do NOT make

| Claim | Why we do not make it |
|---|---|
| "70% accurate" day-1 spread forecasts | Measured day-1 within-5 km is 52-59% (original), up to 60-65% only for an un-wired research candidate; target not met. Exact-cell day 3 is 25-31%. |
| Calibrated probabilities or confidence | Scores are uncalibrated. Low/high outlines are weather scenarios, not confidence intervals. |
| Physics simulation predicts area burned | Validated on 20 fires only; over-predicts, typically 3-6x with calibration. |
| ML + physics beats ML alone | Measured gain is nil (58.68 vs 58.89%, 56.82 vs 57.09% day-1 tolerance). |
| Forecast skill with real weather forecasts | Backtests use observed reanalysis weather; no issued-forecast backtest exists. |
| 1 km alerts cover the fire | Precision 63-66% but recall only 9-15%. |
| Routine/anomaly classifier is 83% (10/12) accurate in general | 12 hand-picked events, 5 countries; 21/22 on the tuning set is optimistic. |
| Reconstructed 2008-2011 data equals real VIIRS | IoU 0.248, 61% reach limit; per-cell FRP not better than raw MODIS; spread skill 0.114 vs 0.209. |
| Bow-tie fix finds separate ignitions | Adaptive radius merges 93% of close fire pairs at the swath edge. |
| Early-warning alerts are validated | No lead-time or false-alarm evaluation. |
| Worldwide coverage of baselines | Baselines and spread training fully cover Brazil, Australia, India, US, Canada only. |
| Detection equals fire presence/absence | Satellites miss fires under cloud/smoke; no-detection is not no-fire. |
| Operational readiness, endorsement by NASA/ESA/NOAA | Research prototype built for a hackathon; no endorsement implied. |
