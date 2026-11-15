/* Fire-activity calendar grid (History tab, under the harmonized record): rows = years, columns = months.
   Uses GET /harmonized/series (resolution=month, series "harmonized"). Months before 2012-02 are hatched (reconstructed from MODIS).
   Class = ratio to the median of the same calendar month in the OTHER years. Auxiliary view, not a forecast. */
(function () {
  "use strict";
  const EF = window.EquiFire;
  if (!EF) return;
  const { getJSON, esc } = EF;
  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const pl = () => (document.documentElement.lang || "en").startsWith("pl");
  const T = (a, b) => (pl() ? a : b);
  let box = null, data = null, token = 0, retries = 0, sel = null;

  function median(a) { const s = a.slice().sort((x, y) => x - y), n = s.length; return n ? (n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2) : null; }
  const fmt = (v) => (v == null ? "n/a" : v >= 1e6 ? (v / 1e6).toFixed(2) + " M" : v >= 1e3 ? (v / 1e3).toFixed(1) + " k" : String(Math.round(v)));

  function build(host) {
    box = document.createElement("div");
    box.id = "cg-card"; box.className = "cg";
    box.innerHTML = `<h3 class="hz-h">${T("Year-by-month activity grid", "Year-by-month activity grid")}</h3>` +
      `<p class="small muted">Each square is one month of detections since 2000; a stronger shade means more activity. Pick a square to see where that month ranks among the same month in other years.</p>` +
      `<div class="cg-grid" id="cg-grid" role="grid" aria-label="Year-by-month activity grid"></div>` +
      `<div class="cg-leg"><span class="cg-ramp">Less <i data-l="1"></i><i data-l="2"></i><i data-l="3"></i><i data-l="4"></i><i data-l="5"></i> More</span><span><span class="cg-hatch"></span> Reconstructed from MODIS (before Feb 2012)</span><span><span class="cg-top"></span> Marked: busy or exceptional month</span></div>` +
      `<p class="cg-out chart-info" id="cg-out" aria-live="polite">Pick a month.</p>` +
      `<p class="cg-note" id="cg-note"></p>`;
    box.querySelectorAll(".cg-ramp i").forEach((i) => { i.style.background = `color-mix(in srgb,var(--primary) ${[0, 22, 42, 62, 80, 100][+i.dataset.l]}%,var(--surface-2))`; });
    host.insertAdjacentElement("afterend", box);
  }

  function classify(vals, periods, measure) {
    // vals: map "YYYY-MM" -> value; returns {cls,ratio,median} per period
    const byMonth = {};
    periods.forEach((p, i) => { const v = vals[i]; if (v != null && isFinite(v)) (byMonth[+p.slice(5)] = byMonth[+p.slice(5)] || []).push([p.slice(0, 4), v]); });
    const out = {};
    periods.forEach((p, i) => {
      const v = vals[i]; if (v == null || !isFinite(v)) return;
      const m = +p.slice(5), others = (byMonth[m] || []).filter((x) => x[0] !== p.slice(0, 4)).map((x) => x[1]);
      const med = median(others); if (med == null) return;
      if (others.length < 8) return;                                   // too few comparison years for a ranking
      const ratio = med > 0 ? v / med : (v > 0 ? Infinity : 1);
      const below = others.filter((x) => x < v).length, equal = others.filter((x) => x === v).length;
      const pct = (below + 0.5 * equal) / others.length;                // percentile rank among the other years, 0..1
      const rank = others.filter((x) => x > v).length + 1;               // 1 = highest of all years for this calendar month
      let cls = pct < 0.2 ? "Quiet" : pct < 0.8 ? "Typical" : pct < 0.95 ? "Busy" : (ratio >= 1.5 ? "Exceptional" : "Busy");
      out[p] = { cls, ratio, med, rank, n: others.length + 1, pct };
    });
    return out;
  }

  function draw() {
    if (!box || !data) return;
    const periods = data.periods, s = (data.series.find((x) => x.id === "harmonized") || {}).values || [];
    const cls = classify(s, periods, data.measure);
    const years = [...new Set(periods.map((p) => p.slice(0, 4)))];
    const max = Math.max(...s.filter((v) => v != null && isFinite(v)), 1);
    let h = `<div class="cg-h"></div>` + MON.map((m) => `<div class="cg-h">${m[0]}</div>`).join("");
    years.forEach((y) => {
      h += `<div class="cg-y">${y}</div>`;
      for (let m = 1; m <= 12; m++) {
        const p = `${y}-${String(m).padStart(2, "0")}`, i = periods.indexOf(p), v = i >= 0 ? s[i] : null;
        if (v == null || !isFinite(v)) { h += `<div class="cg-c cg-nodata" title="${MON[m - 1]} ${y}: no data"></div>`; continue; }
        const lv = v <= 0 ? 0 : Math.min(5, 1 + Math.floor((v / max) * 5 * 0.999));
        const est = p < "2012-02" ? 1 : 0, c = cls[p] ? cls[p].cls : "";
        h += `<button type="button" class="cg-c" data-p="${p}" data-l="${lv}" data-est="${est}" data-cls="${c}" aria-label="${MON[m - 1]} ${y}: ${fmt(v)}${est ? ", reconstructed from MODIS" : ""}${c ? ", " + c : ""}" title="${MON[m - 1]} ${y}: ${fmt(v)} ${esc(data.unit || "")}${est ? " (reconstructed from MODIS)" : ""}"></button>`;
      }
    });
    const g = box.querySelector("#cg-grid"); g.innerHTML = h;
    box.querySelector("#cg-note").textContent = `${data.region_label || data.region} · ${data.measure_label || data.measure} (${data.unit || ""}). Each month is ranked against the same calendar month in the other years: quiet = lowest 20 %, typical = middle 60 %, busy = top 20 %, exceptional = top 5 % and at least 1.5× the median. Needs 8 other years.`;
    g.querySelectorAll("button.cg-c").forEach((b) => b.addEventListener("click", () => {
      g.querySelectorAll(".cg-sel").forEach((x) => x.classList.remove("cg-sel")); b.classList.add("cg-sel"); sel = b.dataset.p;
      const i = periods.indexOf(sel), v = s[i], c = cls[sel];
      box.querySelector("#cg-out").textContent = c ? `${MON[+sel.slice(5) - 1]} ${sel.slice(0, 4)}: ${fmt(v)}, ranked ${c.rank} of ${c.n} for this month (median of other years ${fmt(c.med)}); ${c.cls.toLowerCase()}.` : `${MON[+sel.slice(5) - 1]} ${sel.slice(0, 4)}: ${fmt(v)}.`;
    }));
  }

  async function load() {
    const my = ++token;
    const region = (document.getElementById("hz-region") || {}).value, measure = (document.getElementById("hz-measure") || {}).value;
    if (!region || !measure) { if (retries++ < 20) setTimeout(load, 700); return; }
    retries = 0;
    try { const d = await getJSON("/harmonized/series", { region, measure, resolution: "month" }); if (my !== token) return; data = d; draw(); }
    catch (e) { if (box) box.querySelector("#cg-grid").textContent = "Data unavailable."; }
  }

  function init() {
    const host = document.getElementById("hz-verdict");
    if (!host) return false;
    if (!box) {
      build(host);
      ["hz-region", "hz-measure"].forEach((id) => { const e = document.getElementById(id); if (e) e.addEventListener("change", load); });
      const sec = host.closest("details"); if (sec) sec.addEventListener("toggle", () => { if (sec.open && !data) load(); });
    }
    load();
    return true;
  }
  let tries = 0;
  const iv = setInterval(() => { if (init() || ++tries > 40) clearInterval(iv); }, 500);
})();
