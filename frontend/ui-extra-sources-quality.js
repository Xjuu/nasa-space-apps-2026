/* Extra Data Panels: Static Thermal Sources Mask (#13) & Observation Quality Index (#15).
   Integrates into the "Extra data" container created by frontend/ui-extra-data.js via window.EquiFireFX.extraPanel.
   Connects to backend/static_and_quality.py: GET/POST /extra-sq/static-sources, GET /extra-sq/quality-index. */
(function () {
  "use strict";
  const EF = window.EquiFire, FX = window.EquiFireFX;
  if (!EF || !FX || !FX.extraPanel) return;
  const { map, esc } = EF;
  const $ = (id) => document.getElementById(id);

  // --------------------------------------------------------------------------
  // Panel 1: Static Thermal Sources Mask (#13)
  // --------------------------------------------------------------------------
  const pStatic = FX.extraPanel({
    id: "static-sources",
    title: "Static Thermal Sources Mask (#13)",
    note: "Screens against known industrial flaring, metallurgy, and volcanic craters to flag persistent heat sources and avoid false wildfire dispatches.",
    tag: "extra data"
  });

  pStatic.body.innerHTML = `
    <div style="margin:6px 0;">
      <p class="fx-note" style="margin-bottom:8px;">Multi-temporal filter for stationary thermal anomalies (Schroeder et al. 2014, Elvidge et al. 2016). Distinguishes wildland fires from industrial flares (e.g. Basra, Permian, Samotlor), metal smelting, and zinc complexes (e.g. Flin Flon).</p>
      <div class="fx-grid2" style="margin-bottom:8px;">
        <label class="input-field"><span>Screening radius (km)</span><input id="fx-sq-radius" type="number" min="5" max="100" step="5" value="30"></label>
        <button type="button" id="fx-sq-check-btn" class="btn-primary" style="align-self:flex-end;margin-bottom:2px;">🔍 <span>Check surroundings</span></button>
      </div>
      <div id="fx-sq-static-out" class="fx-note" style="min-height:24px;">Click "Check surroundings" to search catalog of persistent thermal sources.</div>
    </div>
  `;

  const radiusInp = $("fx-sq-radius"), checkBtn = $("fx-sq-check-btn"), staticOut = $("fx-sq-static-out");
  const staticGroup = L.layerGroup();

  async function checkStaticSources() {
    checkBtn.disabled = true;
    staticOut.textContent = "Searching catalog of persistent thermal sources…";
    let centerLat = 54.975, centerLon = -101.725;
    const sim = EF.sim && EF.sim.data;
    if (sim && sim.grid && sim.grid.center) {
      centerLat = sim.grid.center[0];
      centerLon = sim.grid.center[1];
    } else if (EF.state && EF.state.selectedPoint) {
      centerLat = EF.state.selectedPoint.lat;
      centerLon = EF.state.selectedPoint.lon;
    }

    try {
      const data = await FX.fetchJSON(`/extra-sq/static-sources?lat=${centerLat}&lon=${centerLon}&radius_km=${radiusInp.value}`);
      renderStaticResults(data);
    } catch (e) {
      staticOut.innerHTML = `<span class="fx-err">Failed checking static sources: ${esc(e.message)}</span>`;
    } finally {
      checkBtn.disabled = false;
    }
  }

  function renderStaticResults(data) {
    staticGroup.clearLayers();
    const ass = data.static_zone_assessment;
    const isInside = ass.in_known_static_buffer;
    const badgeColor = isInside ? "#eab308" : "#16a34a";
    const statusText = isInside
      ? "⚠️ Known static thermal source detected within buffer"
      : "✓ Negative screening: No cataloged industrial sources within buffer";

    let html = `<div style="padding:10px;border-radius:6px;background:rgba(255,255,255,0.03);border-left:4px solid ${badgeColor};margin-top:8px;">` +
      `<div style="font-weight:700;color:${badgeColor};font-size:0.95rem;margin-bottom:4px;">${statusText}</div>`;

    if (ass.nearby_static_sources_count > 0 && ass.closest_source) {
      const c = ass.closest_source;
      html += `<p style="margin:0 0 6px 0;font-size:0.85rem;">Closest source: <b>${esc(c.name)}</b> (${esc(c.type)}) at <b>${c.distance_km} km</b> (site buffer: ${c.site_buffer_km} km).</p>`;

      // Draw nearby sources on map
      ass.all_nearby_sources.forEach((s) => {
        const circle = L.circle([s.center_lat, s.center_lon], {
          radius: s.site_buffer_km * 1000,
          color: "#eab308",
          fillColor: "#eab308",
          fillOpacity: 0.15,
          weight: 2,
          dashArray: "3 3",
        }).bindPopup(`<b>${esc(s.name)}</b><br>Type: ${esc(s.type)}<br>Country: ${esc(s.country)}<br>Buffer: ${s.site_buffer_km} km`);
        staticGroup.addLayer(circle);
      });
      staticGroup.addTo(map);
    } else {
      html += `<p style="margin:0 0 6px 0;font-size:0.85rem;">No cataloged multi-year gas flaring clusters or major smelters within ${radiusInp.value} km.</p>`;
    }

    html += `<p class="fx-note" style="margin:0;font-size:0.75rem;">Auxiliary data (Extra data): Negative screening against reference catalog does not guarantee absence of uncataloged local industrial or agricultural sources.</p></div>`;
    staticOut.innerHTML = html;
  }

  checkBtn.addEventListener("click", checkStaticSources);
  pStatic.onOpen(() => {
    if (staticOut.textContent.includes("Click") || staticOut.textContent.includes("Kliknij")) checkStaticSources();
  });

  // --------------------------------------------------------------------------
  // Panel 2: Observation Quality Index (#15)
  // --------------------------------------------------------------------------
  const pQuality = FX.extraPanel({
    id: "quality-index",
    title: "Observation Quality Index (#15)",
    note: "Multi-factor satellite detection quality indicator (scan geometry, bow-tie distortion, cloud cover, sensor resolution). Not a calibrated confidence interval.",
    tag: "extra data"
  });

  pQuality.body.innerHTML = `
    <div style="margin:6px 0;">
      <p class="fx-note" style="margin-bottom:8px;">Synthetic observation quality index Q in [0, 1] indicates whether a satellite pass possesses favorable geometry and atmospheric transparency to detect low-intensity ignitions.</p>
      <div class="fx-grid2" style="margin-bottom:8px;">
        <label class="input-field"><span>Sensor / Instrument</span><select id="fx-qi-inst"><option value="viirs" selected>VIIRS (375 m nadir)</option><option value="modis">MODIS (1 km nadir)</option></select></label>
        <label class="input-field"><span>Scan angle (° off-nadir)</span><select id="fx-qi-scan"><option value="5">0–10° (Nadir — optimal)</option><option value="20" selected>15–25° (Mid-swath)</option><option value="40">35–45° (Bow-tie expansion)</option><option value="55">50–60° (Swath edge — 3-5x blur)</option></select></label>
      </div>
      <div class="fx-grid2" style="margin-bottom:8px;">
        <label class="input-field"><span>Pass timing</span><select id="fx-qi-day"><option value="0" selected>Night (high thermal contrast, no glint)</option><option value="1">Day (solar background)</option></select></label>
        <label class="input-field"><span>Cloud cover</span><select id="fx-qi-cloud">
          <option value="auto" selected>Auto (from forecast if available)</option>
          <option value="0">Clear sky (0%)</option>
          <option value="25">Scattered clouds (25%)</option>
          <option value="60">Broken clouds (60%)</option>
          <option value="90">Overcast (90%)</option>
          <option value="none">Unspecified / Unknown (no cloud data)</option>
        </select></label>
      </div>
      <div class="fx-actions" style="margin-bottom:8px;">
        <button type="button" id="fx-qi-calc-btn" class="btn-primary" style="margin-top:2px;">⚡ <span>Calculate quality index</span></button>
      </div>
      <div id="fx-qi-out" class="fx-note" style="min-height:24px;">Click "Calculate quality index" to evaluate observation quality.</div>
    </div>
  `;

  const instSel = $("fx-qi-inst"), scanSel = $("fx-qi-scan"), daySel = $("fx-qi-day"), cloudSel = $("fx-qi-cloud");
  const qiBtn = $("fx-qi-calc-btn"), qiOut = $("fx-qi-out");

  async function calculateQualityIndex() {
    qiBtn.disabled = true;
    qiOut.textContent = "Calculating observation quality index…";
    let centerLat = 54.975, centerLon = -101.725;

    const sim = EF.sim && EF.sim.data;
    if (sim && sim.grid && sim.grid.center) {
      centerLat = sim.grid.center[0];
      centerLon = sim.grid.center[1];
    } else if (EF.state && EF.state.selectedPoint) {
      centerLat = EF.state.selectedPoint.lat;
      centerLon = EF.state.selectedPoint.lon;
    }

    const cloudChoice = cloudSel ? cloudSel.value : "auto";
    let cloudParam = "";
    if (cloudChoice === "auto") {
      let foundCloud = null;
      if (sim && sim.weather && typeof sim.weather.cloud_cover_pct === "number") {
        foundCloud = sim.weather.cloud_cover_pct;
      } else if (sim && sim.weather && Array.isArray(sim.weather.hourly_cloud_cover) && sim.weather.hourly_cloud_cover.length > 0) {
        foundCloud = sim.weather.hourly_cloud_cover[0];
      }
      if (foundCloud !== null && !isNaN(foundCloud)) {
        cloudParam = `&cloud_cover_pct=${encodeURIComponent(foundCloud)}`;
      }
    } else if (cloudChoice !== "none") {
      cloudParam = `&cloud_cover_pct=${encodeURIComponent(cloudChoice)}`;
    }

    const qUrl = `/extra-sq/quality-index?lat=${centerLat}&lon=${centerLon}&scan_angle_deg=${scanSel.value}&instrument=${instSel.value}&is_day=${daySel.value === "1"}${cloudParam}`;

    try {
      const data = await FX.fetchJSON(qUrl);
      renderQualityResults(data);
    } catch (e) {
      qiOut.innerHTML = `<span class="fx-err">Failed evaluating quality index: ${esc(e.message)}</span>`;
    } finally {
      qiBtn.disabled = false;
    }
  }

  function renderQualityResults(data) {
    const q = data.observation_quality_index;
    const tier = data.quality_tier;
    const isPl = (document.documentElement.lang === "pl");
    const tierLabel = (isPl && data.quality_tier_pl) ? data.quality_tier_pl : (data.quality_tier_en || tier.toUpperCase());
    const tierDesc = (isPl && data.tier_description_pl) ? data.tier_description_pl : data.tier_description;
    const subs = data.sub_scores;
    const cloudAccounted = Boolean(data.cloud_accounted);

    const tierColor = tier === "optimal" ? "#16a34a" : (tier === "good" ? "#65a30d" : (tier === "degraded" ? "#eab308" : "#dc2626"));

    let cloudDisplay = "Unverified";
    if (cloudAccounted && subs.cloud_clarity_score !== null) {
      cloudDisplay = `${Math.round(subs.cloud_clarity_score * 100)}%`;
    }

    let html = `<div style="padding:10px;border-radius:6px;background:rgba(255,255,255,0.03);border-left:4px solid ${tierColor};margin-top:8px;">` +
      `<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;">` +
      `<span style="font-weight:700;font-size:1.0rem;color:${tierColor};">${Math.round(q * 100)}% — ${esc(tierLabel)}</span>` +
      `<span style="background:${tierColor};color:#fff;padding:2px 8px;border-radius:10px;font-size:0.75rem;font-weight:600;">${tier.toUpperCase()}</span>` +
      `</div>` +
      `<p style="margin:0 0 8px 0;font-size:0.85rem;line-height:1.4;">${esc(tierDesc)}</p>` +
      `<div class="fx-kpis" style="margin-bottom:6px;">` +
      `<div><b>${Math.round(subs.geometry_score * 100)}%</b><span>scan geometry</span></div>` +
      `<div><b>${esc(cloudDisplay)}</b><span>cloud transparency</span></div>` +
      `<div><b>${Math.round(subs.instrument_resolution_weight * 100)}%</b><span>sensor weight</span></div>` +
      `<div><b>${Math.round(subs.illumination_score * 100)}%</b><span>illumination</span></div>` +
      `</div>` +
      `<p class="fx-note" style="margin:0;font-size:0.72rem;">${esc(data.terminology_disclaimer.warning)}</p>` +
      `</div>`;

    qiOut.innerHTML = html;
  }

  qiBtn.addEventListener("click", calculateQualityIndex);
  pQuality.onOpen(() => {
    if (qiOut.textContent.includes("Click") || qiOut.textContent.includes("Kliknij")) calculateQualityIndex();
  });

  EF.on("reset", () => {
    staticGroup.clearLayers();
    staticOut.textContent = "Click \"Check surroundings\" to search catalog of persistent thermal sources.";
    qiOut.textContent = "Click \"Calculate quality index\" to evaluate observation quality.";
  });
})();
