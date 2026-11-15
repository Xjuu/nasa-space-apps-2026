/* EquiFire: 25-year harmonized record (History workspace) + attribution footer and "About this tool".
   Add-on module; plain SVG, no chart library. Uses ONLY numbers returned by GET /harmonized/series. */
(function () {
  "use strict";
  const EF = window.EquiFire;
  if (!EF) return;
  const { getJSON, esc, icon } = EF;

  // ---------------------------------------------------------------- footer: attribution + About this tool
  (function footer() {
    const a = (u, t) => `<a href="${u}" target="_blank" rel="noopener">${t}</a>`;
    const credits = document.getElementById("app-attribution");
    if (credits) {
      credits.innerHTML = [
        `Fire detections: ${a("https://firms.modaps.eosdis.nasa.gov/", "NASA FIRMS")} (MODIS, VIIRS)`,
        `${a("https://open-meteo.com/", "Weather data by Open-Meteo.com")}`,
        `${a("https://esa-worldcover.org/", "ESA WorldCover")}`,
        `${a("https://dataspace.copernicus.eu/", "Copernicus DEM / Sentinel-2")}`,
        `${a("https://www.openstreetmap.org/copyright", "OpenStreetMap contributors")}`,
        `${a("https://www.goes.noaa.gov/", "NOAA GOES")}`,
        `${a("https://www.rainviewer.com/", "RainViewer")}`,
        `${a("https://dataforgood.facebook.com/dfg/tools/high-resolution-population-density-maps", "Meta/CIESIN HRSL")}`,
        `Esri imagery only when selected in Layers`,
      ].join(" · ");
    }
    const foot = document.querySelector(".panel-footer");
    if (!foot || document.getElementById("about-tool")) return;
    const d = document.createElement("details");
    d.id = "about-tool";
    d.className = "about-tool";
    d.innerHTML = `<summary>About this tool</summary>
      <div class="about-body small">
        <p><strong>FireSpot is a research prototype, not validated for operational decisions</strong> (evacuation, dispatch, insurance pricing, safety of life). Everything below is a retrospective benchmark on satellite detections, copied from the model card (<code>docs/MODEL_CARD.md</code> in the repository).</p>
        <h4>Headline numbers</h4>
        <ul>
          <li>Spread forecast (ML, walk-forward, 100 sampled fires per test year 2023-2025): within-5 km score on day 1 is 52-59% and on day 3 69-76%. Exact-cell (strict) overlap is 18-23% on day 1 and 25-31% on day 3. The project target of more than 70% on day 1 is not met.</li>
          <li>ML + physics blend: no measured gain over ML alone (2024, day 1 within 5 km: 58.68% vs 58.89%).</li>
          <li>Physics simulator: validated on 20 fires only; it over-predicts area, typically 3-6 times with calibration.</li>
          <li>1 km alerts (test 2023-2025): precision 63-66%, recall only 9-15%.</li>
          <li>MODIS-era reconstruction before 2012 (held-out 2021-2025): precision 0.47, recall 0.35, IoU 0.25. It recovers totals better than individual cells.</li>
          <li>Routine-burning vs anomaly classifier: 10 of 12 independent events (a small, hand-picked sample).</li>
        </ul>
      </div>`;
    const credEl = foot.querySelector(".footer-credits");
    if (credEl) credEl.insertAdjacentElement("afterend", d); else foot.appendChild(d);
  })();

  // ---------------------------------------------------------------- harmonized chart in History
  const host = document.getElementById("history");
  if (!host) return;

  const STYLE = {
    modis_raw: { color: "#0072B2", dash: "", marker: "circle" },
    viirs_snpp_raw: { color: "#D55E00", dash: "7 4", marker: "square" },
    viirs_n20_raw: { color: "#009E73", dash: "2 3", marker: "triangle" },
    harmonized: { color: "#145c64", dash: "", marker: "circle", width: 2.6 },
    harmonized_cal: { color: "#B07A00", dash: "7 4", marker: "diamond", width: 2.2 },
  };
  const RAW_IDS = ["modis_raw", "viirs_snpp_raw", "viirs_n20_raw"];
  const HARM_IDS = ["harmonized", "harmonized_cal"];
  const FALLBACK_LIST = {
    regions: { five_countries: "Five countries (Brazil, Australia, India, United States, Canada)", Brazil: "Brazil", Australia: "Australia", India: "India", United_States: "United States", Canada: "Canada" },
    measures: { detections: { label: "Fire detections" }, cell_days: { label: "Burning 0.05 deg cell-days" }, frp_mw: { label: "Fire radiative power, sum" } },
  };

  const hz = { list: null, data: null, region: "five_countries", measure: "detections", res: "year", seq: 0, idx: null, started: false, built: false };

  function build() {
    if (hz.built) return;
    hz.built = true;
    const sec = document.createElement("details");
    sec.id = "hz-section";
    sec.className = "hz-section chart-block";
    sec.open = true;
    sec.innerHTML = `
      <summary class="hz-summary"><span class="hz-title">25-year harmonized record</span></summary>
      <p class="muted small hz-intro">MODIS (from 2000) and VIIRS (from 2012) count fires differently, so a raw splice jumps in 2012. Before 2012-01-20 the harmonized series is reconstructed from MODIS. Whole-region data, not your drawn area.</p>
      <div class="row hist-controls hz-controls">
        <label class="input-field"><span>Region</span><select id="hz-region" name="hz-region"></select></label>
        <label class="input-field"><span>Measure</span><select id="hz-measure" name="hz-measure"></select></label>
        <label class="input-field hz-res"><span>Resolution</span><select id="hz-res" name="hz-res"><option value="year">Yearly</option><option value="month">Monthly</option></select></label>
      </div>
      <p id="hz-status" class="status-msg" role="status" aria-live="polite"></p>
      <div id="hz-error" class="alert-box error" role="alert" hidden></div>
      <div id="hz-body" hidden>
        <h3 class="hz-h">Raw record, by sensor</h3>
        <div id="hz-chart-raw" class="hz-chart"></div>
        <div id="hz-leg-raw" class="legend hz-legend"></div>
        <h3 class="hz-h">Harmonized record</h3>
        <div id="hz-chart-harm" class="hz-chart"></div>
        <div id="hz-leg-harm" class="legend hz-legend"></div>
        <p id="hz-readout" class="chart-info hz-readout" aria-live="polite">Hover, tap or press arrow keys to read values.</p>
        <div id="hz-verdict" class="hz-verdict"></div>
        <details class="methodology"><summary>Data table and notes</summary><div id="hz-table-wrap" class="hz-table-wrap"></div><ul id="hz-notes" class="hz-notes small muted"></ul></details>
      </div>`;
    host.appendChild(sec);
    sec.querySelector("#hz-region").addEventListener("change", (e) => { hz.region = e.target.value; load(); });
    sec.querySelector("#hz-measure").addEventListener("change", (e) => { hz.measure = e.target.value; load(); });
    sec.querySelector("#hz-res").addEventListener("change", (e) => { hz.res = e.target.value; load(); });
    sec.addEventListener("toggle", () => { if (sec.open && hz.data) draw(); });
    if (window.ResizeObserver) {
      let t = null;
      new ResizeObserver(() => { clearTimeout(t); t = setTimeout(() => { if (hz.data && sec.open && !sec.closest("[hidden]")) draw(); }, 80); }).observe(host);
    } else window.addEventListener("resize", () => { if (hz.data) draw(); });
  }

  async function start() {
    if (hz.started) return;
    hz.started = true;
    build();
    try { hz.list = await getJSON("/harmonized/series"); } catch (e) { hz.list = FALLBACK_LIST; }
    const L = hz.list || FALLBACK_LIST;
    const rs = document.getElementById("hz-region");
    rs.innerHTML = Object.entries(L.regions || FALLBACK_LIST.regions).map(([k, v]) => `<option value="${esc(k)}">${esc(v)}</option>`).join("");
    if ((L.regions || {})[hz.region] === undefined) hz.region = rs.options[0] ? rs.options[0].value : hz.region;
    rs.value = hz.region;
    const ms = document.getElementById("hz-measure");
    ms.innerHTML = Object.entries(L.measures || FALLBACK_LIST.measures).map(([k, v]) => `<option value="${esc(k)}">${esc(v.label || k)}</option>`).join("");
    ms.value = hz.measure;
    load();
  }

  async function load() {
    const seq = ++hz.seq;
    const st = document.getElementById("hz-status"), err = document.getElementById("hz-error");
    err.hidden = true;
    st.textContent = "Loading harmonized series…";
    try {
      const d = await getJSON("/harmonized/series", { region: hz.region, measure: hz.measure, resolution: hz.res });
      if (seq !== hz.seq) return;
      hz.data = d; hz.idx = null;
      st.textContent = "";
      document.getElementById("hz-body").hidden = false;
      draw();
      verdict();
    } catch (e) {
      if (seq !== hz.seq) return;
      st.textContent = "";
      err.textContent = "Harmonized record unavailable: " + (e.message || e);
      err.hidden = false;
      document.getElementById("hz-body").hidden = true;
    }
  }

  // ---------------------------------------------------------------- helpers
  const fmtN = (v) => (v == null ? "n/a" : Math.abs(v) >= 1e6 ? (v / 1e6).toFixed(2) + "M" : Math.abs(v) >= 1e3 ? (v / 1e3).toFixed(1) + "K" : String(Math.round(v)));
  const fmtAxis = (v) => (v >= 1e6 ? +(v / 1e6).toFixed(1) + "M" : v >= 1e3 ? +(v / 1e3).toFixed(0) + "K" : String(v));
  const times = (r) => (r.periods || []).map((p) => {
    if (typeof p === "number") return p + 0.5;
    const m = /^(\d{4})-(\d{2})/.exec(String(p));
    return m ? +m[1] + (+m[2] - 0.5) / 12 : NaN;
  });
  function dateFrac(s) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(s || ""));
    if (!m) return null;
    return +m[1] + (Date.UTC(+m[1], +m[2] - 1, +m[3]) - Date.UTC(+m[1], 0, 1)) / (365.25 * 86400000);
  }
  function niceMax(v) {
    if (!(v > 0)) return 1;
    const e = Math.pow(10, Math.floor(Math.log10(v))), f = v / e;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * e;
  }
  function marker(kind, x, y, hollow, color) {
    const fill = hollow ? "#fff" : color, common = `fill="${fill}" stroke="${color}" stroke-width="1.6"`;
    switch (kind) {
      case "square": return `<rect x="${(x - 3.5).toFixed(1)}" y="${(y - 3.5).toFixed(1)}" width="7" height="7" ${common}/>`;
      case "triangle": return `<path d="M${x.toFixed(1)} ${(y - 4.5).toFixed(1)}L${(x + 4.2).toFixed(1)} ${(y + 3.2).toFixed(1)}L${(x - 4.2).toFixed(1)} ${(y + 3.2).toFixed(1)}Z" ${common}/>`;
      case "diamond": return `<path d="M${x.toFixed(1)} ${(y - 4.8).toFixed(1)}L${(x + 4.4).toFixed(1)} ${y.toFixed(1)}L${x.toFixed(1)} ${(y + 4.8).toFixed(1)}L${(x - 4.4).toFixed(1)} ${y.toFixed(1)}Z" ${common}/>`;
      default: return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3.4" ${common}/>`;
    }
  }
  function swatch(id, label) {
    const s = STYLE[id] || { color: "#555", dash: "", marker: "circle" };
    return `<span><svg width="30" height="12" viewBox="0 0 30 12" aria-hidden="true" focusable="false"><line x1="1" y1="6" x2="29" y2="6" stroke="${s.color}" stroke-width="${s.width || 2.2}" ${s.dash ? `stroke-dasharray="${s.dash}"` : ""}/>${marker(s.marker, 15, 6, false, s.color)}</svg>${esc(label)}</span>`;
  }

  // ---------------------------------------------------------------- chart
  function partialSet(r) { const o = r.partial_years || {}; const m = {}; Object.keys(o).forEach((k) => { m[k] = String(o[k]); }); return m; }

  function chartSVG(r, ids, cfg) {
    const host = document.getElementById(cfg.container);
    const W = Math.max(300, Math.floor(host.clientWidth || host.parentElement.clientWidth || 340));
    const H = W < 420 ? 230 : 250;
    const m = { l: 46, r: 20, t: 14, b: 28 };
    const T = times(r), n = T.length;
    const series = (r.series || []).filter((s) => ids.includes(s.id));
    const x0 = Math.floor(Math.min(...T)), x1 = Math.ceil(Math.max(...T));
    let ymax = 0;
    series.forEach((s) => s.values.forEach((v) => { if (v != null && v > ymax) ymax = v; }));
    ymax = niceMax(ymax * 1.04);
    const X = (t) => m.l + ((t - x0) / (x1 - x0)) * (W - m.l - m.r);
    const Y = (v) => H - m.b - (v / ymax) * (H - m.t - m.b);
    let s = `<svg class="hz-svg" role="img" tabindex="0" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" aria-label="${esc(cfg.aria)}" data-chart="${cfg.key}">`;
    s += `<defs><pattern id="hz-terra-${cfg.key}" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="6" stroke="#8a9aa3" stroke-width="1.2" stroke-opacity=".7"/></pattern></defs>`;
    // eras
    const aquaEv = (r.events || []).find((e) => /aqua/i.test(e.label));
    const terraEnd = aquaEv ? dateFrac(aquaEv.date) : null;
    const reconEnd = dateFrac(r.reconstructed_until) != null ? dateFrac(r.reconstructed_until) + 1 / 365.25 : null;
    if (cfg.showRecon && reconEnd) s += `<rect x="${X(x0)}" y="${m.t}" width="${(X(reconEnd) - X(x0)).toFixed(1)}" height="${H - m.t - m.b}" fill="#e4ebee"/>`;
    if (terraEnd) s += `<rect x="${X(x0)}" y="${m.t}" width="${(X(terraEnd) - X(x0)).toFixed(1)}" height="${H - m.t - m.b}" fill="url(#hz-terra-${cfg.key})"/>`;
    // grid + y axis
    for (let i = 0; i <= 4; i++) {
      const v = (ymax * i) / 4, y = Y(v);
      s += `<line x1="${m.l}" x2="${W - m.r}" y1="${y.toFixed(1)}" y2="${y.toFixed(1)}" stroke="#dce3e6" stroke-width="1"/><text class="hz-ax" x="${m.l - 6}" y="${(y + 3.5).toFixed(1)}" text-anchor="end">${fmtAxis(v)}</text>`;
    }
    const step = W < 420 ? 5 : 2;
    for (let yr = x0; yr <= x1; yr += step) {
      const x = X(yr);
      s += `<line x1="${x.toFixed(1)}" x2="${x.toFixed(1)}" y1="${H - m.b}" y2="${H - m.b + 4}" stroke="#8a9aa3"/><text class="hz-ax" x="${x.toFixed(1)}" y="${H - m.b + 16}" text-anchor="middle">${yr}</text>`;
    }
    s += `<line x1="${m.l}" x2="${W - m.r}" y1="${H - m.b}" y2="${H - m.b}" stroke="#8a9aa3"/>`;
    // 2012 step marker
    const stepX = dateFrac("2012-01-20");
    if (stepX) {
      const x = X(stepX);
      s += `<line x1="${x.toFixed(1)}" x2="${x.toFixed(1)}" y1="${m.t}" y2="${H - m.b}" stroke="#1d2b34" stroke-width="1.4" stroke-dasharray="2 3"/>`;
      s += `<text class="hz-ev" x="${(x - 5).toFixed(1)}" y="${m.t + 11}" text-anchor="end">2012 sensor change</text>`;
    }
    const n20 = (r.events || []).find((e) => /noaa-20/i.test(e.label));
    if (n20 && cfg.key === "raw") {
      const x = X(dateFrac(n20.date));
      s += `<line x1="${x.toFixed(1)}" x2="${x.toFixed(1)}" y1="${m.t}" y2="${H - m.b}" stroke="#526570" stroke-width="1" stroke-dasharray="1 3"/><text class="hz-ev" x="${(x - 4).toFixed(1)}" y="${m.t + 11}" text-anchor="end">NOAA-20</text>`;
    }
    // lines
    const partial = partialSet(r), yearly = r.resolution !== "month";
    series.forEach((sr) => {
      const st = STYLE[sr.id] || { color: "#555", dash: "" };
      let d = "", pen = false;
      sr.values.forEach((v, i) => {
        if (v == null || !isFinite(T[i])) { pen = false; return; }
        d += `${pen ? "L" : "M"}${X(T[i]).toFixed(1)} ${Y(v).toFixed(1)}`;
        pen = true;
      });
      s += `<path d="${d}" fill="none" stroke="${st.color}" stroke-width="${st.width || 2}" ${st.dash ? `stroke-dasharray="${st.dash}"` : ""} stroke-linejoin="round" stroke-linecap="round"/>`;
      if (yearly) sr.values.forEach((v, i) => {
        if (v == null) return;
        const yr = String(r.periods[i]);
        const hollow = partial[yr] !== undefined && !(/harmonized year is complete/i.test(partial[yr]) && HARM_IDS.includes(sr.id));
        s += marker(st.marker, X(T[i]), Y(v), hollow, st.color);
      });
    });
    s += `<line class="hz-cursor" x1="0" x2="0" y1="${m.t}" y2="${H - m.b}" stroke="#1d2b34" stroke-width="1" visibility="hidden"/>`;
    s += `<rect class="hz-hit" x="${m.l}" y="${m.t}" width="${W - m.l - m.r}" height="${H - m.t - m.b}" fill="transparent"/>`;
    s += `</svg>`;
    host.innerHTML = s;
    const svg = host.firstElementChild;
    svg._geom = { X, T, n, m, W };
    return svg;
  }

  function draw() {
    const r = hz.data;
    if (!r) return;
    const label = r.measure_label || r.measure;
    const svgA = chartSVG(r, RAW_IDS, { container: "hz-chart-raw", key: "raw", showRecon: false, aria: `Raw ${label} by sensor, ${r.region_label}. MODIS, VIIRS S-NPP and VIIRS NOAA-20 shown separately; a step appears in 2012 when VIIRS starts.` });
    const svgB = chartSVG(r, HARM_IDS, { container: "hz-chart-harm", key: "harm", showRecon: true, aria: `Harmonized ${label}, ${r.region_label}. Years before 2012 are reconstructed from MODIS as a VIIRS equivalent.` });
    const have = (ids) => ids.filter((id) => (r.series || []).some((s) => s.id === id));
    const lab = (id) => ((r.series || []).find((s) => s.id === id) || {}).label || id;
    document.getElementById("hz-leg-raw").innerHTML = have(RAW_IDS).map((id) => swatch(id, lab(id))).join("") +
      `<span><i class="hz-sw hz-terra"></i>Terra-only era</span><span class="muted">Hollow marker = partial year. Dotted vertical line = 2012 sensor change.</span>`;
    document.getElementById("hz-leg-harm").innerHTML = have(HARM_IDS).map((id) => swatch(id, lab(id))).join("") +
      `<span><i class="hz-sw hz-recon"></i>Reconstructed from MODIS (through ${esc(String(r.reconstructed_until || "2012-01-19"))})</span><span><i class="hz-sw hz-terra"></i>Terra-only era</span>`;
    [svgA, svgB].forEach(wire);
    renderTable(r);
    if (hz.idx != null) setCursor(hz.idx);
  }

  function wire(svg) {
    const g = svg._geom;
    const at = (clientX) => {
      const rect = svg.getBoundingClientRect(), x = clientX - rect.left;
      let best = 0, bd = Infinity;
      g.T.forEach((t, i) => { const d = Math.abs(g.X(t) - x); if (isFinite(t) && d < bd) { bd = d; best = i; } });
      return best;
    };
    svg.addEventListener("pointermove", (e) => setCursor(at(e.clientX)));
    svg.addEventListener("pointerdown", (e) => setCursor(at(e.clientX)));
    svg.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight" && e.key !== "Home" && e.key !== "End") return;
      e.preventDefault();
      const n = g.n, cur = hz.idx == null ? (e.key === "ArrowLeft" || e.key === "End" ? n - 1 : 0) : hz.idx;
      const step = hz.data.resolution === "month" ? 12 : 1;
      let i = e.key === "Home" ? 0 : e.key === "End" ? n - 1 : cur + (e.key === "ArrowRight" ? step : -step);
      setCursor(Math.max(0, Math.min(n - 1, i)));
    });
  }

  function setCursor(i) {
    const r = hz.data;
    if (!r || i == null) return;
    hz.idx = i;
    document.querySelectorAll(".hz-svg").forEach((svg) => {
      const ln = svg.querySelector(".hz-cursor"), g = svg._geom;
      if (!ln || !g) return;
      const x = g.X(g.T[i]);
      ln.setAttribute("x1", x); ln.setAttribute("x2", x); ln.setAttribute("visibility", "visible");
    });
    const parts = (r.series || []).filter((s) => STYLE[s.id]).map((s) => `${s.label.split(" (")[0]}: ${fmtN(s.values[i])}`);
    document.getElementById("hz-readout").textContent = `${r.periods[i]} · ${r.unit}. ${parts.join(" · ")}`;
  }

  function renderTable(r) {
    const wrap = document.getElementById("hz-table-wrap");
    if (r.resolution === "month") { wrap.innerHTML = `<p class="small muted">Monthly values: switch to yearly resolution to see the data table.</p>`; }
    else {
      const ids = [...RAW_IDS, ...HARM_IDS].filter((id) => (r.series || []).some((s) => s.id === id));
      const sr = ids.map((id) => r.series.find((s) => s.id === id));
      wrap.innerHTML = `<div class="hz-table-scroll"><table class="rp-table hz-table"><caption class="sr-only">Yearly values (${esc(r.unit)})</caption><thead><tr><th scope="col">Year</th>${sr.map((s) => `<th scope="col" class="num">${esc(s.label.split(" (")[0])}</th>`).join("")}</tr></thead><tbody>` +
        r.periods.map((p, i) => `<tr><th scope="row">${esc(p)}</th>${sr.map((s) => `<td class="num">${fmtN(s.values[i])}</td>`).join("")}</tr>`).join("") + `</tbody></table></div>`;
    }
    document.getElementById("hz-notes").innerHTML = (r.notes || []).map((t) => `<li>${esc(t)}</li>`).join("");
  }

  // ---------------------------------------------------------------- plain-language verdict (API numbers only)
  function verdict() {
    const r = hz.data, box = document.getElementById("hz-verdict");
    const dis = r.discontinuity || {}, tr = r.trend_2003_2025 || {}, br = r.bridge || {};
    const x = (v) => (v == null ? "n/a" : "x" + Number(v).toFixed(2));
    const sgn = (v) => (v == null ? "n/a" : (v >= 0 ? "+" : "") + Number(v).toFixed(1) + "%/yr");
    const raw = dis.raw_splice || {}, h = dis.harmonized || {}, hc = dis.harmonized_cal || {};
    const th = tr.harmonized || {}, thc = tr.harmonized_cal || {}, trw = tr.raw_splice || {};
    const li = [];
    if (raw.first_viirs_year_2012_over_2011 != null) li.push(`<li><strong>Raw splice:</strong> 2012 is ${x(raw.first_viirs_year_2012_over_2011)} the 2011 level. ${trw.verdict ? esc(trw.verdict.charAt(0).toUpperCase() + trw.verdict.slice(1)) + "." : ""}</li>`);
    if (h.first_viirs_year_2012_over_2011 != null) li.push(`<li><strong>Harmonized:</strong> 2012 over 2011 is ${x(h.first_viirs_year_2012_over_2011)}${hc.first_viirs_year_2012_over_2011 != null ? ` (overlap-calibrated variant ${x(hc.first_viirs_year_2012_over_2011)})` : ""}; mean 2012-2014 over 2009-2011 is ${x(h.mean_2012_2014_over_mean_2009_2011)}${hc.mean_2012_2014_over_mean_2009_2011 != null ? ` (${x(hc.mean_2012_2014_over_mean_2009_2011)})` : ""}.</li>`);
    if (th.trend_pct_per_year != null) li.push(`<li><strong>Trend 2003-2025:</strong> harmonized ${sgn(th.trend_pct_per_year)}${th.trend_pct_per_year_with_2012_step != null ? ` (${sgn(th.trend_pct_per_year_with_2012_step)} with a 2012 step fitted)` : ""}${th.verdict ? `: ${esc(th.verdict)}` : ""}.${thc.verdict ? ` Calibrated variant: ${esc(thc.verdict)}.` : ""}</li>`);
    if (br.median_ratio_2021_2025_heldout != null) li.push(`<li><strong>Reconstruction check (held-out 2021-2025):</strong> reconstructed over real VIIRS median ${x(br.median_ratio_2021_2025_heldout)} (x1.00 would be an exact match); annual correlation ${br.annual_corr_2021_2025_heldout != null ? Number(br.annual_corr_2021_2025_heldout).toFixed(3) : "n/a"}. High year-to-year correlation is not a cell-by-cell recovery.</li>`);
    box.innerHTML = `<h3>What the chart says</h3><ul class="hz-verdict-list small">${li.join("")}</ul>
      <p class="small hz-caveat"><strong>Caveat.</strong> Harmonization reduces the 2012 step but does not remove it for every measure; fire detections are the cleanest. The shaded pre-2012 part is a statistical reconstruction, not an observation.</p>`;
  }

  // ---------------------------------------------------------------- wiring
  EF.onModeChange((mode) => {
    if (mode === "history") { start(); if (hz.data) setTimeout(draw, 80); }
  });
  if (EF.state && EF.state.mode === "history") start();
})();
