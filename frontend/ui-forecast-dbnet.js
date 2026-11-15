/* Forecast extras, part 7: NASA Direct Broadcast network (geometry only).
   Shows, from orbits (SGP4 + TLE), which upcoming satellite passes over the selected fire a Direct Broadcast station could receive live,
   a countdown to the next observation, and the station links on the 3D globe. FireSpot has no X-band receiver and does not process L0/L1/L2. */
(function () {
  "use strict";
  const EF = window.EquiFire, FX = window.EquiFireFX;
  if (!EF || !FX) return;
  const { esc } = EF;
  const $ = (id) => document.getElementById(id);
  const hm = (iso) => (iso || "").slice(11, 16) + " UTC";

  const sec = FX.section({ id: "dbnet", title: "Direct Broadcast race", icon: "satellite-dish", order: 12 });
  sec.body.innerHTML = `<p class="fx-note">Terra, Aqua, Suomi NPP and NOAA-20/21 transmit MODIS/VIIRS data live in X-band. A station with the satellite above its horizon can receive a pass within minutes, while standard near-real-time data arrive within about 3 hours of observation (FIRMS). This is orbit geometry only: FireSpot has no receiver and does not process L0/L1/L2.</p>` +
    `<div class="fx-actions"><button type="button" id="fx-db-run" class="btn-primary" disabled>${FX.ic("timer")}<span>Compute for the selected fire</span></button>` +
    `<label class="fx-switch"><input type="checkbox" id="fx-db-globe"><span>Show stations and links on the 3D globe</span></label></div>` +
    `<div id="fx-db-slot"></div><p id="fx-db-status" class="fx-note" role="status">Select a fire on the map.</p><div id="fx-db-out"></div>`;
  const job = FX.job($("fx-db-slot")), statusEl = $("fx-db-status"), out = $("fx-db-out"), btn = $("fx-db-run");
  let timer = null, globeTimer = null, nextObs = null;

  function refresh() { btn.disabled = !FX.point() || job.busy; }
  EF.on("point", () => { refresh(); });
  sec.onOpen(refresh);

  function countdown() {
    const el = $("fx-db-cd"); if (!el || !nextObs) return;
    const s = Math.max(0, Math.round((nextObs - Date.now()) / 1000));
    el.textContent = `${String(Math.floor(s / 3600)).padStart(2, "0")}:${String(Math.floor(s / 60) % 60).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  }

  async function run() {
    const p = FX.point(); if (!p || job.busy) return;
    btn.disabled = true; clearInterval(timer);
    try {
      const d = await job.run("Computing orbits…", "A few seconds.", (signal) =>
        FX.fetchJSON("/dbnet/race", { lat: p.lat.toFixed(4), lon: p.lon.toFixed(4), hours: 24 }, { signal }));
      if (d === null) { statusEl.textContent = "Cancelled."; return; }
      statusEl.textContent = "";
      const first = d.passes[0];
      nextObs = first ? Date.parse(first.observation_utc) : null;
      const rows = d.passes.map((r) => `<tr><td>${esc(r.satellite)} (${esc(r.instrument)})</td><td>${hm(r.observation_utc)}<br><span class="fx-dim">in ${Math.round(r.minutes_until)} min</span></td>` +
        `<td>${r.direct_broadcast ? `<b>Yes</b>: ${esc(r.direct_broadcast.station)}` : `<span class="fx-dim">No station in view</span>`}</td>` +
        `<td>${r.next_station_contact ? `${esc(r.next_station_contact.station)}<br><span class="fx-dim">${r.next_station_contact.minutes_after_observation} min after</span>` : "-"}</td></tr>`).join("");
      out.innerHTML = (first ? `<p class="fx-expo-label">NEXT OBSERVATION IN <span id="fx-db-cd" style="font-variant-numeric:tabular-nums">--:--:--</span> · ${esc(first.satellite)}</p>` : "") +
        `<div class="fx-table-wrap"><table class="fx-scen-table"><thead><tr><th>Satellite</th><th>Observes the fire</th><th>Live reception possible?</th><th>Next station contact</th></tr></thead><tbody>${rows || `<tr><td colspan="4">No passes in the next 24 h.</td></tr>`}</tbody></table></div>` +
        `<p class="fx-note">${esc(d.firms_nrt_note)}</p><ul class="fx-note">` + d.limits.map((l) => `<li>${esc(l)}</li>`).join("") + `</ul>`;
      countdown(); timer = setInterval(countdown, 1000);
    } catch (e) { statusEl.innerHTML = `<span class="fx-err" role="alert" style="display:block">Failed: ${esc(e.message)}</span>`; }
    finally { refresh(); }
  }
  btn.addEventListener("click", run);

  // ---- globe overlay: stations (labels) and live line-of-sight links (arcs); refreshed every 30 s while switched on
  async function drawGlobe() {
    const g = EF.globe, on = $("fx-db-globe").checked;
    if (!g) { if (on) statusEl.textContent = "Open the 3D globe view first, then switch this on."; return; }
    if (!on) { g.labelsData([]); g.arcsData([]); return; }
    try {
      const d = await FX.fetchJSON("/dbnet/live", {});
      g.labelsData(d.stations.map((s) => ({ lat: s.lat, lng: s.lon, text: s.name })))
        .labelLat("lat").labelLng("lng").labelText("text").labelSize(0.9).labelDotRadius(0.45).labelColor(() => "#ffd54f").labelAltitude(0.01).labelResolution(2);
      g.arcsData(d.links.map((l) => ({ startLat: l.sat_lat, startLng: l.sat_lon, startAlt: l.sat_alt_km / 6371, endLat: l.station_lat, endLng: l.station_lon, endAlt: 0, label: `${l.satellite} ↔ ${l.station}: elevation ${l.elevation_deg}°` })))
        .arcStartLat("startLat").arcStartLng("startLng").arcStartAltitude("startAlt").arcEndLat("endLat").arcEndLng("endLng").arcEndAltitude("endAlt")
        .arcColor(() => ["#ffd54f", "#ff6b35"]).arcStroke(0.6).arcDashLength(0.4).arcDashGap(0.2).arcDashAnimateTime(1500).arcLabel("label");
      statusEl.textContent = d.links.length ? `${d.links.length} live line-of-sight link(s) now: ` + d.links.map((l) => `${l.satellite} ↔ ${l.station}`).join(", ") : "No satellite is in view of a station right now.";
    } catch (e) { statusEl.textContent = "Globe overlay failed: " + e.message; }
  }
  $("fx-db-globe").addEventListener("change", () => { clearInterval(globeTimer); drawGlobe(); if ($("fx-db-globe").checked) globeTimer = setInterval(drawGlobe, 30000); });
  refresh();
})();
