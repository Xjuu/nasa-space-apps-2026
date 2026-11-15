/* Extra data panel: independent burned area from EFFIS (Europe / Mediterranean only) vs VIIRS-active cells (GET /extra/effis).
   Auxiliary data, not a forecast result. */
(function () {
  "use strict";
  const EF = window.EquiFire, FX = window.EquiFireFX;
  if (!EF || !FX || !FX.extraPanel) return;
  const { esc } = EF;
  const p = FX.extraPanel({ id: "effis", title: "Independent burned area (EFFIS)", tag: "extra data",
    note: "Burnt-area polygons from the Copernicus EFFIS open service (MODIS-based). Europe and the Mediterranean region only; outside it the panel shows no data. Context only, not ground truth." });
  const yr = new Date().getUTCFullYear();
  p.body.innerHTML = `<div class="fx-grid2"><label class="input-field"><span>Year</span><select id="fx-ef-year">${[...Array(10)].map((_, i) => `<option value="${yr - i}">${yr - i}</option>`).join("")}</select></label></div>` +
    `<div class="fx-actions"><button type="button" id="fx-ef-run" class="btn-primary" disabled>${FX.ic("scale")}<span>Check the selected area (40 km)</span></button></div><p id="fx-ef-status" class="fx-note" role="status">Select a point on the map.</p><div id="fx-ef-out"></div>`;
  const btn = p.body.querySelector("#fx-ef-run"), st = p.body.querySelector("#fx-ef-status"), out = p.body.querySelector("#fx-ef-out");
  const refresh = () => { btn.disabled = !FX.point(); };
  EF.on("point", refresh); p.onOpen(refresh);
  const nf = (n) => Math.round(n).toLocaleString("en-US").replace(/,/g, " ");
  btn.addEventListener("click", async () => {
    const pt = FX.point(); if (!pt) return;
    btn.disabled = true; st.textContent = "Asking EFFIS…";
    try {
      const d = await FX.fetchJSON("/extra/effis", { lat: pt.lat.toFixed(4), lon: pt.lon.toFixed(4), radius_km: 40, year: p.body.querySelector("#fx-ef-year").value });
      st.textContent = "";
      if (!d.n_fires) { out.innerHTML = `<p class="fx-note">${esc(d.message || "No data.")}</p>`; return; }
      const c = d.largest_fire_vs_viirs || {};
      out.innerHTML = `<p class="fx-expo-label">${d.n_fires} EFFIS fire polygon(s), ${nf(d.total_area_ha)} ha in total</p>` +
        `<div class="fx-table-wrap"><table class="fx-scen-table"><thead><tr><th>Fire date</th><th>Area (ha)</th><th>Country</th></tr></thead><tbody>${d.fires.slice(0, 6).map((f) => `<tr><td>${esc((f.fire_date || "").slice(0, 10))}</td><td>${nf(f.area_ha)}</td><td>${esc(f.country || "")}</td></tr>`).join("")}</tbody></table></div>` +
        (c.viirs_active_cells != null ? `<p class="fx-note">Largest fire: EFFIS ${nf(c.effis_area_ha)} ha. VIIRS-active cells over the same dates: ${c.viirs_active_cells} (about ${c.viirs_cell_km2} km² each, upper bound ${nf(c.viirs_cells_area_ha_upper_bound)} ha, ratio ${c.ratio_cells_to_effis}). The cell count is coarse and can include other fires nearby.</p>` : "") +
        `<p class="fx-note">${esc(d.note)}</p>`;
    } catch (e) { st.innerHTML = `<span class="fx-err" role="alert">Failed: ${esc(e.message)}</span>`; }
    finally { refresh(); }
  });
})();
