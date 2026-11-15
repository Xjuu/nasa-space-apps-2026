/* Forecast extras, part 8: sun-satellite illumination geometry (sun-glint / hot-spot risk) at the selected fire.
   Angles only. No radiometric BRDF correction is applied and no fixed percentage is claimed. */
(function () {
  "use strict";
  const EF = window.EquiFire, FX = window.EquiFireFX;
  if (!EF || !FX) return;
  const { esc } = EF;
  const $ = (id) => document.getElementById(id);

  const sec = FX.section({ id: "illum", title: "Sun–satellite geometry", icon: "sun", order: 14 });
  sec.body.innerHTML = `<p class="fx-note">Daytime fire detection uses a band that contains reflected sunlight. When the satellite looks along the mirror direction of the sun (small sun-glint angle) false alarms are more likely; small backscatter is the hot-spot geometry of land reflectance. We compute the angles for the next passes. We do not apply a radiometric BRDF correction.</p>` +
    `<div class="fx-actions"><button type="button" id="fx-il-run" class="btn-primary" disabled>${FX.ic("sun")}<span>Compute for the selected fire</span></button></div>` +
    `<div id="fx-il-slot"></div><p id="fx-il-status" class="fx-note" role="status">Select a fire on the map.</p><div id="fx-il-out"></div>`;
  const job = FX.job($("fx-il-slot")), statusEl = $("fx-il-status"), out = $("fx-il-out"), btn = $("fx-il-run");
  const refresh = () => { btn.disabled = !FX.point() || job.busy; };
  EF.on("point", refresh); sec.onOpen(refresh);

  btn.addEventListener("click", async () => {
    const p = FX.point(); if (!p || job.busy) return;
    btn.disabled = true;
    try {
      const d = await job.run("Computing angles…", "A few seconds.", (signal) => FX.fetchJSON("/illumination", { lat: p.lat.toFixed(4), lon: p.lon.toFixed(4), hours: 24 }, { signal }));
      if (d === null) { statusEl.textContent = "Cancelled."; return; }
      statusEl.textContent = "";
      const rows = d.passes.map((r) => `<tr><td>${esc(r.satellite)}</td><td>${(r.time_utc || "").slice(11, 16)} UTC</td><td>${r.solar_zenith_deg}°</td><td>${r.view_zenith_deg}°</td>` +
        (r.day ? `<td>${r.sun_glint_angle_deg}°</td><td>${r.backscatter_angle_deg}°</td><td><b>${esc(r.glint_risk)}</b>${r.hotspot_geometry ? " · hot-spot geometry" : ""}</td>` : `<td colspan="3" class="fx-dim">night: no reflected sunlight</td>`) + `</tr>`).join("");
      out.innerHTML = `<div class="fx-table-wrap"><table class="fx-scen-table"><thead><tr><th>Satellite</th><th>Time</th><th>Sun zenith</th><th>View zenith</th><th>Sun-glint angle</th><th>Backscatter</th><th>Glint risk</th></tr></thead><tbody>${rows || `<tr><td colspan="7">No passes in the next 24 h.</td></tr>`}</tbody></table></div>` +
        `<p class="fx-note">${esc(d.what_this_is)}</p><p class="fx-note">${esc(d.what_this_is_not)}</p>`;
    } catch (e) { statusEl.innerHTML = `<span class="fx-err" role="alert" style="display:block">Failed: ${esc(e.message)}</span>`; }
    finally { refresh(); }
  });
  refresh();
})();
