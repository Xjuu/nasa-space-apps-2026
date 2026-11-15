# Handoff: what is left (written 2026-11-15 ~07:40; TODO.md left untouched on purpose)

Do these in order, do not re-plan. Servers: backend+frontend http://192.168.1.66:8000 (uvicorn backend.app:app, started ~07:22), static copy :5173. Logs: logs/backend.log. Honest numbers: docs/MODEL_CARD.md. Shared notes: COORDINATION.md.

## 1. Forecast-extras UI agent was stopped mid-work (aae8feaffeae64c19)
Files: frontend/ui-forecast-extras.js/.css, ui-forecast-blindspots.js, ui-forecast-scenarios.js, ui-forecast-exposure.js (all pass `node --check`). Written, NOT browser-tested: next pass (/nextobs), blind spots (/blindspots), scenarios (/scenarios), exposure (POST /exposure with player frames), brief links (/brief.html, /brief.pdf).
- Open in browser (Forecast tab, 1440 px + 390 px), fix console errors, test exposure with cache/demo/flin_flon/simulate.json (Flin Flon 54.975,-101.725; Overpass is slow).
- Backend request in COORDINATION.md: /predict/simulate (backend/ensemble.py ~line 252, Codex file) hard-codes calibration='on'; add a `calibration` query param, default off.

## 2. Small known fixes
- Replay draws on 2D map only; replay panel long on desktop; legend overlay covers map on phones.
- History default measure is harmonized FRP, which STILL steps x1.4-3.5 at 2012. MODIS-only cell-days is the steady one. To default to it: `hist.metric` in frontend/app.js + preselect in `initPicker()` of frontend/ui-history-fix.js.
- Phone: History charts scroll sideways (era labels off-screen).
- Console 404s for ui-* files should be gone now (all exist); re-check.

## 3. Not done (user said skip: LICENSE, first git commit, slides)
- Video (2 min): user records; script in docs/DEMO_SCRIPT.md (event Flin Flon 2025-05-28; note Flin Flon day-1 replay is weak, 0.38 within 5 km).
- Offline-demo test with network off (cache/demo/, cache/replay/): untested.
- Rotate the FIRMS key (it was in orbital zip). Team names in README. Verify HRSL licence line.
- Git: repo initialised (branch main), .gitignore written, NO commit.

## 4. Honest status (do not overclaim)
- ML spread: ~70-75% within 5 km at day 3; day 1 only ~52-59% (70% target NOT met); exact cell ~25-31%. 1 km alerts precision ~63-66%, recall 9-15%.
- ML+physics blend does NOT beat ML alone (reports/ensemble_ab_*.json). Physics: landscape layers fix over-prediction (median sim/obs ~0.5-1.8), calibration inconsistent; scenario tool only.
- Harmonization: detections step x5.24 raw -> x1.16 harmonized; cell-days x1.50 (x1.12 calibrated); US/Brazil/India cell-days biased 0.5-1.5x; only 5 countries have 2000-2025.
- Routine vs anomaly: 10/12 independent events. Bowtie edge clusters 1.339 -> 1.007.

## 5. Optional later
- Process the other 171 downloaded countries (data_country 37+ GB; only 5 countries in cache/cells).
- Chart title fixed in scripts/harmonized_series.py (regenerated at 07:19).

## 0. Deadline
User said ~5.5 hours of hackathon remain (as of ~07:40 on 2026-11-15). Priority order: (1) browser-test + fix Forecast extras UI, (2) offline-demo test, (3) /predict/simulate calibration param, (4) small fixes. Do not start new research.
