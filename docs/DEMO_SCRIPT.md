# EquiFire 3-minute demo: Flin Flon / Manitoba wildfires, 28 May 2025

One real, documented event, told through all eight challenge goals. Every number below was read from the running backend (`http://127.0.0.1:8080`) on 2026-11-15 and is saved in `cache/demo/flin_flon/*.json`. Nothing is invented. Re-run `scripts/demo_cache.py` to refresh the files.

**The event.** On 28 May 2025 Manitoba declared a province-wide state of emergency; roughly 17,000 people were ordered out and all of Flin Flon (about 5,000 residents) had to leave (reported by CBC, Global News, Canadian Press; the fire began at a landfill in Creighton, Saskatchewan, and crossed into Manitoba). Our data cover it: VIIRS and MODIS 2025 are in the archive.

**Demo point:** latitude 54.975, longitude -101.725 (centre of the strongest burning 0.05 degree cell that day, from `/fires`). Date 2025-05-28. Forecast start 2025-05-28 12:00 UTC.

## Before you go on stage (5 minutes)

1. Start the backend: `.venv/bin/python -m uvicorn backend.app:app --host 0.0.0.0 --port 8080` and wait until `/health` says `"status":"ready"` (about 26 s).
2. Warm everything: `.venv/bin/python scripts/demo_cache.py` (first run for this event took 262 s: 64 s landscape, 168 s physics weather + landscape; 6 s when already cached). Expect `Cached 14/14 steps`.
3. The backend keeps in-memory results only for 5 min (combined forecast) and 30 min (physics). The data underneath is cached on disk, so a repeat is fast (physics 0.0 s, combined forecast 25 s without the in-memory hit), but re-run `demo_cache.py` shortly before you present.
4. Open `http://127.0.0.1:8080/` (the backend serves the web app). Keep `cache/demo/flin_flon/` open in a second window as the offline backup.

## The 3 minutes

### 0:00 - 0:25  Problem and harmonization (Goal 1)

Click: **Monitor** tab. In *Define your area* type latitude `54.975`, longitude `-101.725`, press **Set area**, then **Load detections**.

Say: "MODIS sees 1 km pixels, VIIRS sees 375 m. A fire looks very different to each, and that breaks trends across 25 years. EquiFire puts both on one 0.05 degree grid and converts MODIS to VIIRS-equivalent. On held-out years the reconstruction finds 0.248 of the real VIIRS cells versus 0.177 for raw MODIS, and it recovers the total detection count (1.00x) where raw MODIS sees only 0.11x."

On the map you see the VIIRS and MODIS burning cells of that day (`/fires`); the raw detection count for the next step is 5,358.

### 0:25 - 0:50  One fire, not 782 (Goal 2: bow-tie)

Show: the clustered fires (or the `/clusters` result).

Say: "At the swath edge, MODIS pixels stretch and one fire turns into several. In this 1.5 x 1.2 degree box on that day there were 5,358 detections from 15 overpasses. A fixed 1 km clustering radius gives 782 clusters, our scan-angle-adaptive radius gives 401. Across three countries, at the edge, one real VIIRS fire used to become 1.339 MODIS clusters; now it is 1.007. The cost: at the edge, nearby real fires get merged."

### 0:50 - 1:20  Is this normal? (Goals 3 and 4: anomaly and routine vs anomaly)

Click: **History** tab, area size **1 degree** (a 7,468 km2 box centred on the demo point), then the burning calendar heatmap and the weekly chart.

Say: "The History tab is the burning calendar for this area. 2025 has 1,113 VIIRS burning cell-days. The mean of the 12 years 2013 to 2024 is 67.1, so this year is 16.6 times usual, z = 11.8, and weeks 21 to 25, 27, 28 and 31 are flagged. Week 23 alone has 526 cell-days against a normal 0.23. The classifier for that day says **anomaly**: out of season (active in at most 25% of other years this week; here 0%), 76.7 standard deviations above usual, a connected fire of 28 cells (about 840 km2). Compare this with stubble burning in Punjab, which it labels routine."

(For a 1.5 degree box, 16,536 km2, the same API call gives 2,469 cell-days vs mean 168.9, z = 9.4. The numbers depend on the box; say which box you drew.)

Honest caveat to say out loud: "On twelve independent test events the classifier is right 10 times out of 12. It is a triage hint, not a verdict. On the 22 events used to design it, it is 21 of 22, which we flag as optimistic."

(Live API call for the demo point: `/classify?lat=54.975&lon=-101.725&date=2025-05-28` returns `label: anomaly, z: 76.7, burning_cells_today: 8, cluster_cells: 28`.)

### 1:20 - 2:25  Where will it go? (Goal 7 and the forecast)

Click: **Forecast** tab. Set *Observation date* to `2025-05-28`, **Load detections**, click the biggest red cell near the area centre, set *Ignition* to **Archive fire on date**, *Horizon* **24 hours**, *Start (UTC)* `2025-05-28 12:00`, press **Run prediction**. Wait about 25 s (inputs are cached). Press **Play**. Tick **Show observed later (historical replay)**.

Say: "Every forecast in EquiFire runs two things together: a machine-learning model that uses only what was known on the start day, and a physics simulation with real hourly wind, humidity, fuel from ESA WorldCover, slope from Copernicus terrain and rivers and roads from OpenStreetMap as firebreaks. The player moves through the hour-by-hour perimeter; the coloured cells are daily guidance."

What the backend returned for the physics run (24 h, ERA5 replay weather, fuel mapped as forest, landscape coverage 100% land cover, terrain and OSM, 81.5% Sentinel-2):
- 37 archive ignition cells, 37 km2 start area, grown to 139 km2 in 24 h; low and high weather scenarios 100 and 172 km2.
- Spread-rate multiplier fitted on the fire's previous 3 days: m = 3.23, confidence "medium".
- Fire-weather context at the start (Canadian FWI): FFMC 93.9, FWI 34.3, danger class "extreme". Hour 0 weather: wind 14.4 km/h from 223 degrees, 16.4 C, RH 51%, 1-h fuel moisture 9.8%; by 00:00 UTC RH is 28% and fuel moisture 5.5%.

What the ML guidance returned, scored against what really happened (`/forecast`, VIIRS truth, 5.5 km cells):

| Day | Date | Cells we predicted | Cells really newly burning | IoU |
|---|---|---|---|---|
| 1 | 2025-05-29 | 37 | 15 | 0.156 |
| 2 | 2025-05-30 | 81 | 35 | 0.275 |
| 3 | 2025-05-31 | 92 | 72 | 0.355 |

The combined ML + physics guidance (`/predict`, blender fitted on 2024) selects 65, 92 and 106 cells for days 1 to 3. We computed its exact-cell IoU against the same truth ourselves from the two saved responses (a single-event check, not a benchmark): 0.111, 0.245, 0.369.

Say: "Look at the numbers honestly. The model over-predicts how many cells, which is why day 1 overlap is only 0.16. Over three days the real fire catches up and the overlap rises to 0.36. On 100 unseen fires per year the picture is the same: within one cell (5 km) we are right 52 to 59% on day 1 and 69 to 76% on day 3, but only 25 to 31% cell-exact on day 3. Combining physics with ML did not measurably beat ML alone; the 70% day-1 target is not met, and the physics simulator, checked on only 20 fires, over-predicts area by 3 to 6 times. This is research, not an evacuation tool."

### 2:25 - 2:45  When do we see it next? (Goals 5 and 6)

Click: the satellite overlay checkboxes (Layers) and the **Next satellite passes** card for the selected location.

Say: "At the incident point EquiFire propagates SGP4 orbits for Terra, Aqua, Suomi NPP, NOAA-20 and NOAA-21 and shows the countdown to the next pass, with scan angle and pixel size. Cloud and smoke mean a fire that vanishes from the map may still be burning; detection is not presence." (Countdown is relative to the current clock, so it shows the next pass at this location today, not on 28 May 2025.)

### 2:45 - 3:00  Live and export (Goals 3 and 8)

Click: **Alerts** tab, then the CAP / GeoJSON export.

Say: "The same machinery runs live. The FIRMS 7-day feed is compared with an 18-year same-week baseline: on 2026-11-15 00:15 UTC the live summary had 91 critical, 149 warning and 172 watch regions at 1 degree. Alerts export as CAP 1.2 and GeoJSON for QGIS, ArcGIS or a command centre. Honest limits: no lead-time study yet, baselines cover five countries (Brazil, Australia, India, United States, Canada), and Suomi NPP no longer delivers new data."

## Extra segments (use them to stretch to 5 minutes, or swap them in for 2:25-3:00)

All numbers below come from `reports/harmonization_headline.json` and the saved replays in `cache/replay/` (`/replay/run?incident=<id>&cached_only=1`), not from memory.

### A. The proof for the challenge: one record, 2000 to 2025 (about 45 s)

Click: **History** tab, scroll to **25-year harmonized record**. Region *Five countries*, measure *Fire detections*.

Say: "If you just splice MODIS before 2012 onto VIIRS after, the record jumps. For our five countries, 2012 has 5.2 times the detections of 2011, and the naive record shows a fire trend of plus 7.7 percent a year. That is mostly the sensor change. After harmonization the 2012 step is 1.16 times, and the trend is plus 0.6 percent a year, not significant. The continuous MODIS-only record agrees: no significant trend. On held-out years 2021 to 2025, harmonized detection totals match real VIIRS within 6 percent (annual correlation 0.99)."

Say, honestly: "Burning cell-days are weaker: the harmonized step is still 1.5 times, or 1.12 with a calibration to the overlap years, and the reconstruction finds only about 36 percent of the cells VIIRS sees, so it is biased by region (0.5 to 1.5 times). Detections and fire power are the clean measures. Only five countries have the full 25 years."

Do not say the step "disappears" for every measure.

### B. Honest replay: judge it yourself (about 60 s)

Click: **Replay** tab. Pick **Pantanal 2024** (savanna). Show the forecast first, drag the day slider 1 to 3, then press **Reveal what happened**. Switch strict / within 5 km.

Say: "The forecast is issued from only what was known at the start; the reveal comes after. Blue is a hit, orange a miss, pink a false alarm, grey hatched cells we could not have verified because of cloud. Here, within 5 km, we score 0.80 on day 1 and 0.88 on day 3 (ML plus physics)."

Then pick **Flin Flon 2025** and say: "Not every case is good. Our demo fire scores only 0.38 on day 1, below the simple baseline of marking every cell, and 0.77 on day 3. Across the six curated incidents, day 3 is 0.71 to 0.93 within 5 km, but day 1 is above 0.70 in only two of six (Pantanal 0.80, Punjab stubble 0.81). Six incidents are a showcase, not a benchmark; the 100-fire test is the benchmark. The physics part uses real weather for the forecast period, so its skill here is flattering compared with a true forecast."

Cloud case to show if asked: **Roraima 2024** or **Kalimantan 2023** (ML only): many "false alarms" sit where no clear overpass could have seen a fire, and the panel shows the adjusted score.

### C. Optional, only if you have tested the Forecast extras screens (they were written but not browser-tested when this was written)

- **Until next pass:** the last-detection age and the next usable satellite pass for the selected fire (`/nextobs`), with the simulation horizon set to that pass. Say: "Cloud cover is a visibility indicator, not proof that a sensor misses a fire."
- **Blind spots:** `/blindspots` separates "no fire detected, good observation" from "insufficient observation". Say: "Grey hatch means unknown, not safe."
- **Scenarios:** `/scenarios` compares baseline wind with stronger wind, rotated wind, rain and drier air. Always say: "Hypothetical weather, not the issued forecast."
- **Exposure and brief:** `/exposure` shows which mapped settlements, roads, power sites, hospitals and schools the simulated footprint reaches and when. Say: "Estimated exposure, not damage; population is a separate figure; we never output money." `/brief.html` is the one-page printable brief (also `/brief.pdf`). Industrial heat sources such as steelworks can appear as fires.

If these screens are not working, skip C and quote the endpoints from `docs/MODEL_CARD.md` instead.

### Suggested 5-minute order

0:00 problem and harmonization (A, 45 s) → 0:45 one fire, not 782 → 1:10 is this normal (History + classify) → 1:40 forecast and honest numbers → 2:45 replay (B, 60 s) → 3:45 next pass and exposure (C, only if tested) → 4:30 live alerts and export → 5:00 close: "Experimental and uncalibrated, with our limits stated up front."

## Questions you should expect

- "How accurate is it?" Quote the table in `docs/MODEL_CARD.md`: 52 to 59% day 1 and 69 to 76% day 3 within 5 km; 25 to 31% exact day 3; 1 km alerts precision 63 to 66%, recall 9 to 15%. Target of 70% on day 1 is not met.
- "Why z = 76.7?" The historical standard deviation of this area in this week is 0.09 cells. Huge z means "never seen", not "76 times more dangerous".
- "Why does the forecast differ from the real fire?" Day-1 targets are tiny (often 1 to 5 cells), 29% of burning cells are re-detected next day, and VIIRS misses fires under cloud and smoke.

## If the network goes down

What works offline, because it reads local files: the whole archive (`/fires`, `/stats`, `/classify`, `/clusters`, `/forecast`, `/predict`), the History tab and the satellite overpass countdown (embedded TLEs, a few days stale). What works only if `demo_cache.py` was run beforehand: the physics run and the combined forecast (weather, landscape and DEM are cached on disk in `backend/.cache/meteo_*.json`, `cache/landscape`, `cache/gfs_forecast`). What stops: basemap tiles (OpenStreetMap, Esri), RainViewer radar, the wind layer, the icon/font CDNs (unpkg, Google Fonts), and live NRT data (the backend shows the last fetch marked stale).

1. Switch the map to the **Globe** view (local textures in `frontend/assets/`) or just present on the dark map background with the fire cells and perimeter on top.
2. Run the forecast again: it will be answered from cache. If the in-memory result has expired and a fresh network call is needed, say so, then read the saved responses in `cache/demo/flin_flon/` (`classify.json`, `forecast.json`, `predict.json`, `predict_simulate.json`, `stats.json`, `clusters.json`).
3. Skip the live Alerts segment, or show `live_summary.json` / `live_alerts.json` as "the last snapshot".
4. Keep a screen recording of the full run as the last resort. **Not tested with the network physically disconnected** in this session; rehearse it once with Wi-Fi off.
5. Do not pre-cache or script-download OpenStreetMap tiles: the OSMF tile policy forbids bulk and offline use.

## Alternate event (independent of the combined forecast)

`python scripts/demo_cache.py --event yellowknife` would cache (not run in this session) Yellowknife, NWT, 2023-08-13 at 62.725, -115.125. `/classify` there returns anomaly, z = 42.7, 17 burning cells in the region, a 37-cell (about 1,110 km2) connected fire; this event is in the independent 12-event set and was classified correctly. The ML `/forecast` works (v1 model, start 2023-08-13: IoU 0.179, 0.213, 0.191 on days 1 to 3; 46, 80, 112 cells predicted vs 20, 34, 44 observed), but `/predict` and the combined forecast refuse 2023 dates (no blender trained before 2023), so the always-combined Forecast tab will show an error. Use it only for the History and classification story.
