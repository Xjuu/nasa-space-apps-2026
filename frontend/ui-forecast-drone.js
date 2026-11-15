/* Forecast extras, part 5: drone recon route around the forecast fire (POST /drone/mission with the frames already in the player).
   A planning aid only: not a certified flight plan. Thermal updrafts and turbulence are NOT modelled. */
(function () {
  "use strict";
  const EF = window.EquiFire, FX = window.EquiFireFX;
  if (!EF || !FX) return;
  const { map, esc } = EF;
  const $ = (id) => document.getElementById(id);

  const sec = FX.section({ id: "drone", title: "Drone recon route", icon: "plane", order: 45 });
  sec.body.innerHTML = FX.warn("Planning aid only, not a certified flight plan. Check airspace, rules and the real situation on site.") +
    `<p class="fx-note">Route around the forecast fire at a standoff distance (larger on the downwind side), launch from the upwind side. Hover points aim the camera at the ignition points. Updrafts and turbulence are not modelled. Needs a finished ML + physics run.</p>` +
    `<div class="fx-grid2">` +
    `<label class="input-field"><span>Forecast horizon (h)</span><select id="fx-dr-h"><option value="1">1 h</option><option value="2" selected>2 h</option><option value="3">3 h</option></select></label>` +
    `<label class="input-field"><span>Standoff (m)</span><input id="fx-dr-so" type="number" min="100" max="2000" step="50" value="250"></label>` +
    `<label class="input-field"><span>Altitude AGL (m)</span><input id="fx-dr-alt" type="number" min="30" max="120" step="10" value="100"></label>` +
    `<label class="input-field"><span>Speed (m/s)</span><input id="fx-dr-sp" type="number" min="2" max="20" step="1" value="10"></label>` +
    `<label class="input-field"><span>Max flight (min)</span><input id="fx-dr-mf" type="number" min="5" max="90" step="5" value="25"></label>` +
    `</div>` +
    `<div class="fx-actions"><button type="button" id="fx-dr-prev" class="btn-primary" disabled>${FX.ic("route")}<span>Plan route</span></button></div>` +
    `<div id="fx-dr-slot"></div><p id="fx-dr-status" class="fx-note" role="status">Run a prediction first.</p><div id="fx-dr-out"></div>`;
  const job = FX.job($("fx-dr-slot")), statusEl = $("fx-dr-status"), out = $("fx-dr-out"), btn = $("fx-dr-prev");
  const group = L.layerGroup();
  let last = null;

  function refresh() { const n = EF.sim.frames.length; btn.disabled = !n || job.busy; if (!last && !job.busy) statusEl.textContent = n ? "Ready." : "Run a prediction first."; }
  function clearAll(msg) { job.cancel && job.cancel(); group.clearLayers(); group.remove(); out.innerHTML = ""; last = null; if (msg) statusEl.textContent = msg; refresh(); }
  EF.on("reset", () => clearAll());
  EF.on("load", () => { if (last) clearAll("The simulation changed. Plan the route again."); refresh(); });
  sec.onOpen(refresh);

  function qs(fmt) {
    return new URLSearchParams({ format: fmt, horizon_h: $("fx-dr-h").value, standoff_m: $("fx-dr-so").value, alt_agl_m: $("fx-dr-alt").value,
      speed_ms: $("fx-dr-sp").value, max_flight_min: $("fx-dr-mf").value });
  }
  function body() { const b = { ...EF.sim.data }; delete b.guidance; return b; }

  async function plan() {
    if (!EF.sim.data || !EF.sim.frames.length || job.busy) return;
    btn.disabled = true;
    try {
      const m = await job.run("Planning route…", "Usually a few seconds.", (signal) =>
        FX.fetchJSON("/drone/mission", Object.fromEntries(qs("json")), { method: "POST", body: body(), signal }));
      if (m === null) { statusEl.textContent = "Cancelled."; return; }
      last = m; statusEl.textContent = ""; draw(m); render(m);
    } catch (e) {
      statusEl.innerHTML = `<span class="fx-err" role="alert" style="display:block">Route failed: ${esc(e.message)}</span>`;
    } finally { refresh(); }
  }
  btn.addEventListener("click", plan);

  function draw(m) {
    group.clearLayers();
    const ll = m.waypoints.map((p) => [p.lat, p.lon]);
    L.geoJSON(m.footprint, { style: { color: "#d93025", weight: 1, fillOpacity: 0.12 } }).addTo(group);
    L.polyline(ll, { color: "#f5a30a", weight: 3, dashArray: "6 4" }).addTo(group);
    m.waypoints.forEach((p) => {
      const chk = p.kind === "ignition_check", end = p.kind === "launch" || p.kind === "return";
      L.circleMarker([p.lat, p.lon], { radius: end ? 7 : chk ? 6 : 3, color: end ? "#188038" : chk ? "#d93025" : "#f5a30a", fillOpacity: 0.9, weight: 1 })
        .bindTooltip(`WP${p.n} ${p.kind}: heading ${p.heading_deg}°, ${p.alt_agl_m} m AGL${p.hover_s ? ", hover " + p.hover_s + " s" : ""}, ${p.dist_to_fire_m} m from fire`).addTo(group);
    });
    group.addTo(map);
    try { map.fitBounds(L.latLngBounds(ll).pad(0.2)); } catch (e) { /* ignore */ }
  }

  function render(m) {
    const s = m.stats;
    out.innerHTML = `<div class="fx-kpis"><div><b>${s.waypoints}</b><span>waypoints</span></div><div><b>${s.length_km} km</b><span>route length</span></div>` +
      `<div><b>~${s.est_flight_min} min</b><span>estimated flight</span></div><div><b>${s.ignition_checks}</b><span>ignition checks</span></div></div>` +
      `<p class="fx-note">Launch point: ${s.launch_dist_to_fire_m} m from the forecast fire. Orange line = route, red dots = ignition checks (camera aimed at the ignition point), green = launch and return.</p>` +
      `<div class="fx-actions fx-dl">` + [["gpx", "GPX"], ["kml", "KML"], ["csv", "CSV (Litchi)"], ["geojson", "GeoJSON"]]
        .map(([f, l]) => `<button type="button" class="btn-download" data-fmt="${f}">${FX.ic("download")}<span>${l}</span></button>`).join("") + `</div>` +
      `<ul class="fx-note">` + m.warnings.map((w) => `<li>${esc(w)}</li>`).join("") + `</ul>`;
    out.querySelectorAll("[data-fmt]").forEach((b) => b.addEventListener("click", () => download(b.dataset.fmt, b)));
  }

  async function download(fmt, b) {
    const old = b.querySelector("span").textContent; b.disabled = true;
    try {
      const res = await fetch(`${EF.API}/drone/mission?${qs(fmt)}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body()) });
      if (!res.ok) { let m = res.statusText; try { m = (await res.json()).detail || m; } catch (e) { /* not JSON */ } throw new Error(m); }
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a"); a.href = url; a.download = "firespot_recon." + fmt; document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (e) { statusEl.innerHTML = `<span class="fx-err" role="alert" style="display:block">Download failed: ${esc(e.message)}</span>`; }
    finally { b.disabled = false; b.querySelector("span").textContent = old; }
  }
  refresh();
})();
