/* Forecast extras, part 6: CO2 emissions and carbon value ticker.
   Live mode: satellite FRP of the selected area -> burned dry matter (Wooster 0.368 kg/MJ) -> CO2 -> value at an ASSUMED price.
   Forecast mode: simulated burned area x typical fuel load x emission factor, following the player scrubber.
   Orders of magnitude only. Not an insured loss, not damage. */
(function () {
  "use strict";
  const EF = window.EquiFire, FX = window.EquiFireFX;
  if (!EF || !FX) return;
  const { esc } = EF;
  const $ = (id) => document.getElementById(id);
  const nf = (n) => Math.round(n).toLocaleString("en-US").replace(/,/g, " ");

  const sec = FX.section({ id: "carbon", title: "CO₂ and value ticker", icon: "factory", order: 42 });
  sec.body.innerHTML = FX.warn("Rough estimate, orders of magnitude only. The value is CO₂ at an ASSUMED price, not an insured loss and not damage.") +
    `<div class="fx-grid2">` +
    `<label class="input-field"><span>Carbon price (EUR / t CO₂)</span><input id="fx-cb-price" type="number" min="1" max="1000" step="5" value="70"></label>` +
    `<label class="input-field"><span>USD per EUR</span><input id="fx-cb-fx" type="number" min="0.5" max="2" step="0.01" value="1.08"></label>` +
    `<label class="input-field"><span>Biome</span><select id="fx-cb-biome"><option value="auto">Auto (coarse map)</option><option value="boreal">Boreal forest</option><option value="temperate">Temperate forest</option>` +
    `<option value="tropical_forest">Tropical forest</option><option value="savanna">Savanna / grassland</option><option value="cropland">Cropland residue</option><option value="peat">Peatland</option></select></label></div>` +
    `<div class="fx-actions"><button type="button" id="fx-cb-live" class="btn-primary" disabled>${FX.ic("activity")}<span>Live: selected area, last 24 h</span></button>` +
    `<button type="button" id="fx-cb-fc" class="btn-download" disabled>${FX.ic("flame")}<span>For this forecast</span></button></div>` +
    `<div id="fx-cb-slot"></div><p id="fx-cb-status" class="fx-note" role="status">Select a fire on the map.</p><div id="fx-cb-out"></div>`;
  const job = FX.job($("fx-cb-slot")), statusEl = $("fx-cb-status"), out = $("fx-cb-out"), bLive = $("fx-cb-live"), bFc = $("fx-cb-fc");
  let tick = null, mode = null, live = null, fc = null, t0 = 0;
  const q = () => ({ price_eur_t: $("fx-cb-price").value, usd_per_eur: $("fx-cb-fx").value, biome: $("fx-cb-biome").value });

  function refresh() { bLive.disabled = !FX.point() || job.busy; bFc.disabled = !EF.sim.frames.length || job.busy; }
  EF.on("point", refresh); EF.on("load", () => { if (mode === "fc") { fc = null; clearInterval(tick); out.innerHTML = ""; statusEl.textContent = "The simulation changed. Press “For this forecast” again."; } refresh(); });
  EF.on("reset", () => { if (mode === "fc") { fc = null; out.innerHTML = ""; } refresh(); });
  sec.onOpen(refresh);

  function cards(a, b, c, d) {
    return `<div class="fx-kpis fx-kpis-big"><div><b id="cb-k1">${a[0]}</b><span>${a[1]}</span></div><div><b id="cb-k2">${b[0]}</b><span>${b[1]}</span></div>` +
      `<div><b id="cb-k3">${c[0]}</b><span>${c[1]}</span></div><div><b id="cb-k4">${d[0]}</b><span>${d[1]}</span></div></div>`;
  }
  function assume(a) {
    return `<details class="fx-details"><summary>Assumptions</summary><ul class="fx-note"><li>${esc(a.biomass_formula)}</li><li>Biome: ${esc(a.biome_label)} (${esc(a.biome_source)}); ${a.co2_emission_factor_g_per_kg} g CO₂ per kg dry matter; typical fuel use ${a.fuel_consumption_t_per_ha} t/ha</li>` +
      `<li>${esc(a.carbon_price)}</li><li>${esc(a.meaning)}</li><li>${esc(a.uncertainty)}</li></ul></details>`;
  }

  async function runLive() {
    const p = FX.point(); if (!p || job.busy) return;
    clearInterval(tick); bLive.disabled = bFc.disabled = true;
    try {
      const d = await job.run("Reading satellite detections…", "Uses the public FIRMS near-real-time feed.", (signal) =>
        FX.fetchJSON("/carbon/live", { lat: p.lat.toFixed(4), lon: p.lon.toFixed(4), radius_km: 30, hours: 24, ...q() }, { signal }));
      if (d === null) { statusEl.textContent = "Cancelled."; return; }
      mode = "live"; live = d; t0 = Date.now(); statusEl.textContent = "";
      if (!d.passes) { out.innerHTML = `<p class="fx-note">${esc(d.note || "No detections in this area and window.")}</p>`; return; }
      const t = d.total, r = d.rate;
      out.innerHTML = `<p class="fx-expo-label">LIVE · last ${d.window_h} h · ${d.detections} detections · ${d.passes} satellite passes</p>` +
        cards([nf(t.forest_ha) + " ha", "forest burned (estimate)"], [nf(t.area_ha) + " ha", "all vegetation burned"], [nf(t.co2_t) + " t", "CO₂ emitted"], ["€" + nf(t.value.eur), "≈ $" + nf(t.value.usd) + " at the assumed price"]) +
        `<p class="fx-note">Now: ${r.frp_mw} MW of fire power → ${nf(r.co2_t_per_h)} t CO₂ per hour (≈ €${nf(r.value_eur_per_h)} per hour). The counter ticks at this rate.</p>` + assume(d.assumptions) +
        `<p class="fx-note">${esc(d.area_note)}</p>`;
      tick = setInterval(() => {
        const s = (Date.now() - t0) / 1000, co2 = t.co2_t + r.co2_kg_per_s * s / 1000, ha = t.area_ha + (r.biomass_kg_per_s * s / 1000) / d.assumptions.fuel_consumption_t_per_ha;
        const k2 = $("cb-k2"), k3 = $("cb-k3"), k4 = $("cb-k4"), k1 = $("cb-k1");
        if (!k3) { clearInterval(tick); return; }
        k2.textContent = nf(ha) + " ha"; if (t.forest_ha) k1.textContent = nf(ha) + " ha";
        k3.textContent = nf(co2) + " t"; k4.textContent = "€" + nf(co2 * t.value.price_eur_t);
      }, 1000);
    } catch (e) { statusEl.innerHTML = `<span class="fx-err" role="alert" style="display:block">Failed: ${esc(e.message)}</span>`; }
    finally { refresh(); }
  }

  function showFrame(i) {
    if (mode !== "fc" || !fc) return;
    const f = fc.timeline[Math.min(i || 0, fc.timeline.length - 1)];
    $("cb-k1") && ($("cb-k1").textContent = nf(f.forest_ha) + " ha"); $("cb-k2") && ($("cb-k2").textContent = nf(f.area_ha) + " ha");
    $("cb-k3") && ($("cb-k3").textContent = nf(f.co2_t) + " t"); $("cb-k4") && ($("cb-k4").textContent = "€" + nf(f.value_eur) + " (≈ $" + nf(f.value_usd) + ")");
  }
  EF.on("frame", showFrame);

  async function runFc() {
    if (!EF.sim.data || !EF.sim.frames.length || job.busy) return;
    clearInterval(tick); bLive.disabled = bFc.disabled = true;
    try {
      const b = { ...EF.sim.data }; delete b.guidance;
      const d = await job.run("Calculating…", "", (signal) => FX.fetchJSON("/carbon/forecast", q(), { method: "POST", body: b, signal }));
      if (d === null) return;
      mode = "fc"; fc = d; statusEl.textContent = "";
      out.innerHTML = `<p class="fx-expo-label">FORECAST · follows the timeline slider · ${esc(d.assumptions.biome_label)}</p>` +
        cards(["", "forest burned (simulated)"], ["", "all vegetation burned"], ["", "CO₂ emitted"], ["", "value at the assumed price"]) + assume(d.assumptions) + `<p class="fx-note">${esc(d.area_note)}</p>`;
      showFrame(EF.sim.idx);
    } catch (e) { statusEl.innerHTML = `<span class="fx-err" role="alert" style="display:block">Failed: ${esc(e.message)}</span>`; }
    finally { refresh(); }
  }
  bLive.addEventListener("click", runLive); bFc.addEventListener("click", runFc);
  refresh();
})();
