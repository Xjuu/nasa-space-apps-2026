/* Monte Carlo Ensemble: Burn frequency & weather perturbation simulator.
   Constructs multi-member physical spread ensembles with perturbed wind and fuel moisture.
   Connects to backend/monte_carlo.py: GET /montecarlo/presets, POST /montecarlo/run. */
(function () {
  "use strict";
  const EF = window.EquiFire, FX = window.EquiFireFX;
  if (!EF || !FX) return;
  const { map, esc } = EF;
  const $ = (id) => document.getElementById(id);

  const sec = FX.section({ id: "montecarlo", title: "Monte Carlo Ensemble (Częstość)", icon: "activity", order: 52 });
  sec.body.innerHTML = FX.warn("Wskaźnik częstości w zespole symulacji fizycznej (ensemble burn frequency). Nie jest to empirycznie skalibrowane prawdopodobieństwo w świecie rzeczywistym.") +
    `<p class="fx-note">Wielokrotny przebieg symulacji fizycznej (Rothermel MTT) z losowymi perturbacjami wiatru, kierunku i wilgotności paliwa. Wyznacza izolinie częstości osiągnięcia komórek przez ogień (20%, 50%, 80%, 95%).</p>` +
    `<div class="fx-grid2">` +
    `<label class="input-field"><span>Wariant zespołu</span><select id="fx-mc-preset"></select></label>` +
    `<label class="input-field"><span>Horyzont (h)</span><select id="fx-mc-hours"><option value="12">12 h</option><option value="24" selected>24 h</option><option value="48">48 h</option><option value="72">72 h</option></select></label>` +
    `<label class="input-field"><span>Liczba przebiegów (N)</span><input id="fx-mc-members" type="number" min="10" max="60" step="5" value="30"></label>` +
    `<label class="input-field"><span>Model paliwa</span><select id="fx-mc-fuel"><option value="auto" selected>Automatyczny (strefowy)</option><option value="forest">Las (FM10)</option><option value="shrub">Zarośla (FM6)</option><option value="savanna">Sawanna (FM2)</option><option value="grass">Trawy (FM1)</option></select></label>` +
    `</div>` +
    `<div class="fx-actions" style="margin:10px 0;">` +
    `<button type="button" id="fx-mc-run-btn" class="btn-primary" style="width:100%;">${FX.ic("activity") || "⚡"} <span>Uruchom zespół Monte Carlo</span></button>` +
    `</div>` +
    `<div id="fx-mc-slot"></div><p id="fx-mc-status" class="fx-note" role="status">Gotowy do uruchomienia zespołu.</p><div id="fx-mc-out"></div>`;

  const job = FX.job($("fx-mc-slot")), statusEl = $("fx-mc-status"), out = $("fx-mc-out");
  const presetSel = $("fx-mc-preset"), hoursSel = $("fx-mc-hours"), membersInput = $("fx-mc-members"), fuelSel = $("fx-mc-fuel");
  const runBtn = $("fx-mc-run-btn");

  const group = L.layerGroup();
  let presetsData = {};
  let lastResult = null;

  async function loadPresets() {
    try {
      const data = await FX.fetchJSON("/montecarlo/presets");
      if (data && data.presets) {
        presetSel.innerHTML = "";
        data.presets.forEach((p) => {
          presetsData[p.id] = p;
          const opt = document.createElement("option");
          opt.value = p.id;
          opt.textContent = `${p.name_pl || p.name}`;
          if (p.id === "standard") opt.selected = true;
          presetSel.appendChild(opt);
        });
      }
    } catch (e) {
      console.warn("Failed to load Monte Carlo presets:", e);
    }
  }

  function onPresetChange() {
    const p = presetsData[presetSel.value];
    if (p) {
      membersInput.value = p.members;
    }
  }
  presetSel.addEventListener("change", onPresetChange);

  async function executeEnsemble() {
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

    const payload = {
      lat: centerLat,
      lon: centerLon,
      hours: Number(hoursSel.value),
      members: Number(membersInput.value),
      preset: presetSel.value,
      fuel: fuelSel.value,
      ignition: sim ? sim.ignition : undefined
    };

    try {
      const res = await job.run(
        `Obliczanie ${payload.members} przebiegów fizycznych…`,
        "Równoległa symulacja MTT z perturbacjami wiatru.",
        (signal) => FX.fetchJSON("/montecarlo/run", {}, { method: "POST", body: payload, signal })
      );

      if (res === null) { statusEl.textContent = "Anulowano."; return; }
      lastResult = res;
      statusEl.textContent = "";
      renderResults(res);
      drawContoursOnMap(res);
    } catch (e) {
      statusEl.innerHTML = `<span class="fx-err" role="alert" style="display:block">Błąd zespołu: ${esc(e.message)}</span>`;
    } finally {
      runBtn.disabled = false;
    }
  }
  runBtn.addEventListener("click", executeEnsemble);

  function drawContoursOnMap(res) {
    group.clearLayers();
    const bands = res.frequency_bands || [];

    // Draw from lowest frequency (outermost) to highest (innermost)
    bands.forEach((b) => {
      if (b.polygon) {
        L.geoJSON(b.polygon, {
          style: {
            color: b.color,
            weight: 2,
            opacity: 0.9,
            fillColor: b.color,
            fillOpacity: 0.15 + (b.threshold_frequency * 0.35)
          }
        }).bindTooltip(
          `<b>${b.label}</b><br>` +
          `Powierzchnia: ${b.area_km2} km² (${b.area_ha} ha)<br>` +
          `Komórki rastra: ${b.cell_count}`
        ).addTo(group);
      }
    });

    group.addTo(map);
  }

  function renderResults(res) {
    const stats = res.ensemble_stats;
    const dist = stats.area_distribution_km2;
    const bands = res.frequency_bands;

    let html = `<div style="margin:12px 0;padding:12px;border-radius:8px;background:rgba(255,255,255,0.04);border-left:4px solid #f97316;">` +
      `<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;">` +
      `<span style="font-weight:700;font-size:1.0rem;color:#f97316;">CZĘSTOŚĆ W ZESPOLE (${stats.members_run} PRZEBIEGÓW)</span>` +
      `<span style="background:#ea580c;color:#fff;padding:2px 8px;border-radius:12px;font-size:0.75rem;font-weight:600;">${stats.runtime_seconds} s</span>` +
      `</div>` +
      `<p style="margin:0 0 8px 0;font-size:0.85rem;line-height:1.4;">${esc(res.wording_and_disclaimer.uncalibrated_notice)}</p>` +
      `</div>`;

    // Distribution KPIs
    html += `<div class="fx-kpis">` +
      `<div><b>${dist.p50_median} km²</b><span>mediana (P50)</span></div>` +
      `<div><b>${dist.p10}–${dist.p90} km²</b><span>przedział P10–P90</span></div>` +
      `<div><b>${dist.min}–${dist.max} km²</b><span>min–max</span></div>` +
      `<div><b>±${dist.std_km2} km²</b><span>odchylenie std</span></div>` +
      `</div>`;

    // Frequency bands table
    html += `<div style="margin-top:12px;"><b style="font-size:0.85rem;">Izolinie częstości w zespole:</b>` +
      `<table class="fx-table" style="width:100%;margin-top:6px;font-size:0.8rem;">` +
      `<thead><tr><th>Próg</th><th>Powierzchnia</th><th>Hektary</th></tr></thead><tbody>`;

    bands.forEach((b) => {
      html += `<tr>` +
        `<td><span style="display:inline-block;width:10px;height:10px;background:${b.color};margin-right:6px;border-radius:2px;"></span>${b.label}</td>` +
        `<td><b>${b.area_km2} km²</b></td>` +
        `<td>${b.area_ha} ha</td>` +
        `</tr>`;
    });

    html += `</tbody></table></div>`;
    out.innerHTML = html;
  }

  function clearAll() {
    group.clearLayers();
    out.innerHTML = "";
    lastResult = null;
    statusEl.textContent = "Gotowy do uruchomienia zespołu.";
  }
  EF.on("reset", clearAll);

  loadPresets();
})();
