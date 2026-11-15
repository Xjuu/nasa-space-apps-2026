/* What-If Firebreak Sandbox: Tactical Barrier & Containment Simulation.
   Evaluates user-drawn firebreaks, bulldozer lines, and aerial retardant drops.
   Connects with backend/firebreak.py endpoints: GET /firebreak/presets, POST /firebreak/evaluate. */
(function () {
  "use strict";
  const EF = window.EquiFire, FX = window.EquiFireFX;
  if (!EF || !FX) return;
  const { map, esc } = EF;
  const $ = (id) => document.getElementById(id);

  const sec = FX.section({ id: "firebreak", title: "What-If Firebreak Sandbox", icon: "shield", order: 50 });
  sec.body.innerHTML = FX.warn("Tactical screening aid based on Rothermel/Albini/Wilson models. Not a certified incident action plan. Spotting and terrain channeling can compromise lines.") +
    `<p class="fx-note">Draw a tactical containment line (dozer trench, mineral line, retardant drop, backburn) on the map or place an automated downwind shield to simulate if the barrier stops the firefront or gets breached.</p>` +
    `<div class="fx-grid2">` +
    `<label class="input-field"><span>Barrier Preset</span><select id="fx-fb-preset"></select></label>` +
    `<label class="input-field"><span>Width (m)</span><input id="fx-fb-width" type="number" min="1" max="200" step="1" value="12"></label>` +
    `<label class="input-field"><span>Fuel Model</span><select id="fx-fb-fuel"><option value="forest" selected>Forest (FM10)</option><option value="shrub">Shrub / Chaparral (FM6)</option><option value="savanna">Savanna / Woodland (FM2)</option><option value="grass">Grass / Rangeland (FM1)</option></select></label>` +
    `<label class="input-field"><span>Efficacy (0.1–1.0)</span><input id="fx-fb-eff" type="number" min="0.1" max="1.0" step="0.05" value="0.95"></label>` +
    `</div>` +
    `<div class="fx-actions" style="display:flex;gap:8px;flex-wrap:wrap;margin:10px 0;">` +
    `<button type="button" id="fx-fb-draw-btn" class="btn-secondary" style="flex:1;">${FX.ic("edit-3") || "✏️"} <span>Draw on map</span></button>` +
    `<button type="button" id="fx-fb-auto-btn" class="btn-secondary" style="flex:1;">${FX.ic("compass") || "🛡️"} <span>Auto-shield downwind</span></button>` +
    `<button type="button" id="fx-fb-eval-btn" class="btn-primary" style="flex:1;" disabled>${FX.ic("play") || "▶"} <span>Evaluate line</span></button>` +
    `<button type="button" id="fx-fb-clear-btn" class="btn-secondary" style="width:auto;">${FX.ic("trash-2") || "✖"}</button>` +
    `</div>` +
    `<div id="fx-fb-slot"></div><p id="fx-fb-status" class="fx-note" role="status">Select or draw a firebreak line to begin.</p><div id="fx-fb-out"></div>`;

  const job = FX.job($("fx-fb-slot")), statusEl = $("fx-fb-status"), out = $("fx-fb-out");
  const presetSel = $("fx-fb-preset"), widthInput = $("fx-fb-width"), fuelSel = $("fx-fb-fuel"), effInput = $("fx-fb-eff");
  const drawBtn = $("fx-fb-draw-btn"), autoBtn = $("fx-fb-auto-btn"), evalBtn = $("fx-fb-eval-btn"), clearBtn = $("fx-fb-clear-btn");

  const group = L.layerGroup();
  let presetsData = {};
  let currentPoints = []; // [[lat, lon], ...]
  let isDrawing = false;
  let drawPolyline = null;
  let lastResult = null;

  // Load presets from backend
  async function loadPresets() {
    try {
      const data = await FX.fetchJSON("/firebreak/presets");
      if (data && data.presets) {
        presetSel.innerHTML = "";
        data.presets.forEach((p) => {
          presetsData[p.id] = p;
          const opt = document.createElement("option");
          opt.value = p.id;
          opt.textContent = `${p.name} (${p.width_m} m)`;
          if (p.id === "dozer_double") opt.selected = true;
          presetSel.appendChild(opt);
        });
        onPresetChange();
      }
    } catch (e) {
      console.warn("Failed to load firebreak presets:", e);
    }
  }

  function onPresetChange() {
    const p = presetsData[presetSel.value];
    if (p) {
      widthInput.value = p.width_m;
      effInput.value = p.efficacy;
    }
  }
  presetSel.addEventListener("change", onPresetChange);

  // Map Click Handler for Drawing
  function onMapClick(e) {
    if (!isDrawing) return;
    const pt = [e.latlng.lat, e.latlng.lng];
    currentPoints.push(pt);
    updateDraftLine();
    evalBtn.disabled = currentPoints.length < 2;
    statusEl.textContent = `Drawing: ${currentPoints.length} points placed. Click more or 'Evaluate' when finished.`;
  }

  function updateDraftLine() {
    if (drawPolyline) drawPolyline.remove();
    if (currentPoints.length < 1) return;
    const p = presetsData[presetSel.value] || { color: "#f97316" };
    drawPolyline = L.polyline(currentPoints, {
      color: p.color || "#f97316",
      weight: Math.max(3, Math.min(10, Math.round(Number(widthInput.value) / 3))),
      opacity: 0.9,
      dashArray: "4 4"
    }).addTo(group);
    group.addTo(map);
  }

  function toggleDrawing() {
    isDrawing = !isDrawing;
    if (isDrawing) {
      drawBtn.classList.add("btn-primary");
      drawBtn.classList.remove("btn-secondary");
      drawBtn.innerHTML = `${FX.ic("check") || "✓"} <span>Finish drawing</span>`;
      statusEl.textContent = "Click on map to place firebreak vertices. Click 'Finish' when done.";
      map.getContainer().style.cursor = "crosshair";
    } else {
      drawBtn.classList.remove("btn-primary");
      drawBtn.classList.add("btn-secondary");
      drawBtn.innerHTML = `${FX.ic("edit-3") || "✏️"} <span>Draw on map</span>`;
      map.getContainer().style.cursor = "";
      if (currentPoints.length >= 2) {
        evalBtn.disabled = false;
        statusEl.textContent = `Line defined (${currentPoints.length} points). Click 'Evaluate line' to simulate.`;
      } else {
        statusEl.textContent = "Draw at least 2 points to evaluate.";
      }
    }
  }
  drawBtn.addEventListener("click", toggleDrawing);
  map.on("click", onMapClick);

  // Auto-shield: computes an arc or perpendicular line downwind of ignition or firefront
  function autoShield() {
    const sim = EF.sim && EF.sim.data;
    let centerLat = 54.975, centerLon = -101.725;
    let windDir = 270.0; // from west

    if (sim) {
      if (sim.grid && sim.grid.center) {
        centerLat = sim.grid.center[0];
        centerLon = sim.grid.center[1];
      }
      if (sim.weather && sim.weather.wind_dir_deg != null) {
        windDir = sim.weather.wind_dir_deg;
      }
    } else if (EF.state && EF.state.selectedPoint) {
      centerLat = EF.state.selectedPoint.lat;
      centerLon = EF.state.selectedPoint.lon;
    }

    // Spread direction is wind direction + 180 deg
    const spreadRad = ((windDir + 180.0) % 360.0) * (Math.PI / 180.0);
    // Stand off distance ~2.5 km in spread direction
    const distM = 2500.0;
    const mLat = 110574.0, mLon = 111320.0 * Math.cos(centerLat * Math.PI / 180.0);

    const shieldCenterLat = centerLat + (Math.cos(spreadRad) * distM) / mLat;
    const shieldCenterLon = centerLon + (Math.sin(spreadRad) * distM) / mLon;

    // Normal to spread direction: perpendicular barrier line of length 4 km
    const normRad = spreadRad + Math.PI / 2.0;
    const halfLen = 2000.0;

    const p1 = [
      shieldCenterLat - (Math.cos(normRad) * halfLen) / mLat,
      shieldCenterLon - (Math.sin(normRad) * halfLen) / mLon
    ];
    const mid = [shieldCenterLat, shieldCenterLon];
    const p2 = [
      shieldCenterLat + (Math.cos(normRad) * halfLen) / mLat,
      shieldCenterLon + (Math.sin(normRad) * halfLen) / mLon
    ];

    currentPoints = [p1, mid, p2];
    updateDraftLine();
    evalBtn.disabled = false;
    statusEl.textContent = `Auto-shield barrier placed 2.5 km downwind (${windDir}° wind). Ready to evaluate.`;
    try { map.fitBounds(L.latLngBounds(currentPoints).pad(0.3)); } catch (e) {}
  }
  autoBtn.addEventListener("click", autoShield);

  function clearAll() {
    isDrawing = false;
    map.getContainer().style.cursor = "";
    drawBtn.classList.remove("btn-primary");
    drawBtn.classList.add("btn-secondary");
    drawBtn.innerHTML = `${FX.ic("edit-3") || "✏️"} <span>Draw on map</span>`;
    currentPoints = [];
    if (drawPolyline) drawPolyline.remove();
    drawPolyline = null;
    group.clearLayers();
    out.innerHTML = "";
    lastResult = null;
    evalBtn.disabled = true;
    statusEl.textContent = "Select preset or draw line on map.";
  }
  clearBtn.addEventListener("click", clearAll);
  EF.on("reset", clearAll);

  // Evaluation trigger
  async function evaluate() {
    if (currentPoints.length < 2 || job.busy) return;
    evalBtn.disabled = true;

    // Convert currentPoints [lat, lon] to GeoJSON coordinates [lon, lat]
    const coords = currentPoints.map((pt) => [pt[1], pt[0]]);
    const barrierGeoJSON = {
      type: "Feature",
      geometry: { type: "LineString", coordinates: coords },
      properties: {}
    };

    const payload = {
      barrier: barrierGeoJSON,
      preset: presetSel.value,
      width_m: Number(widthInput.value),
      fuel: fuelSel.value,
      efficacy: Number(effInput.value),
      simulation: EF.sim && EF.sim.data ? EF.sim.data : undefined
    };

    try {
      const res = await job.run("Evaluating firebreak containment…", "Analyzing flame length, spotting distance and line holding.", (signal) =>
        FX.fetchJSON("/firebreak/evaluate", {}, { method: "POST", body: payload, signal }));

      if (res === null) { statusEl.textContent = "Cancelled."; return; }
      lastResult = res;
      statusEl.textContent = "";
      renderResults(res);
      drawResultsOnMap(res);
    } catch (e) {
      statusEl.innerHTML = `<span class="fx-err" role="alert" style="display:block">Evaluation failed: ${esc(e.message)}</span>`;
    } finally {
      evalBtn.disabled = currentPoints.length < 2;
    }
  }
  evalBtn.addEventListener("click", evaluate);

  function drawResultsOnMap(res) {
    group.clearLayers();
    const segs = res.segments || [];

    // Draw individual segments color-coded by hold/breached
    segs.forEach((seg) => {
      const p1Idx = seg.segment_index;
      if (p1Idx < currentPoints.length - 1) {
        const segCoords = [currentPoints[p1Idx], currentPoints[p1Idx + 1]];
        const isHold = seg.status === "holding";
        const color = isHold ? "#16a34a" : "#dc2626";
        const weight = Math.max(4, Math.min(12, Math.round(res.barrier_width_m / 2.5)));

        const score = Math.round((seg.heuristic_holding_score != null ? seg.heuristic_holding_score : seg.hold_probability) * 100);
        const pl = L.polyline(segCoords, {
          color: color,
          weight: weight,
          opacity: 0.95,
          dashArray: isHold ? null : "6 4"
        }).bindTooltip(
          `<b>Segment #${seg.segment_index + 1}: ${isHold ? "HOLDING" : "BREACH RISK"}</b><br>` +
          `Heuristic Score: ${score}/100<br>` +
          `Attack Angle: ${seg.attack_angle_deg}°<br>` +
          `Critical Width: ${seg.crit_width_m} m (Actual: ${res.barrier_width_m} m)`
        );
        pl.addTo(group);
      }
    });

    // Add endpoint and breach markers
    segs.forEach((seg) => {
      if (seg.status === "breached" && seg.midpoint) {
        L.circleMarker([seg.midpoint[1], seg.midpoint[0]], {
          radius: 6,
          color: "#b91c1c",
          fillColor: "#ef4444",
          fillOpacity: 0.9,
          weight: 2
        }).bindTooltip(`⚠️ <b>Vulnerable Point:</b> Ember/flame breach likely.`).addTo(group);
      }
    });

    group.addTo(map);
  }

  function renderResults(r) {
    const isHold = r.status === "holding";
    const score = Math.round((r.heuristic_holding_score != null ? r.heuristic_holding_score : r.hold_probability) * 100);
    const badgeColor = isHold ? "#16a34a" : "#dc2626";

    let html = `<div style="margin:12px 0;padding:12px;border-radius:8px;background:rgba(255,255,255,0.04);border-left:4px solid ${badgeColor};">` +
      `<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;">` +
      `<span style="font-weight:700;font-size:1.05rem;color:${badgeColor};">${isHold ? "CONTAINMENT PROJECTED TO HOLD" : "HIGH BREACH HAZARD"}</span>` +
      `<span style="background:${badgeColor};color:#fff;padding:2px 8px;border-radius:12px;font-size:0.8rem;font-weight:600;">Heuristic Score: ${score}/100</span>` +
      `</div>` +
      `<p style="margin:0 0 10px 0;font-size:0.9rem;line-height:1.4;">${esc(r.tactical_summary)}</p>` +
      `<p class="fx-note" style="margin:0;font-size:0.75rem;opacity:0.8;">Wskaźnik heurystyczny (niekalibrowany na rzeczywistych akcjach gaśniczych). Szacunek ocalonego obszaru i CO₂ liczony względem nieskrępowanej symulacji (Wooster 0,368 kg/MJ).</p>` +
      `</div>`;

    // KPIs
    html += `<div class="fx-kpis">` +
      `<div><b>${r.barrier_length_m} m</b><span>barrier length</span></div>` +
      `<div><b>${r.flame_length_m} m</b><span>flame length</span></div>` +
      `<div><b>${r.critical_width_m} m</b><span>critical width</span></div>` +
      `<div><b>${r.spotting.max_distance_m} m</b><span>max spotting</span></div>` +
      `<div><b>${r.impact.area_saved_ha} ha</b><span>area protected</span></div>` +
      `<div><b>${r.impact.co2_prevented_tons} t</b><span>CO₂ prevented</span></div>` +
      `</div>`;

    // Tactical suggestions
    if (r.spotting.risk_category === "high" || r.spotting.risk_category === "extreme") {
      html += `<p class="fx-note" style="color:#f59e0b;">🔥 <b>Spotting Advisory:</b> Ember throw (${r.spotting.max_distance_m} m) exceeds barrier width (${r.barrier_width_m} m). Consider aerial retardant drops or widening line to >= ${r.recommended_width_m} m.</p>`;
    }

    out.innerHTML = html;
  }

  // Initial load
  loadPresets();
})();
