# FireSpot frontend

Map-first wildfire monitoring workspace for NASA FIRMS MODIS/VIIRS detections, historical comparisons, forecast replay and alerts. The static frontend requires no build step.

## Interface

Use Monitor, Forecast, History and Alerts in the top navigation. Satellite tracking opens the separate MODIS/VIIRS pass viewer served by the backend at `/orbital/`.

- Define an area by drawing, using the map centre, or entering latitude and longitude. The maximum is 25,000 km²; coordinate bounds are available under Area coordinates.
- Load detections, then select a location to inspect it on the map. Map is the default view; Globe is optional.
- Forecast offers historical replay, estimated spread and later observed detections. History provides a burning calendar, weekly comparisons and sensor totals, with JSON/CSV downloads.
- Alerts exposes the existing alert analysis and exports. Connection details and methodology are expandable.
- Desktop places analysis beside the map. Mobile stacks the map and analysis with normal page scrolling. Controls support visible keyboard focus; Escape cancels area drawing.

- Layers menu (map): basemap (OSM / Esri imagery / Esri topo, all keyless), fire detections, RainViewer precipitation radar, Open-Meteo 10 m wind arrows (one request per view, 5 x 4 grid, cached), fire satellites and orbit tracks.
- Fire satellites (Terra, Aqua, Suomi NPP, NOAA-20, NOAA-21) move live on the globe and the map: positions interpolated every second from `/api/satellite-track` (about one orbit, refreshed every 10 min), re-anchored on `/api/satellites/live` every minute. Each satellite can be toggled in the "Fire satellites" panel.
- Forecast always uses ML + physics (`/predict/simulate`), with persistent play/pause/step/slider/speed controls, daily combined risk cells and finer physical frames. Low/high outlines are weather-sensitivity scenarios; model scores are uncalibrated. Any fire popup has "Predict spread".
- Every approximate position carries an "Estimated location" badge with the reason (pixel centres, 5 km cells, snapped forecast centre, reconstructed MODIS-era data, assumed ignition, assumed fuel).
- Icons: lucide@0.344.0 (unpkg). Fonts: Outfit + Inter (Google Fonts). No emojis.

Data services still require their existing backend configuration, including `FIRMS_MAP_KEY`. The interface shows loading, empty and service-error states. UI checks and screenshots are documented in [UI_REDESIGN.md](../reports/UI_REDESIGN.md).

## Run

From the project root:

```sh
# Start backend API (FastAPI on port 8080)
.venv/bin/python -m uvicorn backend.app:app --host 0.0.0.0 --port 8080

# Start static frontend server (port 5173)
.venv/bin/python -m http.server 5173 --bind 0.0.0.0 -d frontend
```

Open `http://localhost:5173/` in your browser.

## Combined forecasts and timeline

Forecast always runs **ML + physics** through `/predict/simulate`. There are no method switches. Select a satellite fire, choose the initialization time, 3–72-hour horizon and playback interval, then run the prediction. The map shows combined daily risk cells and the time-varying physical perimeter together.

The timeline is visible whenever Forecast is open. Play/pause, start/end, step buttons, keyboard scrubbing and playback speed become available after a successful prediction. Changing settings clears old frames; missing components show an error without switching to another model. On phones, the forecast map is compact so the player fits beside the working view vertically.

ML guidance retains its daily UTC horizons; the simulator supplies the finer time steps using hourly weather. Full results export as JSON, with combined daily cells also available as GeoJSON. Details: [always-combined workflow](../reports/ALWAYS_COMBINED.md).

Map zoom/panning is constrained to avoid grey bands outside Mercator coverage. Playback colours show timing, not confidence. Areas sum all included fire groups; the confidence badge explains the available evidence without assigning an unsupported percentage. See [India case and rendering fix](../reports/MAP_CONFIDENCE_FIX.md).
