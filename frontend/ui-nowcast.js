/* Satellite Overpass Nowcast: Predicts fire front position and satellite detection at next overpass.
   Connects to backend/nowcast.py: GET /nowcast/pass. */
(function () {
  "use strict";
  const EF = window.EquiFire, FX = window.EquiFireFX;
  if (!EF || !FX) return;
  const { map, esc } = EF;
  const $ = (id) => document.getElementById(id);

  const sec = FX.section({ id: "nowcast", title: "Nowcasting przelotu satelity", icon: "satellite", order: 54 });
  sec.body.innerHTML = FX.warn("Projekcja heurystyczna łącząca mechanikę orbitalną SGP4, zniekształcenia bow-tie i model MTT. Zachmurzenie jest wartością prognozowaną (wskaźnik widoczności).") +
    `<p class="fx-note">Ekstrapoluje pozycję czoła ognia do momentu najbliższego przelotu satelity MODIS/VIIRS (NOAA-20, NOAA-21, Aqua, Terra). Oblicza rzeczywisty rozmiar piksela przy kącie skanu oraz szansę detekcji w warunkach zachmurzenia.</p>` +
    `<div class="fx-grid2">` +
    `<label class="input-field"><span>Preferowany satelita</span><select id="fx-nc-sat"><option value="" selected>Najbliższy użyteczny</option><option value="noaa20">NOAA-20 (VIIRS 375m)</option><option value="noaa21">NOAA-21 (VIIRS 375m)</option><option value="aqua">Aqua (MODIS 1km)</option><option value="terra">Terra (MODIS 1km)</option></select></label>` +
    `<label class="input-field"><span>Model paliwa</span><select id="fx-nc-fuel"><option value="auto" selected>Automatyczny</option><option value="forest">Las (FM10)</option><option value="shrub">Zarośla (FM6)</option><option value="savanna">Sawanna (FM2)</option><option value="grass">Trawy (FM1)</option></select></label>` +
    `</div>` +
    `<div class="fx-actions" style="margin:10px 0;">` +
    `<button type="button" id="fx-nc-run-btn" class="btn-primary" style="width:100%;">${FX.ic("satellite") || "🛰️"} <span>Oblicz nowcasting dla przelotu</span></button>` +
    `</div>` +
    `<div id="fx-nc-slot"></div><p id="fx-nc-status" class="fx-note" role="status">Gotowy do wyliczenia nowcastingu.</p><div id="fx-nc-out"></div>`;

  const job = FX.job($("fx-nc-slot")), statusEl = $("fx-nc-status"), out = $("fx-nc-out");
  const satSel = $("fx-nc-sat"), fuelSel = $("fx-nc-fuel"), runBtn = $("fx-nc-run-btn");

  const group = L.layerGroup();
  let lastResult = null;

  async function executeNowcast() {
    if (job.busy) return;
    runBtn.disabled = true;

    let centerLat = 54.975, centerLon = -101.725;
    const sim = EF.sim && EF.sim.data;
    if (sim && sim.grid && sim.grid.center) {
      centerLat = sim.grid.center[0];
      centerLon = sim.grid.center[1];
    } else if (EF.state && EF.state.selectedPoint) {
      centerLat = EF.state.selectedPoint.lat;
      centerLon = EF.state.selectedPoint.lon;
    }

    const q = {
      lat: centerLat,
      lon: centerLon,
      fuel: fuelSel.value,
      sat: satSel.value || undefined
    };

    try {
      const res = await job.run(
        "Kalkulacja geometrii orbity i ekstrapolacji frontu…",
        "Propagacja SGP4 + symulacja rozprzestrzeniania do t_pass.",
        (signal) => FX.fetchJSON("/nowcast/pass", q, { method: "GET", signal })
      );

      if (res === null) { statusEl.textContent = "Anulowano."; return; }
      lastResult = res;
      statusEl.textContent = "";
      renderResults(res);
      drawOnMap(res);
    } catch (e) {
      statusEl.innerHTML = `<span class="fx-err" role="alert" style="display:block">Błąd nowcastingu: ${esc(e.message)}</span>`;
    } finally {
      runBtn.disabled = false;
    }
  }
  runBtn.addEventListener("click", executeNowcast);

  function drawOnMap(res) {
    group.clearLayers();
    const pfs = res.projected_fire_state;

    // Draw active perimeter at overpass
    if (pfs.active_perimeter) {
      L.geoJSON(pfs.active_perimeter, {
        style: { color: "#ea580c", weight: 2, fillOpacity: 0.25, dashArray: "4 4" }
      }).bindTooltip(`<b>Przewidywany obrys w chwili przelotu (${res.target_overpass.satellite}):</b><br>Powierzchnia: ${pfs.burned_area_km2} km²`).addTo(group);
    }

    // Draw flaming front
    if (pfs.flaming_front) {
      L.geoJSON(pfs.flaming_front, {
        style: { color: "#dc2626", weight: 4, opacity: 0.95 }
      }).bindTooltip(`🔥 <b>Aktywne czoło ognia w chwili przelotu</b>`).addTo(group);
    }

    group.addTo(map);
  }

  function renderResults(r) {
    const top = r.target_overpass;
    const fp = r.sensor_footprint;
    const pfs = r.projected_fire_state;
    const det = r.sensor_detection_forecast;

    const confColor = det.detection_confidence === "high" ? "#16a34a" : (det.detection_confidence === "moderate" ? "#eab308" : (det.detection_confidence === "unknown" ? "#64748b" : "#dc2626"));
    const cloudText = (top.cloud_cover_pct !== null && top.cloud_cover_pct !== undefined) ? `${top.cloud_cover_pct}%` : "b/d";

    let html = `<div style="margin:12px 0;padding:12px;border-radius:8px;background:rgba(255,255,255,0.04);border-left:4px solid ${confColor};">` +
      `<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;">` +
      `<span style="font-weight:700;font-size:1.0rem;color:${confColor};">${top.satellite} (${top.instrument}) za ${top.delta_hours} h</span>` +
      `<span style="background:${confColor};color:#fff;padding:2px 8px;border-radius:12px;font-size:0.75rem;font-weight:600;">Detekcja: ${det.detection_confidence.toUpperCase()}</span>` +
      `</div>` +
      `<p style="margin:0 0 8px 0;font-size:0.9rem;line-height:1.4;">${esc(r.commander_briefing)}</p>` +
      `<p class="fx-note" style="margin:0;font-size:0.75rem;">${esc(top.visibility_note)}</p>` +
      `</div>`;

    // KPIs
    html += `<div class="fx-kpis">` +
      `<div><b>+${pfs.front_advance_dist_m} m</b><span>postęp czoła (${pfs.front_advance_compass})</span></div>` +
      `<div><b>${fp.along_scan_km * 1000}×${fp.along_track_km * 1000} m</b><span>rozmiar piksela</span></div>` +
      `<div><b>~${det.expected_pixels}</b><span>oczekiwane piksele</span></div>` +
      `<div><b>~${det.expected_total_frp_mw} MW</b><span>szacowane FRP</span></div>` +
      `<div><b>${top.scan_angle_deg}°</b><span>kąt skanu (${top.quality_tier || top.scan_tier})</span></div>` +
      `<div><b>${cloudText}</b><span>chmury (${top.cloud_tier})</span></div>` +
      `</div>`;

    out.innerHTML = html;
  }

  function clearAll() {
    group.clearLayers();
    out.innerHTML = "";
    lastResult = null;
    statusEl.textContent = "Gotowy do wyliczenia nowcastingu.";
  }
  EF.on("reset", clearAll);
})();
