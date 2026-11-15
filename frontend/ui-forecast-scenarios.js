/* Forecast extras, part 3: weather scenario comparison (GET /scenarios).
   Every scenario except the baseline edits the forecast weather by a fixed amount. They are sensitivity runs,
   not forecasts, not probabilities and not confidence intervals. */
(function () {
  "use strict";
  const EF = window.EquiFire, FX = window.EquiFireFX;
  if (!EF || !FX) return;
  const { map, esc } = EF;
  const $ = (id) => document.getElementById(id);

  const sec = FX.section({ id: "scen", title: "Weather scenarios", icon: "wind", order: 30 });
  sec.body.innerHTML = `<p class="fx-fixed" id="fx-scen-badge0">${FX.ic("triangle-alert")}<span>Hypothetical weather — not the issued forecast</span></p>` +
    `<p class="fx-note">Baseline against fixed wind, rain and humidity edits. Not probabilities or confidence intervals.</p>` +
    `<label class="input-field"><span>Horizon</span><select id="fx-scen-hours" name="fx-scen-hours"></select></label>` +
    `<div class="fx-actions"><button type="button" id="fx-scen-run" class="btn-primary" disabled>${FX.ic("git-compare")}<span>Compare weather scenarios</span></button></div>` +
    `<div id="fx-scen-slot"></div><p id="fx-scen-status" class="fx-note" role="status"></p><div id="fx-scen-body"></div>`;
  const slot = $("fx-scen-slot"), bodyEl = $("fx-scen-body"), statusEl = $("fx-scen-status"), runBtn = $("fx-scen-run"), hoursSel = $("fx-scen-hours");
  const job = FX.job(slot);
  let touched = false;   // once the user picks a horizon it is kept; otherwise the best default follows the player / next pass
  hoursSel.addEventListener("change", () => { touched = true; });
  const st = { data: null, seq: 0, layers: {}, group: L.layerGroup(), visible: {}, rose: null, key: null };

  // ------------------------------------------------------------------ names and styles
  function label(s) {
    const n = s.name || "", m = (re) => n.match(re);
    if (n === "baseline") return "Baseline (issued weather)";
    let x;
    if ((x = m(/^wind_x([\d.]+)$/))) return `Wind × ${x[1]}`;
    if ((x = m(/^wind_rot([+-][\d.]+)$/))) return `Wind rotated ${x[1].replace("-", "−")}°`;
    if ((x = m(/^rain_([\d.]+)mm$/))) return `Rain ${x[1]} mm`;
    if ((x = m(/^dry_rh-([\d.]+)$/))) return `Drier air (RH −${x[1]} points)`;
    if ((x = m(/^wet_rh\+([\d.]+)$/))) return `Wetter air (RH +${x[1]} points)`;
    return n;
  }
  const STYLES = [
    { c: "#1d2b34", dash: "", w: 3, m: "circle" },
    { c: "#D55E00", dash: "", w: 2.2, m: "square" },
    { c: "#56B4E9", dash: "7 4", w: 2.2, m: "triangle" },
    { c: "#009E73", dash: "2 3", w: 2.6, m: "diamond" },
    { c: "#CC79A7", dash: "9 3 2 3", w: 2.2, m: "cross" },
    { c: "#0072B2", dash: "1 3", w: 2.8, m: "circle" },
    { c: "#E69F00", dash: "12 4", w: 2.2, m: "square" },
    { c: "#7F7F7F", dash: "4 4", w: 2.2, m: "triangle" },
  ];
  const style = (i) => STYLES[i % STYLES.length];
  function marker(m, x, y, c, r) {
    r = r || 4;
    return {
      circle: `<circle cx="${x}" cy="${y}" r="${r}" fill="#fff" stroke="${c}" stroke-width="2"/>`,
      square: `<rect x="${x - r}" y="${y - r}" width="${r * 2}" height="${r * 2}" fill="${c}"/>`,
      triangle: `<path d="M${x} ${y - r - 1}L${x + r + 1} ${y + r}H${x - r - 1}Z" fill="${c}"/>`,
      diamond: `<path d="M${x} ${y - r - 1}L${x + r + 1} ${y}L${x} ${y + r + 1}L${x - r - 1} ${y}Z" fill="${c}"/>`,
      cross: `<path d="M${x - r} ${y - r}L${x + r} ${y + r}M${x + r} ${y - r}L${x - r} ${y + r}" stroke="${c}" stroke-width="2.4"/>`,
    }[m];
  }
  const lineSample = (i) => { const s = style(i); return `<svg width="34" height="14" viewBox="0 0 34 14" aria-hidden="true" focusable="false"><line x1="1" y1="7" x2="33" y2="7" stroke="${s.c}" stroke-width="${s.w}" stroke-dasharray="${s.dash}"/>${marker(s.m, 17, 7, s.c, 3.5)}</svg>`; };

  // ------------------------------------------------------------------ horizon options
  function refreshOptions() {
    const p = FX.point(), run = FX.lastRun(), no = FX.nextobs;
    const prev = hoursSel.value;
    const o = [];
    if (no && no.next_usable_observation && no.simulate_until) o.push(["until", `Until next usable pass (${no.simulate_until.hours} h)`]);
    if (run) o.push([String(Math.min(72, run.hours)), `Same as the player (${run.hours} h)`]);
    [6, 12, 24, 48, 72].forEach((h) => { if (!o.some((x) => x[0] === String(h))) o.push([String(h), `${h} hours`]); });
    hoursSel.innerHTML = o.map((x) => `<option value="${x[0]}">${esc(x[1])}</option>`).join("");
    hoursSel.value = touched && o.some((x) => x[0] === prev) ? prev : o[0][0];
    const archive = $("sim-ignition") && $("sim-ignition").value === "archive";
    runBtn.disabled = !p || archive || job.busy;
    if (!p) statusEl.textContent = "Select a fire on the map first.";
    else if (archive) statusEl.textContent = "Scenarios need live ignition. Set Ignition to Nearest live fire.";
    else if (!st.data && !job.busy && !statusEl.dataset.keep) statusEl.textContent = "";
  }
  EF.on("point", () => { clearResults(); refreshOptions(); });
  EF.on("load", refreshOptions);
  EF.on("reset", () => { clearResults(); refreshOptions(); });
  document.addEventListener("fx-nextobs", refreshOptions);
  sec.onOpen(refreshOptions);
  FX.onModeChange((mode) => {
    if (mode !== "date") st.group.remove();
    else if (st.data) st.group.addTo(map);
    if (mode === "date") refreshOptions();
  });
  $("sim-ignition") && $("sim-ignition").addEventListener("change", refreshOptions);

  function clearResults() {
    job.cancel(); st.seq++;
    st.data = null; st.visible = {}; st.group.clearLayers(); st.group.remove(); st.layers = {};
    bodyEl.innerHTML = ""; statusEl.textContent = ""; delete statusEl.dataset.keep;
    const b0 = $("fx-scen-badge0"); if (b0) b0.hidden = false;
  }

  // ------------------------------------------------------------------ run
  async function run() {
    const p = FX.point(); if (!p) return;
    clearResults();
    const seq = st.seq;
    const q = { lat: p.lat.toFixed(4), lon: p.lon.toFixed(4), ignition: "live", calibration: EF.options.calibration || "off", perimeters: "true" };
    if (hoursSel.value === "until") q.until_next_observation = "true";
    const hv = hoursSel.value === "until" ? (FX.nextobs && FX.nextobs.simulate_until ? FX.nextobs.simulate_until.hours : 24) : +hoursSel.value;
    q.hours = String(Math.min(72, Math.max(1, hv)));
    const startVal = $("sim-start") && $("sim-start").value;
    if (startVal) q.start = startVal.slice(0, 16);
    runBtn.disabled = true;
    try {
      const d = await job.run("Running weather scenarios…", `${q.hours} h, calibration ${q.calibration}. First run for a new area can take up to 3 min.`,
        (signal) => FX.fetchJSON("/scenarios", q, { signal }));
      if (seq !== st.seq) return;
      if (d === null) { statusEl.textContent = "Cancelled."; statusEl.dataset.keep = "1"; return; }
      st.data = d; render(d);
    } catch (e) {
      if (seq !== st.seq) return;
      statusEl.innerHTML = `<span class="fx-err" role="alert" style="display:block">Scenario comparison failed: ${esc(e.message)}</span>`; statusEl.dataset.keep = "1";
    } finally { if (seq === st.seq) refreshOptions(); }
  }
  runBtn.addEventListener("click", run);

  // ------------------------------------------------------------------ chart
  function niceMax(v) { const p = Math.pow(10, Math.floor(Math.log10(v || 1))); const f = v / p; return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p; }
  function chartSVG(list, W) {
    const H = 210, L0 = 48, R0 = 14, T0 = 12, B0 = 40;
    const tmax = Math.max(1, ...list.map((s) => Math.max(...(s.timeline || []).map((t) => t.t_hours))));
    const ymaxRaw = Math.max(0.01, ...list.map((s) => Math.max(...(s.timeline || []).map((t) => t.burned_km2))));
    const ymax = niceMax(ymaxRaw * 1.05);
    const x = (t) => L0 + (t / tmax) * (W - L0 - R0), y = (v) => T0 + (1 - v / ymax) * (H - T0 - B0);
    let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Simulated burned area over time for each weather scenario">`;
    for (let i = 0; i <= 4; i++) {
      const v = (ymax * i) / 4;
      s += `<line x1="${L0}" x2="${W - R0}" y1="${y(v)}" y2="${y(v)}" stroke="#dce3e6"/><text x="${L0 - 6}" y="${y(v) + 4}" text-anchor="end" style="font-size:12px;fill:#526570">${+v.toFixed(v < 10 ? 2 : 0)}</text>`;
    }
    const step = tmax <= 8 ? 1 : tmax <= 24 ? 4 : tmax <= 48 ? 8 : 12;
    for (let t = 0; t <= tmax + 1e-6; t += step) s += `<line x1="${x(t)}" x2="${x(t)}" y1="${H - B0}" y2="${H - B0 + 4}" stroke="#526570"/><text x="${x(t)}" y="${H - B0 + 16}" text-anchor="middle" style="font-size:12px;fill:#526570">+${t}</text>`;
    s += `<text x="${(L0 + W - R0) / 2}" y="${H - 3}" text-anchor="middle" style="font-size:12px;fill:#344650">hours from start</text>`;
    s += `<text transform="translate(11 ${(T0 + H - B0) / 2}) rotate(-90)" text-anchor="middle" style="font-size:12px;fill:#344650">burned area (km²)</text>`;
    s += `<line x1="${L0}" x2="${L0}" y1="${T0}" y2="${H - B0}" stroke="#526570"/><line x1="${L0}" x2="${W - R0}" y1="${H - B0}" y2="${H - B0}" stroke="#526570"/>`;
    list.slice().reverse().forEach((sc) => {
      const i = list.indexOf(sc), stl = style(i), tl = sc.timeline || [];
      if (!tl.length) return;
      s += `<polyline fill="none" stroke="${stl.c}" stroke-width="${stl.w}" stroke-dasharray="${stl.dash}" stroke-linejoin="round" points="${tl.map((t) => `${x(t.t_hours).toFixed(1)},${y(t.burned_km2).toFixed(1)}`).join(" ")}"><title>${esc(label(sc))}</title></polyline>`;
      const last = tl[tl.length - 1];
      s += `<g><title>${esc(label(sc))}: ${FX.num(last.burned_km2, 2)} km² at +${last.t_hours} h</title>${marker(stl.m, x(last.t_hours), y(last.burned_km2), stl.c, 3.6)}</g>`;
    });
    return s + `</svg>`;
  }

  // ------------------------------------------------------------------ rose (sector reach)
  const SECT = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
  function roseSVG(base, sc, idx, maxKm) {
    const C = 105, R = 78, stl = style(idx);
    const sc2 = maxKm > 0 ? R / maxKm : 0;
    const wedge = (k, km) => {
      const a0 = ((k * 45 - 22.5 - 90) * Math.PI) / 180, a1 = ((k * 45 + 22.5 - 90) * Math.PI) / 180, r = km * sc2;
      return `M${C} ${C}L${(C + r * Math.cos(a0)).toFixed(1)} ${(C + r * Math.sin(a0)).toFixed(1)}A${r.toFixed(1)} ${r.toFixed(1)} 0 0 1 ${(C + r * Math.cos(a1)).toFixed(1)} ${(C + r * Math.sin(a1)).toFixed(1)}Z`;
    };
    let s = `<svg viewBox="0 0 210 210" role="img" aria-label="Reach of new burn by compass direction for ${esc(label(sc))} against the baseline">`;
    [0.5, 1].forEach((f) => { s += `<circle cx="${C}" cy="${C}" r="${R * f}" fill="none" stroke="#dce3e6"/>`; });
    for (let k = 0; k < 8; k++) { const a = ((k * 45 - 90) * Math.PI) / 180; s += `<line x1="${C}" y1="${C}" x2="${C + R * Math.cos(a)}" y2="${C + R * Math.sin(a)}" stroke="#eef1f2"/>`; }
    SECT.forEach((n, k) => {
      const a = ((k * 45 - 90) * Math.PI) / 180;
      s += `<text x="${C + (R + 14) * Math.cos(a)}" y="${C + (R + 14) * Math.sin(a) + 4}" text-anchor="middle" style="font-size:12px;font-weight:600;fill:#344650">${n}</text>`;
    });
    SECT.forEach((n, k) => { const b = (base.sector_reach_km || {})[n] || 0; if (b > 0) s += `<path d="${wedge(k, b)}" fill="none" stroke="#6b7780" stroke-width="1.6" stroke-dasharray="4 3"/>`; });
    SECT.forEach((n, k) => { const v = (sc.sector_reach_km || {})[n] || 0; if (v > 0) s += `<path d="${wedge(k, v)}" fill="${stl.c}" fill-opacity=".32" stroke="${stl.c}" stroke-width="2" stroke-dasharray="${stl.dash}"/>`; });
    s += `<circle cx="${C}" cy="${C}" r="2.5" fill="#1d2b34"/>`;
    s += `<text x="${C}" y="${C + R + 26}" text-anchor="middle" style="font-size:11px;fill:#526570">outer ring ${FX.num(maxKm, 1)} km</text>`;
    return s + `</svg>`;
  }

  // ------------------------------------------------------------------ render
  function render(d) {
    const list = d.scenarios || [];
    const base = list.find((s) => s.name === "baseline") || list[0];
    let h = "";
    h += `<p class="fx-fixed">${FX.ic("triangle-alert")}<span>Hypothetical weather — not the issued forecast</span></p>`;
    if (d.baseline_no_spread) {
      const note = (d.notes || []).find((n) => /^WARNING/i.test(n)) || "The baseline simulation shows no spread beyond the initial footprint.";
      h += FX.warn(`<b>Baseline shows no spread beyond the initial footprint.</b> ${esc(note.replace(/^WARNING:\s*/i, ""))}`);
    }
    const q = d.query || {};
    h += `<p class="fx-note">${esc(q.hours)} h from ${esc(String(q.start_utc || "").replace("T", " ").replace("Z", " UTC"))}${d.simulate_until ? `, ending at the next usable pass (${esc(d.simulate_until.satellite || "satellite")}, ${FX.utc(Date.parse(d.simulate_until.until_utc))})` : ""}. Calibration ${d.calibration && d.calibration.applied ? `on (multiplier ${FX.num(d.calibration.m, 2)})` : "off"}. Weather: ${esc(d.weather_mode || "gridded")}.</p>`;
    // table
    h += `<div class="fx-table-wrap"><table class="fx-scen-table"><thead><tr><th>Scenario (map)</th><th class="num">Burned km²</th><th class="num">Growth km²</th><th class="num">Extra vs baseline</th></tr></thead><tbody>`;
    list.forEach((s, i) => {
      const v = s.vs_baseline, isBase = s === base;
      const extra = isBase ? "reference" : v ? (v.extra_pct != null ? `${v.extra_pct > 0 ? "+" : ""}${FX.num(v.extra_pct, 0)}%` : v.extra_km2 ? `${v.extra_km2 > 0 ? "+" : ""}${FX.num(v.extra_km2, 2)} km²` : "no change") : "n/a";
      h += `<tr class="fx-scen-main"><td class="fx-name"><label>${s.perimeter ? `<input type="checkbox" data-sc="${i}" aria-label="Show ${esc(label(s))} perimeter on the map">` : ""}${lineSample(i)}<span>${esc(label(s))}</span></label></td>` +
        `<td class="num">${FX.num(s.burned_km2, 2)}</td><td class="num">${FX.num(s.growth_km2, 2)}</td><td class="num">${esc(extra)}</td></tr>` +
        `<tr class="fx-expl"><td colspan="4">${esc(s.explanation || s.weather_change || "")}</td></tr>`;
    });
    h += `</tbody></table></div>`;
    h += `<p class="fx-note">Growth = burned area minus initial footprint. Extra is shown only when the baseline grows.</p>`;
    h += `<div class="fx-h">Burned area over time</div><div class="fx-scen-chart" id="fx-scen-chart"></div>`;
    h += `<p class="fx-note">Overlapping lines: that edit did not change the spread.</p>`;
    // rose
    const maxKm = Math.max(0, ...list.map((s) => Math.max(0, ...Object.values(s.sector_reach_km || {}))));
    h += `<div class="fx-h">Reach by compass direction</div>`;
    if (maxKm <= 0) h += `<p class="fx-empty">No spread beyond the initial footprint in any direction.</p>`;
    else {
      const def = (d.ranking_by_growth || []).find((n) => n !== "baseline") || (list[1] || base).name;
      h += `<div class="fx-rose-row"><div id="fx-rose"></div><div class="fx-rose-side"><label class="input-field"><span>Scenario in the rose</span><select id="fx-rose-sel" name="fx-rose-sel">${list.filter((s) => s !== base).map((s) => `<option value="${esc(s.name)}"${s.name === def ? " selected" : ""}>${esc(label(s))}</option>`).join("")}</select></label><div id="fx-rose-info" class="fx-note"></div></div></div>` +
        `<p class="fx-note">Filled: farthest new burn per sector. Dashed grey: baseline.</p>`;
    }
    h += `<details class="fx-details"><summary>Notes</summary>${d.disclaimer ? `<p class="fx-note">${esc(d.disclaimer)}</p>` : ""}${(d.notes || []).filter((n) => !/^WARNING/i.test(n)).map((n) => `<p class="fx-note">${esc(n)}</p>`).join("")}<p class="fx-note">Run time ${FX.num(d.runtime_s, 1)} s${d.input_fetch_s ? `, input download ${FX.num(d.input_fetch_s, 0)} s` : ""}.</p></details>`;
    h += `<div class="fx-actions"><button type="button" class="tool-btn" id="fx-scen-zoom">${FX.ic("scan")}<span>Zoom to perimeters</span></button></div>`;
    bodyEl.innerHTML = h;
    statusEl.textContent = "";
    $("fx-scen-badge0").hidden = true;   // the badge repeats inside the results
    // map layers
    st.group.clearLayers(); st.layers = {};
    list.forEach((s, i) => {
      if (!s.perimeter) return;
      const stl = style(i);
      st.layers[i] = L.geoJSON({ type: "Feature", properties: {}, geometry: s.perimeter }, { interactive: false,
        style: { color: stl.c, weight: i === 0 ? 3 : 2.4, dashArray: stl.dash, fill: true, fillColor: stl.c, fillOpacity: 0.06 } });
    });
    const first = list.indexOf(base);
    const set = (i, on) => { st.visible[i] = on; if (on) st.layers[i] && st.layers[i].addTo(st.group); else st.layers[i] && st.group.removeLayer(st.layers[i]); };
    bodyEl.querySelectorAll("input[data-sc]").forEach((cb) => {
      const i = +cb.dataset.sc;
      cb.checked = i === first;
      cb.addEventListener("change", () => set(i, cb.checked));
    });
    if (st.layers[first]) set(first, true);
    if (EF.state.mode === "date") st.group.addTo(map);
    $("fx-scen-zoom").addEventListener("click", () => {
      const b = L.featureGroup(Object.values(st.layers)).getBounds();
      if (b.isValid()) map.fitBounds(b, { padding: [50, 50], maxZoom: 13 });
    });
    drawCharts(list, base, maxKm, d);
    EF.hydrateIcons(bodyEl);
  }
  function drawCharts(list, base, maxKm, d) {
    const ch = $("fx-scen-chart");
    if (ch) ch.innerHTML = chartSVG(list, Math.max(280, Math.floor(ch.clientWidth - 12 || 330)));
    const sel = $("fx-rose-sel");
    if (sel) {
      const upd = () => {
        const i = list.findIndex((s) => s.name === sel.value), sc = list[i];
        $("fx-rose").innerHTML = roseSVG(base, sc, i, maxKm);
        const ch2 = (sc.vs_baseline && sc.vs_baseline.sector_reach_change_km) || {};
        const rows = SECT.filter((n) => Math.abs(ch2[n] || 0) > 0.005).map((n) => `${n} ${ch2[n] > 0 ? "+" : "−"}${FX.num(Math.abs(ch2[n]), 2)} km`);
        const dr = sc.vs_baseline && sc.vs_baseline.drift_shift_km;
        $("fx-rose-info").innerHTML = (rows.length ? `<b>Change in reach vs baseline:</b> ${esc(rows.join(", "))}.` : "No change in reach vs baseline.") +
          (dr ? ` Centre of the burn shifts ${FX.num(dr, 2)} km${sc.vs_baseline.drift_shift_toward ? " toward " + esc(sc.vs_baseline.drift_shift_toward) : ""}.` : "") +
          ` Weather: mean wind ${FX.num((sc.weather || {}).mean_wind_kmh, 0)} km/h from ${FX.num((sc.weather || {}).mean_wind_from_deg, 0)} deg, RH ${FX.num((sc.weather || {}).mean_rh_pct, 0)}%, rain ${FX.num((sc.weather || {}).total_rain_mm, 1)} mm.`;
      };
      sel.onchange = upd; upd();
    }
  }
  window.addEventListener("resize", () => { if (st.data && sec.open) { const list = st.data.scenarios || []; const maxKm = Math.max(0, ...list.map((s) => Math.max(0, ...Object.values(s.sector_reach_km || {})))); drawCharts(list, list.find((s) => s.name === "baseline") || list[0], maxKm, st.data); } });
  refreshOptions();
})();
