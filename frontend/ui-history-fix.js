/* EquiFire: History sensor honesty.
   MODIS counts are 3-7x lower than VIIRS and the record changes instruments (S-NPP VIIRS 2012-01-20, NOAA-20 2018),
   so a step in a count chart is usually an instrument change, not a fire change. This module
   (1) explains raw vs harmonized under the measure picker with numbers computed from the loaded /stats data,
   (2) draws the sensor-era boundaries in the calendar, weekly and yearly charts (hooks called by app.js),
   (3) shows a warning badge for the mixed-sensor measure and (4) links to the 25-year harmonized record.
   Hooks live on window.EqHistFix; app.js calls them defensively, so History still works if this file is missing. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const N20_YEAR = 2018;                    // NOAA-20 (second VIIRS satellite); FIRMS archive starts 2018-04-01
  const DEFAULT_VIIRS = "2012-01-20";       // S-NPP VIIRS start; overridden by d.sensor_ranges.viirs[0]
  const INK = "#1d2b34";
  const ERA_FILL = ["#c9d3d8", "#8a9aa3", "#4a5b65"];   // gutter bars: MODIS only, + S-NPP, + NOAA-20
  const MIXED = new Set(["any_cell_days"]);
  const COUNT = new Set(["viirs_cell_days", "viirs_detections", "modis_cell_days", "modis_detections", "any_cell_days"]);
  const TEAL = "#145c64";

  let cur = null;           // last History payload
  let yrMode = "measure";   // yearly chart for frp/any metrics: "measure" | "sensor"

  // ------------------------------------------------------------ era helpers
  function viirsStart(d) {
    const s = (d && d.sensor_ranges && d.sensor_ranges.viirs && d.sensor_ranges.viirs[0]) || DEFAULT_VIIRS;
    return { date: s, year: +s.slice(0, 4) };
  }
  // era index of a year: 0 MODIS only, 1 + S-NPP VIIRS, 2 + NOAA-20
  function eraOf(year, d) {
    const v = viirsStart(d).year;
    return year < v ? 0 : year < N20_YEAR ? 1 : 2;
  }
  const ERA_SHORT = ["MODIS", "+VIIRS", "+NOAA-20"];
  const ERA_LONG = [
    "MODIS only (Terra + Aqua)",
    "MODIS + S-NPP VIIRS (VIIRS from 20 Jan 2012)",
    "MODIS + two VIIRS satellites (NOAA-20 from 2018)",
  ];
  const ERA_NAME = (d) => ["MODIS only", "S-NPP VIIRS starts " + fmtDate(viirsStart(d).date), "NOAA-20 added"];
  function fmtDate(s) {
    const t = new Date(s + "T00:00:00Z");
    return isNaN(t) ? s : t.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
  }
  // sensor-era group used for era-matched baselines of the sensor-mixing measures
  function eraGroup(year, d) { return year >= viirsStart(d).year ? 1 : 0; }

  // ------------------------------------------------------------ SVG hooks for app.js
  const halo = (x1, y1, x2, y2) =>
    `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="#fff" stroke-opacity=".85" stroke-width="2.4"/>` +
    `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${INK}" stroke-width="1" stroke-dasharray="3 2"/>`;

  // Calendar: rows = years (top to bottom). Dashed line above the first row of each new era, labelled gutter at the right.
  function calendar(c) {
    const { rows, L, cw, T, ch, W, R, d } = c;
    if (!rows.length) return "";
    const x2 = (W - R + 2).toFixed(1);
    const firstRow = [0, 1, 2].map((e) => rows.findIndex((r) => eraOf(r.y, d) === e));
    let s = "";
    [0, 1, 2].forEach((e) => {
      const a = firstRow[e];
      if (a < 0) return;
      const nxt = e < 2 ? (firstRow[e + 1] >= 0 ? firstRow[e + 1] : (e === 0 && firstRow[2] >= 0 ? firstRow[2] : -1)) : -1;
      const b = nxt >= 0 ? nxt : rows.length;
      const y0 = T + a * ch, y1 = T + b * ch;
      s += `<rect x="${(W - R + 5).toFixed(1)}" y="${(y0 + 0.5).toFixed(1)}" width="3" height="${(y1 - y0 - 1).toFixed(1)}" fill="${ERA_FILL[e]}"><title>${esc(ERA_LONG[e])}</title></rect>`;
      s += `<text x="${(W - R + 10).toFixed(1)}" y="${((y0 + y1) / 2 + 2.5).toFixed(1)}" class="ax hf-era"><title>${esc(ERA_LONG[e])}</title>${ERA_SHORT[e]}</text>`;
      if (e > 0 && a > 0) s += halo((L - 1).toFixed(1), y0.toFixed(1), x2, y0.toFixed(1));
    });
    return s;
  }

  // Weekly chart: era label of the selected year, plus a marker where VIIRS / NOAA-20 start inside that year.
  function weekPos(y, iso) {
    const jan4 = new Date(Date.UTC(y, 0, 4)), dow = jan4.getUTCDay() || 7;
    const w1 = Date.UTC(y, 0, 4 - dow + 1);
    return 1 + (new Date(iso + "T00:00:00Z").getTime() - w1) / (7 * 864e5);
  }
  function weekly(c) {
    const { x, L, R, W, T, H, B, year, d } = c;
    const e = eraOf(year, d), v = viirsStart(d);
    let s = `<text x="${L}" y="9" class="ax hf-era-top">${year}: ${esc(ERA_LONG[e])}</text>`;
    const mark = (iso, label) => {
      const wp = weekPos(year, iso);
      if (!(wp >= 1 && wp <= 53)) return "";
      const xx = x(Math.max(1, wp)).toFixed(1), right = wp > 40;
      return halo(xx, T, xx, H - B) + `<text x="${right ? +xx - 3 : +xx + 3}" y="${T + 8}" text-anchor="${right ? "end" : "start"}" class="ax hf-era-top">${esc(label)}</text>`;
    };
    if (year === v.year) s += mark(v.date, "S-NPP VIIRS starts " + fmtDate(v.date).replace(/ \d{4}$/, ""));
    if (year === N20_YEAR) s += mark("2018-04-01", "NOAA-20 added (Apr)");
    return s;
  }

  // Yearly bars: shaded MODIS-only era behind the bars, dashed boundaries and era labels on top.
  function yearlyBand(c) {
    const { py, L, gw, T, H } = c;
    const i0 = py.findIndex((r) => eraOf(r.year, c.d) >= 1);
    if (i0 <= 0) return "";
    const w = i0 * gw;
    return `<rect x="${L}" y="${T}" width="${w.toFixed(1)}" height="${H - T}" fill="#eef1f3"><title>MODIS only (before ${esc(fmtDate(viirsStart(c.d).date))})</title></rect>`;
  }
  function yearlyLines(c) {
    const { py, L, gw, T, H, B, d } = c;
    let s = "";
    const idx = [0, 1, 2].map((e) => py.findIndex((r) => eraOf(r.year, d) === e));
    [0, 1, 2].forEach((e) => {
      const a = idx[e];
      if (a < 0) return;
      const b = e < 2 && idx[e + 1] >= 0 ? idx[e + 1] : py.length;
      const x0 = L + a * gw, x1 = L + b * gw;
      s += `<text x="${((x0 + x1) / 2).toFixed(1)}" y="${T - 6}" text-anchor="middle" class="ax hf-era-top"><title>${esc(ERA_LONG[e])}</title>${["MODIS only", "+ S-NPP VIIRS", "+ NOAA-20"][e]}</text>`;
      if (e > 0 && a > 0) s += halo(x0.toFixed(1), T - 12, x0.toFixed(1), H - B);
    });
    return s;
  }

  // yearly chart: which series? sensor metrics keep the per-sensor bars; frp/any show the chosen measure (default)
  function yearlyMode(d) {
    return d.metric === "frp_equiv_mw" || d.metric === "any_cell_days" ? yrMode : "sensor";
  }

  // era-matched baselines (z-scores in the calendar's anomaly mode) for the sensor-mixing measures
  function eraMatched(d) {
    return !!(d && d.selected && d.selected.key && d.selected.key.baseline_scope === "same_sensor_era");
  }
  function calendarGroup(d) {
    if (!d || !eraMatched(d)) return null;
    return (year) => eraGroup(year, d);
  }

  // ------------------------------------------------------------ numbers from the loaded data
  const median = (a) => { const b = a.slice().sort((p, q) => p - q); return b.length ? (b.length % 2 ? b[(b.length - 1) >> 1] : (b[b.length / 2 - 1] + b[b.length / 2]) / 2) : null; };
  function overlapRatios(d) {
    const rc = [], rd = [];
    d.per_year.forEach((r) => {
      if (r.year < 2013 || !(r.viirs_coverage_days >= 360) || !(r.modis_coverage_days >= 360)) return;
      if (r.viirs_cell_days > 0 && r.modis_cell_days != null) rc.push(r.modis_cell_days / r.viirs_cell_days);
      if (r.viirs_detections > 0 && r.modis_detections != null) rd.push(r.modis_detections / r.viirs_detections);
    });
    if (rc.length < 3) return null;
    return { cd: median(rc), det: median(rd), n: rc.length, y0: 2013 };
  }
  function step(d, key) {
    const m = (ys) => {
      const v = ys.map((y) => (d.per_year.find((r) => r.year === y) || {})[key]).filter((x) => x != null);
      return v.length === ys.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
    };
    const pre = m([2009, 2010, 2011]), post = m([2013, 2014, 2015]);
    return pre > 0 && post != null ? post / pre : null;
  }
  const pct = (v) => Math.round(v * 100) + "%";
  const times = (v) => "×" + (v >= 10 ? v.toFixed(0) : v.toFixed(1));

  // ------------------------------------------------------------ explanatory block under the measure picker
  function ensureDom() {
    if ($("hf-note")) return;
    const ctl = document.querySelector("#history .hist-controls");
    if (!ctl) return;
    const note = document.createElement("div");
    note.id = "hf-note";
    note.className = "hf-note";
    note.setAttribute("aria-live", "polite");
    ctl.insertAdjacentElement("afterend", note);
    // per-chart badges + era legend
    [["cal-chart", "hf-b-cal"], ["wk-chart", "hf-b-wk"], ["yr-chart", "hf-b-yr"]].forEach(([id, bid]) => {
      const box = $(id);
      if (!box) return;
      const b = document.createElement("p");
      b.id = bid;
      b.className = "hf-badge";
      b.hidden = true;
      box.insertAdjacentElement("beforebegin", b);
    });
    const lg = $("cal-legend");
    if (lg) {
      const e = document.createElement("p");
      e.id = "hf-era-legend";
      e.className = "hf-era-legend small muted";
      lg.insertAdjacentElement("afterend", e);
    }
    const yh = document.querySelector("#yr-title");
    if (yh && !$("hf-yr-toggle")) {
      const head = document.createElement("div");
      head.className = "chart-head";
      yh.parentNode.insertBefore(head, yh);
      head.appendChild(yh);
      const seg = document.createElement("div");
      seg.id = "hf-yr-toggle";
      seg.className = "seg";
      seg.setAttribute("role", "group");
      seg.setAttribute("aria-label", "Yearly chart series");
      seg.hidden = true;
      seg.innerHTML = '<button id="hf-yr-measure" class="seg-btn active" type="button" aria-pressed="true">This measure</button>' +
        '<button id="hf-yr-sensor" class="seg-btn" type="button" aria-pressed="false">By sensor (raw)</button>';
      head.appendChild(seg);
      const set = (m) => {
        yrMode = m;
        $("hf-yr-measure").classList.toggle("active", m === "measure");
        $("hf-yr-sensor").classList.toggle("active", m === "sensor");
        $("hf-yr-measure").setAttribute("aria-pressed", m === "measure");
        $("hf-yr-sensor").setAttribute("aria-pressed", m === "sensor");
        if (window.EqHistApp) window.EqHistApp.rerenderYearly();
      };
      $("hf-yr-measure").addEventListener("click", () => set("measure"));
      $("hf-yr-sensor").addEventListener("click", () => set("sensor"));
    }
  }

  function goHarmonized() {
    const sec = $("hz-section");
    const meas = { viirs_cell_days: "cell_days", modis_cell_days: "cell_days", any_cell_days: "cell_days", viirs_detections: "detections", modis_detections: "detections", frp_equiv_mw: "frp_mw" }[cur && cur.metric];
    if (sec) {
      sec.open = true;
      const ms = $("hz-measure");
      if (ms && meas && ms.value !== meas && [...ms.options].some((o) => o.value === meas)) {
        ms.value = meas;
        ms.dispatchEvent(new Event("change", { bubbles: true }));
      }
      sec.scrollIntoView({ behavior: "smooth", block: "start" });
      const sm = sec.querySelector("summary");
      if (sm) setTimeout(() => sm.focus({ preventScroll: true }), 350);
    } else {
      const h = $("history");
      if (h) h.scrollTo ? h.scrollTo({ top: h.scrollHeight, behavior: "smooth" }) : null;
      const sc = document.getElementById("panel");
      if (sc) sc.scrollTo({ top: sc.scrollHeight, behavior: "smooth" });
    }
  }

  function switchMetric(m) {
    const sel = $("hist-metric");
    if (!sel) return;
    sel.value = m;
    sel.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function render(d) {
    cur = d;
    ensureDom();
    const note = $("hf-note");
    if (!note) return;
    const m = d.metric, v = viirsStart(d);
    const ov = overlapRatios(d);
    const sAny = step(d, "any_cell_days"), sFrp = step(d, "frp_equiv_mw"), sMod = step(d, "modis_cell_days");
    let head, tone = "info";
    if (m === "frp_equiv_mw") {
      head = "<strong>Harmonized fire power (VIIRS-equivalent).</strong> MODIS power is converted to the VIIRS scale, so MW is comparable between sensors; counts are not.";
    } else if (m === "any_cell_days") {
      tone = "warn";
      head = "<strong>Raw count, two sensors mixed.</strong> MODIS-only days before " + esc(fmtDate(v.date)) + " are joined to MODIS + VIIRS days after, so the record jumps in 2012 and 2018.";
    } else if (m.startsWith("modis")) {
      head = "<strong>Raw MODIS count (Terra + Aqua, 1 km).</strong> Same instruments throughout: the steadiest basis for trends across 2012, but it counts only a fraction of what VIIRS sees.";
    } else {
      head = "<strong>Raw VIIRS count (375 m, from " + esc(fmtDate(v.date)) + ").</strong> NOAA-20 joined S-NPP in " + N20_YEAR + " with more overpasses, so a " + N20_YEAR + " step is partly instrument. Nothing before 2012.";
    }
    let facts = [];
    if (ov) {
      facts.push(`In this area, in ${ov.n} overlap years (${ov.y0}+), MODIS counted <b>${pct(ov.cd)}</b> of the burning cell-days VIIRS saw` +
        (ov.det != null ? ` and <b>${pct(ov.det)}</b> of its detections (medians).` : "."));
    }
    facts.push("Across Amazon, N. Australia, India, W. USA and Canada, MODIS counted 13-56% of VIIRS cell-days and 8-31% of detections.");
    if (sAny != null && sFrp != null && sMod != null) {
      facts.push(`Step across 2012 here (2013-15 over 2009-11): any-sensor cell-days <b>${times(sAny)}</b>, harmonized FRP <b>${times(sFrp)}</b>, MODIS-only cell-days <b>${times(sMod)}</b>. Part of any step is real change.`);
    }
    if (m === "frp_equiv_mw") {
      facts.push("Fire power converts well per detection (held-out 2021-2025: total FRP within about 6% of real VIIRS), but some 2012 step remains. For trends across 2012 use the MODIS-only measure or the harmonized record below.");
    }
    if (d.selected && d.selected.key && (m === "frp_equiv_mw" || m === "any_cell_days")) {
      facts.push(eraMatched(d)
        ? "Baselines, z-scores and anomaly flags compare each year only with years of the same sensor era (before or from 2012)."
        : "Baselines, z-scores and anomaly flags here compare against all years, including the MODIS-only years before 2012.");
    }
    const warn = "";
    const showLink = COUNT.has(m) || m === "frp_equiv_mw";
    const prim = COUNT.has(m);
    const actions =
      (showLink ? `<button type="button" id="hf-go-harm" class="tool-btn small-btn${prim ? " hf-primary" : ""}">${EF_icon("arrow-down")}<span>See the harmonized 25-year record</span></button>` : "") +
      (m !== "modis_cell_days" ? `<button type="button" id="hf-go-modis" class="tool-btn small-btn">Show MODIS-only burning cells (steady record)</button>` : "");
    note.className = "hf-note hf-" + tone;
    note.innerHTML = warn + `<p class="hf-head">${head}</p><ul class="hf-facts small">${facts.map((f) => `<li>${f}</li>`).join("")}</ul><div class="hf-actions">${actions}</div>`;
    const g = $("hf-go-harm"); if (g) g.addEventListener("click", goHarmonized);
    const mo = $("hf-go-modis"); if (mo) mo.addEventListener("click", () => switchMetric("modis_cell_days"));
    if (window.EquiFire && window.EquiFire.hydrateIcons) window.EquiFire.hydrateIcons();

    // per-chart badges
    ["hf-b-cal", "hf-b-wk", "hf-b-yr"].forEach((id) => {
      const b = $(id);
      if (!b) return;
      b.hidden = !MIXED.has(m);
      if (!b.hidden) b.innerHTML = EF_icon("alert-triangle") + "<span>Mixed sensors: 2012 and 2018 steps are instrument changes</span>";
    });
    if (window.EquiFire && window.EquiFire.hydrateIcons) window.EquiFire.hydrateIcons();

    // era legend under the calendar
    const el = $("hf-era-legend");
    if (el) {
      el.innerHTML = `<b>Sensor eras</b> (dashed lines mark changes): ` +
        `<span><i class="hf-sw" style="background:${ERA_FILL[0]}"></i>MODIS only (Terra + Aqua)</span> ` +
        `<span><i class="hf-sw" style="background:${ERA_FILL[1]}"></i>+ S-NPP VIIRS from ${esc(fmtDate(v.date))}</span> ` +
        `<span><i class="hf-sw" style="background:${ERA_FILL[2]}"></i>+ NOAA-20 from ${N20_YEAR}</span>`;
    }
    const tg = $("hf-yr-toggle");
    if (tg) tg.hidden = !(m === "frp_equiv_mw" || m === "any_cell_days");
    // yearly legend (measure mode) is written by app.js through yearlyLegend()
  }

  function EF_icon(name) {
    const f = window.EquiFire && window.EquiFire.icon;
    return f ? f(name) : "";
  }

  function yearlyLegend(d) {
    const era = (e, t) => `<span><i class="hf-sw" style="background:${ERA_FILL[e]}"></i>${t}</span>`;
    const meas = d.metric === "frp_equiv_mw"
      ? `<span><i class="sw" style="background:${TEAL}"></i>harmonized FRP (MW)</span>`
      : `<span><i class="sw" style="background:#7a4b00"></i>burning cell-days, MODIS or VIIRS</span>`;
    return meas + `<span><i class="sw" style="background:${d.metric === "frp_equiv_mw" ? TEAL : "#7a4b00"};opacity:.45"></i>MODIS-only years</span>` +
      era(0, "shaded: MODIS only") + `<span class="muted">dashed lines: VIIRS 2012, NOAA-20 2018 &middot; * partial year</span>`;
  }

  // picker: label raw vs harmonized and preselect the consistent MODIS measure (matches hist.metric in app.js)
  (function initPicker() {
    const sel = $("hist-metric");
    if (!sel) return;
    const lab = {
      frp_equiv_mw: "Harmonized FRP, VIIRS-equivalent (MW)",
      viirs_cell_days: "VIIRS burning cells (raw)",
      modis_cell_days: "MODIS burning cells (raw)",
      any_cell_days: "Any-sensor burning cells (raw, mixed)",
      viirs_detections: "VIIRS detections (raw)",
      modis_detections: "MODIS detections (raw)",
    };
    [...sel.options].forEach((o) => { if (lab[o.value]) o.textContent = lab[o.value]; });
    const first = [...sel.options].find((o) => o.value === "modis_cell_days");
    if (first) sel.insertBefore(first, sel.firstChild);
    sel.value = "modis_cell_days";
  })();

  window.EqHistFix = {
    calendar, weekly, yearlyBand, yearlyLines, yearlyMode, yearlyLegend, calendarGroup, eraOf,
    after: (d) => { try { render(d); } catch (e) { console.error("history note", e); } },
  };
})();
