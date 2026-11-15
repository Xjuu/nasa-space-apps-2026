/* Separate research calibration overlay. The issued forecast and its player stay unchanged. */
(function () {
  "use strict";
  const EF = window.EquiFire, FX = window.EquiFireFX;
  if (!EF || !FX) return;
  const { esc, map } = EF;
  const sec = FX.section({ id: "riskdiag", title: "Daily risk calibration", icon: "chart-no-axes-combined", order: 60 });
  const pl = () => document.documentElement.lang === "pl";
  const tr = (a, b) => pl() ? a : b;
  const st = { diagnostics: null, data: null, busy: false, error: "", day: 1, visible: false, controller: null, seq: 0 };
  const layer = L.layerGroup();
  const nf = (value, digits) => Number.isFinite(value) ? Number(value).toFixed(digits) : "—";
  const pct = (value) => nf(value * 100, 2) + "%";
  const $ = (id) => document.getElementById(id);

  function eligibility() {
    const a = st.diagnostics, d = EF.sim.data, g = d?.guidance;
    if (!a?.available) return tr("Brak zapisanej diagnostyki kalibracji.", "Saved calibration diagnostics are unavailable.");
    if (!a.model_matches) return tr("Modele zmieniły się od eksperymentu. Warstwa badawcza jest wyłączona.", "The models changed after this experiment. The research overlay is disabled.");
    if (!g) return tr("Ukończ prognozę archiwalną ML + fizyka, aby sprawdzić warstwę badawczą.", "Finish an archive ML + physics forecast to check the research overlay.");
    if (g.history?.reduced !== false || d.ignition?.mode !== "archive") return tr("Ten kandydat nie obsługuje danych na żywo ani skróconej historii.", "This candidate does not support live data or reduced history.");
    const date = g.query?.date || "";
    if (!date.startsWith(String(a.test_year)) || date <= a.calibration_outcomes_through) return tr("Warstwa dotyczy archiwum 2025 po 31 maja. Wcześniejsze daty uczestniczyły w dopasowaniu.", "The overlay covers the 2025 archive after May 31. Earlier dates contributed to fitting.");
    if (g.model?.blender !== a.source_model || Object.keys(a.fingerprints || {}).some((key) => g.model?.fingerprints?.[key] !== a.fingerprints[key]))
      return tr("Brak zgodnego odcisku modelu. Uruchom ponownie zgodną prognozę archiwalną.", "No matching model fingerprint. Run a compatible archive forecast again.");
    return "";
  }

  function chart(h) {
    const a = h.raw_frozen.reliability, b = h.calibrated.reliability;
    const W = 560, H = 320, x = (v) => 54 + v * 466, y = (v) => 265 - v * 226;
    const line = (rows) => rows.filter((r) => r.weight > 0).map((r) => `${x(r.mean_probability)},${y(r.observed_frequency)}`).join(" ");
    let svg = `<svg class="risk-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(tr("Rzetelność: estymata modelu a częstość obserwacji w próbce", "Reliability: model estimate versus observed frequency in the sample"))}">`;
    for (const t of [0, .25, .5, .75, 1]) svg += `<path d="M${x(t)} 39V265M54 ${y(t)}H520" stroke="var(--border,#d5dfe4)" fill="none"/><text x="${x(t)}" y="282" text-anchor="middle">${Math.round(t * 100)}</text><text x="46" y="${y(t) + 4}" text-anchor="end">${Math.round(t * 100)}</text>`;
    svg += `<path d="M54 265L520 39" stroke="currentColor" stroke-opacity=".45" stroke-dasharray="3 4" fill="none"/>`;
    svg += `<polyline points="${line(a)}" stroke="#0072b2" stroke-width="2.5" stroke-dasharray="7 4" fill="none"/><polyline points="${line(b)}" stroke="#009e73" stroke-width="2.5" fill="none"/>`;
    for (const [rows, color] of [[a, "#0072b2"], [b, "#009e73"]]) {
      for (const r of rows.filter((v) => v.weight > 0)) svg += `<circle cx="${x(r.mean_probability)}" cy="${y(r.observed_frequency)}" r="3.5" fill="${color}"><title>${esc(`${tr("Komórki", "Cells")}: ${r.cells}; ${tr("estymata", "estimate")}: ${pct(r.mean_probability)}; ${tr("obserwacje", "observations")}: ${pct(r.observed_frequency)}`)}</title></circle>`;
    }
    svg += `<text x="287" y="307" text-anchor="middle">${esc(tr("Estymata modelu (%)", "Model estimate (%)"))}</text><text transform="translate(15 155) rotate(-90)" text-anchor="middle">${esc(tr("Częstość obserwacji (%)", "Observed frequency (%)"))}</text></svg>`;
    return svg;
  }

  function render() {
    sec.el.querySelector("summary > span").textContent = tr("Kalibracja dziennego ryzyka", "Daily risk calibration");
    const a = st.diagnostics;
    sec.body.innerHTML = `<p class="fx-note">${esc(tr("Kandydat badawczy, niepromowany do produkcji. Kalibracja dotyczy wykrycia nowych komórek przez satelitę, nie pewności konkretnego pożaru ani obrysu fizycznego.", "Research candidate, not promoted to production. Calibration targets satellite detections of new cells, not confidence in an individual fire or its physical perimeter."))}</p>`;
    if (!a?.available) {
      sec.body.insertAdjacentHTML("beforeend", `<p class="fx-note" role="status">${esc(st.error || tr("Wczytywanie diagnostyki…", "Loading diagnostics…"))}</p><button id="risk-retry" class="tool-btn" type="button">${esc(tr("Wczytaj diagnostykę", "Load diagnostics"))}</button>`);
      $("risk-retry").addEventListener("click", load); return;
    }
    const rows = Object.entries(a.horizons).map(([day, h]) => `<tr><th>${day}</th><td>${nf(h.raw_frozen.brier, 5)} → ${nf(h.calibrated.brier, 5)}</td><td>${pct(h.raw_frozen.ece)} → ${pct(h.calibrated.ece)}</td><td>${nf(h.brier_difference.lower_95, 5)}…${nf(h.brier_difference.upper_95, 5)}</td></tr>`).join("");
    const h = a.horizons[String(st.day)];
    sec.body.insertAdjacentHTML("beforeend", `<div id="risk-result"><p class="fx-note">${esc(tr(`Kalibracja: ${a.calibration_cases} przypadków. Ocena: ${a.test_cases} późniejszych przypadków z 2025, retrospektywnie użyty benchmark. Wszystkie przedziały różnicy Brier obejmują zero; poprawa trafności nie została wykazana.`, `Calibration: ${a.calibration_cases} cases. Evaluation: ${a.test_cases} later 2025 cases on a retrospectively reused benchmark. Every Brier-difference interval includes zero; improved accuracy has not been established.`))}</p>` +
      `<div class="fx-table-wrap"><table class="fx-scen-table"><thead><tr><th>${esc(tr("Dzień", "Day"))}</th><th>Brier: ${esc(tr("przed → po", "raw → calibrated"))}</th><th>ECE: ${esc(tr("przed → po", "raw → calibrated"))}</th><th>${esc(tr("Różnica Brier, 95% CI", "Brier difference, 95% CI"))}</th></tr></thead><tbody>${rows}</tbody></table></div>` +
      `<p class="fx-note">${esc(tr("Brier/ECE: mniej znaczy lepiej. Ujemna różnica Brier sprzyja kalibracji. Wagi wyrównują wkład grup zdarzeń; liczby nie są pewnością pojedynczego incydentu.", "Lower Brier/ECE is better. A negative Brier difference favors calibration. Weights balance event groups; these numbers are not individual-incident confidence."))}</p>` +
      `<label class="input-field"><span>${esc(tr("Dzień horyzontu", "Horizon day"))}</span><select id="risk-day">${[1, 2, 3].map((day) => `<option value="${day}"${day === st.day ? " selected" : ""}>${day}</option>`).join("")}</select></label>` +
      chart(h) + `<p class="fx-note">${esc(tr("Niebieska linia przerywana: wynik surowy. Zielona ciągła: po kalibracji. Szara przekątna: idealna zgodność. Puste przedziały nie są interpolowaną obserwacją.", "Blue dashed: raw. Green solid: calibrated. Grey diagonal: ideal agreement. Empty bins are not interpolated observations."))}</p></div>` +
      `<p id="risk-eligibility" class="fx-note" role="status"></p><div class="fx-actions"><button id="risk-show" type="button" class="tool-btn">${esc(tr("Pokaż warstwę badawczą", "Show research overlay"))}</button><button id="risk-hide" type="button" class="tool-btn">${esc(tr("Ukryj warstwę", "Hide overlay"))}</button></div><p id="risk-status" class="fx-note" role="status"></p><div id="risk-levels"></div>`);
    $("risk-day").addEventListener("change", (event) => { st.day = +event.target.value; render(); draw(); });
    $("risk-show").addEventListener("click", overlay);
    $("risk-hide").addEventListener("click", () => { st.visible = false; layer.clearLayers(); layer.remove(); update(); });
    update();
  }

  function update() {
    if (!$("risk-show")) return;
    $("risk-show").disabled = st.busy || !!eligibility();
    $("risk-hide").disabled = !st.visible;
    $("risk-eligibility").textContent = eligibility() || tr("Zgodny model i data archiwalna. Warstwa jest oddzielnym eksperymentem; nie zmienia prognozy ani animacji.", "Matching archive model and date. This is a separate experiment; it does not change the forecast or animation.");
    $("risk-status").textContent = st.busy ? tr("Przeliczanie zapisanych wyników…", "Transforming the saved scores…") : st.error;
    const row = st.data?.per_day.find((r) => r.day === st.day);
    $("risk-levels").textContent = row ? tr("Progi estymaty modelu, nie przedziały ufności: ", "Model-estimate thresholds, not confidence intervals: ") + row.thresholds.map((r) => `${r.level * 100}%: ${r.cells} ${tr("komórek", "cells")}`).join(" · ") : "";
  }
  function draw() {
    layer.clearLayers();
    if (!st.visible || !st.data) { layer.remove(); update(); return; }
    const features = st.data.features.filter((f) => f.properties.day === st.day);
    L.geoJSON({ type: "FeatureCollection", features }, { style: (f) => ({ color: "#009e73", weight: .3,
      fillColor: "#009e73", fillOpacity: .08 + .5 * f.properties.research_probability }), onEachFeature: (f, node) => {
        node.bindTooltip(esc(tr(`Badawcza estymata dla komórki: ${pct(f.properties.research_probability)}. Nie pewność pożaru.`, `Research cell estimate: ${pct(f.properties.research_probability)}. Not fire confidence.`)));
      } }).addTo(layer);
    L.geoJSON(st.data.contours, { filter: (f) => f.properties.day === st.day, style: (f) => ({ color: "#009e73", weight: 2,
      fillOpacity: 0, dashArray: f.properties.level === .5 ? "8 5" : f.properties.level === .8 ? "2 4" : "" }),
      onEachFeature: (f, node) => node.bindTooltip(esc(tr(`Próg badawczej estymaty: ${f.properties.level * 100}%; nie granica ufności obrysu.`, `Research estimate threshold: ${f.properties.level * 100}%; not a perimeter confidence bound.`))) }).addTo(layer);
    if (EF.state.mode === "date") layer.addTo(map);
    update();
  }
  async function overlay() {
    if (eligibility() || st.busy) return;
    const seq = ++st.seq; st.controller?.abort(); st.controller = new AbortController(); st.busy = true; st.error = ""; update();
    try {
      const d = EF.sim.data;
      const res = await fetch(EF.API + "/risk/calibrate", { method: "POST", headers: { "Content-Type": "application/json" }, signal: st.controller.signal,
        body: JSON.stringify({ guidance: d.guidance, ignition: d.ignition }) });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
      if (seq !== st.seq) return;
      st.data = body; st.visible = true; draw();
    } catch (error) { if (seq === st.seq && error.name !== "AbortError") st.error = error.message; }
    finally { if (seq === st.seq) { st.busy = false; update(); } }
  }
  async function load() {
    try { st.diagnostics = await EF.getJSON("/risk/diagnostics"); st.error = st.diagnostics.available ? "" : tr("Brak zapisanej diagnostyki. Sprawdź instalację artefaktów.", "No saved diagnostics. Check the installed artifacts."); }
    catch (_) { st.error = tr("Diagnostyka niedostępna. Spróbuj ponownie.", "Diagnostics unavailable. Please retry."); }
    render();
  }
  function reset() { st.seq++; st.controller?.abort(); st.busy = false; st.data = null; st.visible = false; st.error = ""; layer.clearLayers(); layer.remove(); update(); }
  ["point", "reset", "load"].forEach((event) => EF.on(event, reset));
  EF.on("frame", (_, frame) => { const day = frame.guidance_day || 1; if (st.visible && st.day !== day) { st.day = day; render(); draw(); } });
  EF.onModeChange((mode) => { if (mode !== "date") layer.remove(); else if (st.visible) layer.addTo(map); });
  document.addEventListener("equifire:langchange", () => { render(); draw(); });
  sec.onOpen(() => { if (!st.diagnostics) load(); else update(); });
  render();
})();
