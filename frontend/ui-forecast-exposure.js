/* Forecast extras, part 4: estimated exposure over time (POST /exposure with the frames already in the player).
   Everything here is ESTIMATED EXPOSURE: an asset inside the simulated burned footprint. Not damage, not loss.
   No monetary values are ever shown. Population is a separate card. Assets follow the player scrubber. */
(function () {
  "use strict";
  const EF = window.EquiFire, FX = window.EquiFireFX;
  if (!EF || !FX) return;
  const { map, esc } = EF;
  const $ = (id) => document.getElementById(id);
  const A = FX.ASSET;

  const sec = FX.section({ id: "expo", title: "Exposure over time", icon: "shield-alert", order: 40 });
  sec.body.innerHTML = `<p class="fx-expo-label">ESTIMATED EXPOSURE: not damage, not loss</p>` +
    `<p class="fx-note">Assets from OpenStreetMap inside the simulated burned area. Needs a finished ML + physics run.</p>` +
    `<div class="fx-actions"><button type="button" id="fx-expo-run" class="btn-primary" disabled>${FX.ic("shield-alert")}<span>Estimate exposure for this run</span></button></div>` +
    `<div id="fx-expo-slot"></div><p id="fx-expo-status" class="fx-note" role="status">Run a prediction first.</p><div id="fx-expo-body"></div>`;
  const slot = $("fx-expo-slot"), statusEl = $("fx-expo-status"), bodyEl = $("fx-expo-body"), runBtn = $("fx-expo-run");
  const job = FX.job(slot);
  const st = { data: null, recs: [], group: L.layerGroup(), show: true, seq: 0, showAll: false, ign: null };

  function refresh() {
    const n = EF.sim.frames.length;
    runBtn.disabled = !n || job.busy;
    if (!st.data && !job.busy && !statusEl.dataset.keep) statusEl.textContent = n ? "Ready. First request for an area can take about 2 min." : "Run a prediction first.";
  }
  function clearAll(msg) {
    job.cancel(); st.seq++;
    st.data = null; st.recs = []; st.group.clearLayers(); st.group.remove();
    bodyEl.innerHTML = ""; delete statusEl.dataset.keep;
    if (msg) { statusEl.textContent = msg; statusEl.dataset.keep = "1"; }
    refresh();
  }
  EF.on("reset", () => clearAll());
  EF.on("load", () => { if (st.data) clearAll("The simulation changed. Estimate exposure again for the new run."); refresh(); });
  EF.on("frame", (i) => { if (st.data) update(i); });
  FX.onModeChange((mode) => { if (mode !== "date") st.group.remove(); refresh(); });
  sec.onOpen(refresh);

  // ------------------------------------------------------------------ run
  async function run() {
    const d = EF.sim.data;
    if (!d || !EF.sim.frames.length || job.busy) return;
    const seq = ++st.seq;
    delete statusEl.dataset.keep;
    runBtn.disabled = true;
    const body = { ...d }; delete body.guidance;   // the daily ML guidance is not needed and is large
    try {
      const r = await job.run("Estimating exposure…", "Querying OpenStreetMap. First request for an area takes 1-2 min; later ones are cached.",
        (signal) => FX.fetchJSON("/exposure", { top_n: 25, buildings: "true", population: "true" }, { method: "POST", body, signal }));
      if (seq !== st.seq) return;
      if (r === null) { statusEl.textContent = "Cancelled. The server may still finish and cache it."; statusEl.dataset.keep = "1"; return; }
      st.data = r; st.showAll = false;
      statusEl.textContent = "";
      render(r);
    } catch (e) {
      if (seq !== st.seq) return;
      statusEl.innerHTML = `<span class="fx-err" role="alert" style="display:block">Exposure estimate failed: ${esc(e.message)}</span>`; statusEl.dataset.keep = "1";
    } finally { if (seq === st.seq) refresh(); }
  }
  runBtn.addEventListener("click", run);

  // ------------------------------------------------------------------ map layers
  const OPEN = { power_site: 1 };
  function mkIcon(cls) {
    return L.divIcon({ className: "fx-marker", html: FX.assetShape(cls, cls === "hospital" ? 20 : 16), iconSize: [20, 20], iconAnchor: [10, 10] });
  }
  function buildLayers(r) {
    st.group.clearLayers(); st.recs = [];
    (r.assets_geojson && r.assets_geojson.features || []).forEach((f) => {
      const p = f.properties || {}, cls = p.class, a = A[cls];
      if (!a || !f.geometry) return;
      const tip = `${a.label}: ${p.name || "unnamed"}${p.arrival_h != null ? ` · reached at +${FX.hours(p.arrival_h).replace(" h", "")} h (estimated)` : ""}`;
      let layer, kind;
      if (f.geometry.type === "Point") {
        const [lon, lat] = f.geometry.coordinates;
        if (cls === "building_cell") {
          layer = L.circleMarker([lat, lon], { radius: Math.max(4, Math.min(18, 2.5 + Math.sqrt(p.buildings || 1) * 0.9)), color: "#fff", weight: 1.5, fillColor: a.c, fillOpacity: 0.8 });
          layer.bindTooltip(`About ${p.buildings} buildings in a ~500 m cell (OSM, estimated exposure)`);
          kind = "circle";
        } else {
          layer = L.marker([lat, lon], { icon: mkIcon(cls), keyboard: false, riseOnHover: true });
          layer.bindTooltip(tip); kind = "marker";
        }
      } else {
        const lineLike = cls === "road" || cls === "rail" || cls === "power_line";
        layer = L.geoJSON(f, { style: lineLike ? { color: a.c, weight: cls === "road" ? 4 : 3, dashArray: cls === "road" ? "" : a.dash, lineCap: cls === "power_line" ? "round" : "butt", opacity: 1 }
          : { color: a.c, weight: 2.5, fill: true, fillColor: a.c, fillOpacity: 0.1, opacity: 1 } });
        layer.bindTooltip(tip); kind = "path";
      }
      layer.addTo(st.group);
      st.recs.push({ layer, kind, ff: p.first_frame == null ? 1e9 : p.first_frame, cls });
    });
    // ignition (estimated)
    const ig = (EF.sim.data && EF.sim.data.ignition) || r.ignition || {};
    let ll = null;
    if (ig.lat != null) ll = [+ig.lat, +ig.lon];
    else if ((r.ignition && r.ignition.points || []).length) {
      const pts = r.ignition.points; ll = [pts.reduce((s, q) => s + q[1], 0) / pts.length, pts.reduce((s, q) => s + q[0], 0) / pts.length];
    }
    if (ll) {
      L.marker(ll, { icon: L.divIcon({ className: "fx-marker", iconSize: [20, 20], iconAnchor: [10, 10],
        html: `<svg width="20" height="20" viewBox="-10 -10 20 20" aria-hidden="true"><path d="M-6 -6L6 6M6 -6L-6 6" stroke="#fff" stroke-width="6" stroke-linecap="round"/><path d="M-6 -6L6 6M6 -6L-6 6" stroke="#B2182B" stroke-width="3" stroke-linecap="round"/></svg>` }),
        keyboard: false, zIndexOffset: 800 }).bindTooltip("Ignition (ESTIMATE): satellite-based, location approximate", { permanent: false }).addTo(st.group);
    }
    if (st.show && EF.state.mode === "date") st.group.addTo(map);
  }
  function styleRecs(idx) {
    st.recs.forEach((r) => {
      const on = r.ff <= idx;
      if (r.kind === "marker") {
        r.layer.setOpacity(on ? 1 : 0.28); r.layer.setZIndexOffset(on ? 400 : 0);
        const el = r.layer.getElement(); if (el) el.classList.toggle("fx-reached", on);
      } else if (r.kind === "circle") r.layer.setStyle({ fillOpacity: on ? 0.85 : 0.18, opacity: on ? 1 : 0.3 });
      else r.layer.setStyle({ opacity: on ? 1 : 0.28, fillOpacity: on ? 0.22 : 0.04, weight: on ? (r.cls === "road" ? 5 : 3.5) : (r.cls === "road" ? 2.5 : 2) });
    });
  }

  // ------------------------------------------------------------------ rendering
  const COUNTERS = [
    ["hospital", "hospital", "Hospitals"], ["school", "school", "Schools"], ["fire_station", "fire_station", "Fire stations"],
    ["power_site", "power_site", "Power sites"], ["settlement", "settlement", "Settlements"], ["residential_area", "residential_area", "Residential areas"],
    ["road", "road_km", "Roads (km)", true], ["rail", "rail_km", "Railways (km)", true], ["power_line", "power_line_km", "Power lines (km)", true],
    ["building", "buildings", "Buildings (OSM)"],
  ];
  function render(r) {
    let h = `<p class="fx-expo-label">${esc(r.label || "ESTIMATED EXPOSURE: an asset inside the simulated burned footprint. Not damage, not loss.")}</p>`;
    const cov = r.coverage || {};
    if (cov.complete === false) {
      const errs = [];
      const osm = cov.osm || {};
      Object.keys(osm).forEach((k) => { if (osm[k] && osm[k].ok === false) errs.push(`${k}: ${(osm[k].errors || ["unavailable"])[0]}`); });
      h += FX.warn(`<b>Partial coverage.</b> Some OpenStreetMap requests failed (${esc(errs.join("; ") || "Overpass unavailable")}). Counts are lower bounds. Try again later.`);
    }
    if (cov.bbox_capped_at_km) h += FX.warn(`Asset search capped at ${esc(cov.bbox_capped_at_km)} km; farther assets are not counted.`);
    h += `<div class="fx-h" id="fx-expo-when">Estimated exposure</div><div class="fx-ctrs" id="fx-ctrs"></div>`;
    h += `<div class="fx-row"><label class="check"><input id="fx-expo-show" type="checkbox"${st.show ? " checked" : ""}> Show assets on the map</label></div>`;
    h += `<div class="fx-legend" aria-label="Asset legend">` + ["hospital", "school", "fire_station", "power_site", "settlement", "residential_area", "road", "rail", "power_line", "building_cell"].map((k) => `<span>${FX.assetShape(k, 16)}${esc(A[k].label)}</span>`).join("") +
      `<span><svg width="16" height="16" viewBox="-8 -8 16 16" aria-hidden="true"><path d="M-5 -5L5 5M5 -5L-5 5" stroke="#B2182B" stroke-width="2.6" stroke-linecap="round"/></svg>Ignition (estimate)</span></div>`;
    h += `<p class="fx-note">Bright = reached at the current timeline step; faded = not yet. Building cell size grows with count.</p>`;
    // arrivals
    h += `<div class="fx-h">Estimated arrival times</div><ul class="fx-arrivals" id="fx-arrivals"></ul><div class="fx-actions" id="fx-arr-more"></div>`;
    // population card (separate)
    h += `<div class="fx-pop" id="fx-pop"></div>`;
    h += `<p class="fx-warn">${FX.ic("triangle-alert")}<span>Industrial heat (steelworks, flares, power plants) can appear as fire detections. Confirm detections on industrial sites.</span></p>`;
    h += `<details class="fx-details"><summary>Limits and sources</summary><p class="fx-note">${esc(r.disclaimer || "")}</p><p class="fx-note">${esc(r.insured_loss_note || "This tool does not estimate monetary or insured loss.")}</p>` +
      `<p class="fx-note">Assets from OpenStreetMap contributors (ODbL), queried ${esc(String((cov.osm_timestamp || "")).replace("T", " ").replace("Z", " UTC"))}${cov.seconds ? `, ${FX.num(cov.seconds, 0)} s` : ""}. Footprint ${FX.num((r.footprint_km2 || {}).central_final, 1)} km² (weather scenarios ${FX.num(((r.footprint_km2 || {}).envelope || {}).low, 1)} to ${FX.num(((r.footprint_km2 || {}).envelope || {}).high, 1)} km²; not a confidence interval).</p></details>`;
    bodyEl.innerHTML = h;
    buildLayers(r);
    buildArrivals(r);
    $("fx-expo-show").addEventListener("change", (e) => { st.show = e.target.checked; if (st.show && EF.state.mode === "date") st.group.addTo(map); else st.group.remove(); });
    update(EF.sim.idx);
    EF.hydrateIcons(bodyEl);
  }

  function arrivalRows(r) {
    return (r.arrivals || []).map((a) => ({ ...a })).concat((r.scenario_only_assets || []).map((a) => ({ ...a, scenarioOnly: true })));
  }
  function rowHTML(a) {
    const cls = a.class, meta = A[cls] || { label: cls };
    const name = a.name && !/^\(unnamed/.test(a.name) ? a.name : `${meta.label} (unnamed)`;
    const extra = a.exposed_length_km != null ? `${FX.num(a.exposed_length_km, 1)} km exposed` : a.count > 1 ? `${a.count} areas` : "";
    const when = a.scenarioOnly ? `scenario only: ${esc(a.scenario || "high weather scenario")}` : `${esc(meta.label)}${a.type && a.type !== cls ? " · " + esc(a.type) : ""}${extra ? " · " + esc(extra) : ""} · ${FX.utc(Date.parse(a.time_utc))}`;
    return `<li class="${a.scenarioOnly ? "scenario-only" : ""}" data-ff="${a.first_frame == null ? "" : a.first_frame}"><button type="button" data-lat="${a.lat}" data-lon="${a.lon}" data-ff="${a.first_frame == null ? "" : a.first_frame}">` +
      `<span class="fx-eta">${a.scenarioOnly ? "n/a" : "+" + FX.hours(a.arrival_h).replace(" h", "") + " h"}</span><span class="fx-asset-icon">${FX.assetShape(cls, 16)}</span>` +
      `<span class="fx-nm">${esc(name)}<small>${when}</small></span><span class="fx-rtag">reached</span></button></li>`;
  }
  function buildArrivals(r) {
    const rows = arrivalRows(r), lim = st.showAll ? rows.length : 25;
    $("fx-arrivals").innerHTML = rows.slice(0, lim).map(rowHTML).join("") || `<li><button type="button" disabled>No mapped assets are reached in this run (OpenStreetMap may be incomplete here).</button></li>`;
    $("fx-arr-more").innerHTML = rows.length > 25 ? `<button type="button" class="tool-btn small-btn" id="fx-arr-toggle"><span>${st.showAll ? "Show fewer" : `Show all ${rows.length}`}</span></button>` : "";
    const tg = $("fx-arr-toggle"); if (tg) tg.addEventListener("click", () => { st.showAll = !st.showAll; buildArrivals(r); update(EF.sim.idx); });
    $("fx-arrivals").querySelectorAll("button[data-lat]").forEach((b) => b.addEventListener("click", () => {
      if (b.dataset.ff !== "") { EF.sim.stop(); EF.sim.show(+b.dataset.ff); }
      map.panTo([+b.dataset.lat, +b.dataset.lon]);
    }));
  }
  function update(idx) {
    const r = st.data; if (!r) return;
    const pf = r.per_frame || [], i = Math.max(0, Math.min(pf.length - 1, idx || 0)), f = pf[i] || {};
    const fr = EF.sim.frames[idx] || {};
    $("fx-expo-when").textContent = `Estimated exposure at T+${FX.hours(f.t_hours != null ? f.t_hours : 0).replace(" h", "")} h${fr.t ? " · " + FX.utc(fr.t) : ""}`;
    const sum = r.classes || {};
    $("fx-ctrs").innerHTML = COUNTERS.filter(([c]) => c !== "building" || sum.building).map(([c, key, lab, km]) => {
      const info = sum[c] || {}, v = f[key];
      const mapped = info.mapped_in_bbox;
      const sr = info.scenario_range;
      const sub = (mapped != null ? `of ${mapped} mapped` : "") + (sr && sr.low != null && sr.high != null ? `${mapped != null ? " · " : ""}${sr.low}–${sr.high} at end (weather scenarios)` : "");
      return `<div class="fx-ctr" role="group" aria-label="Estimated exposure: ${esc(lab)}">${FX.assetShape(c === "building" ? "building_cell" : c, 16)}<div><b>${v == null ? "n/a" : km ? FX.num(v, 1) : Math.round(v).toLocaleString("en")}</b><span>${esc(lab)}</span>${sub ? `<small>${esc(sub)}</small>` : ""}</div></div>`;
    }).join("");
    $("fx-arrivals").querySelectorAll("li[data-ff]").forEach((li) => li.classList.toggle("reached", li.dataset.ff !== "" && !li.classList.contains("scenario-only") && +li.dataset.ff <= idx));
    renderPop(r, i);
    styleRecs(idx);
  }
  function renderPop(r, i) {
    const p = r.population || {}, f = (r.per_frame || [])[i] || {};
    let h = `<h4>ESTIMATED POPULATION EXPOSURE</h4>`;
    const hr = p.hrsl, bp = p.buildings_proxy;
    if (hr) {
      const sr = hr.scenario_range || {};
      h += `<span class="fx-big">${f.people_hrsl == null ? "n/a" : Math.round(f.people_hrsl).toLocaleString("en")}</span><span class="fx-note">people inside the simulated footprint at this step, ${esc(hr.source || "HRSL")} (${esc(hr.vintage || "static")}).${sr.low != null ? ` Scenario range at end: ${Math.round(sr.low).toLocaleString("en")} to ${Math.round(sr.high).toLocaleString("en")}.` : ""}</span>`;
    } else h += `<p class="fx-note">Gridded population (HRSL) unavailable here: ${esc(p.hrsl_unavailable || "no data for this area")}.</p>`;
    if (bp) {
      h += `<span class="fx-big" style="font-size:18px">${f.people_proxy == null ? "n/a" : Math.round(f.people_proxy).toLocaleString("en")}</span><span class="fx-note">rough proxy: mapped OSM buildings x ${esc(bp.persons_per_building_assumed)} persons each; range at end ${Math.round(bp.low).toLocaleString("en")} to ${Math.round(bp.high).toLocaleString("en")}.</span>`;
    } else if (!hr) h += `<p class="fx-note">No building-based proxy either (building data unavailable).</p>`;
    h += `<p class="fx-note">${esc(p.note || "People inside the simulated footprint, not casualties or evacuees.")} Static, no time-of-day model.</p>`;
    $("fx-pop").innerHTML = h;
  }
  refresh();
})();
