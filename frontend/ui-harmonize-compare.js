/* Before / after harmonization slider (History tab, under the harmonized record).
   Draws the raw splice (MODIS to 2011, VIIRS from 2012) and the harmonized record in one chart; the slider is a curtain:
   left of it you see the raw splice, right of it the harmonized record. Uses only numbers returned by GET /harmonized/series. */
(function () {
  "use strict";
  const EF = window.EquiFire;
  if (!EF) return;
  const { getJSON, esc } = EF;
  const NS = "http://www.w3.org/2000/svg";
  let box = null, token = 0, retries = 0;

  function fmt(v) { return v >= 1e6 ? (v / 1e6).toFixed(1) + " M" : v >= 1e3 ? (v / 1e3).toFixed(0) + " k" : String(Math.round(v)); }

  function build(host) {
    box = document.createElement("div");
    box.id = "hz-compare"; box.className = "hz-compare";
    box.innerHTML = `<h3 class="hz-h">Before / after harmonization</h3>` +
      `<p class="small muted">Drag the slider: left of it the raw splice (MODIS to 2011, VIIRS from 2012), right of it the harmonized record. The step at 2012 is the instrument change, not a change in fires.</p>` +
      `<div class="hzc-chart"></div><label class="hzc-slider"><span>Raw</span><input id="hzc-range" type="range" min="0" max="100" value="50" aria-label="Before and after harmonization"><span>Harmonized</span></label>` +
      `<p id="hzc-jump" class="chart-info" aria-live="polite"></p>`;
    host.insertAdjacentElement("afterend", box);
    box.querySelector("#hzc-range").addEventListener("input", draw);
  }

  let data = null;
  function pick(id) { return (data.series.find((s) => s.id === id) || {}).values || []; }

  function draw() {
    if (!box || !data) return;
    const periods = data.periods, raw = pick("raw_splice"), har = pick("harmonized");
    const W = 640, H = 260, L = 52, R = 12, T = 12, B = 28;
    const all = raw.concat(har).filter((v) => v != null && isFinite(v));
    if (!all.length) { box.querySelector(".hzc-chart").textContent = "No data for this selection."; return; }
    const ymax = Math.max(...all) * 1.08, n = periods.length;
    const x = (i) => L + (W - L - R) * (i / Math.max(1, n - 1)), y = (v) => T + (H - T - B) * (1 - v / ymax);
    const path = (vals) => { let d = "", pen = false; vals.forEach((v, i) => { if (v == null) { pen = false; return; } d += (pen ? "L" : "M") + x(i).toFixed(1) + "," + y(v).toFixed(1); pen = true; }); return d; };
    const pct = +box.querySelector("#hzc-range").value / 100, cut = L + (W - L - R) * pct;
    let ticks = "";
    for (let k = 0; k <= 4; k++) { const v = ymax * k / 4 / 1.08 * 1.08; ticks += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="currentColor" opacity=".12"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end" font-size="11" fill="currentColor">${fmt(v)}</text>`; }
    const step = periods.findIndex((p) => +p === 2012);
    const evx = step >= 0 ? x(step) : null;
    const yrs = periods.map((p, i) => (i % 5 === 0 ? `<text x="${x(i)}" y="${H - 8}" text-anchor="middle" font-size="11" fill="currentColor">${p}</text>` : "")).join("");
    box.querySelector(".hzc-chart").innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Raw splice versus harmonized record" style="color:inherit">` +
      `<defs><clipPath id="hzc-l"><rect x="0" y="0" width="${cut}" height="${H}"/></clipPath><clipPath id="hzc-r"><rect x="${cut}" y="0" width="${W - cut}" height="${H}"/></clipPath></defs>` +
      ticks + yrs + (evx ? `<line x1="${evx}" x2="${evx}" y1="${T}" y2="${H - B}" stroke="currentColor" stroke-dasharray="4 3" opacity=".5"/><text x="${evx + 4}" y="${T + 10}" font-size="11" fill="currentColor">2012 VIIRS starts</text>` : "") +
      `<path d="${path(raw)}" fill="none" stroke="#d93025" stroke-width="2.4" clip-path="url(#hzc-l)"/>` +
      `<path d="${path(har)}" fill="none" stroke="#1a73e8" stroke-width="2.4" clip-path="url(#hzc-r)"/>` +
      `<line x1="${cut}" x2="${cut}" y1="${T}" y2="${H - B}" stroke="currentColor" stroke-width="2"/></svg>`;
    const i12 = periods.findIndex((p) => +p === 2012), i11 = i12 - 1;
    const jump = (vals) => (vals[i11] > 0 && vals[i12] > 0 ? vals[i12] / vals[i11] : null);
    const jr = jump(raw), jh = jump(har);
    box.querySelector("#hzc-jump").textContent = i12 > 0 && jr && jh ? `Step 2011 → 2012: raw ×${jr.toFixed(2)}, harmonized ×${jh.toFixed(2)} (${data.measure_label || data.measure}, ${data.region_label || data.region}).` : "";
  }

  async function load() {
    const my = ++token;
    const region = (document.getElementById("hz-region") || {}).value, measure = (document.getElementById("hz-measure") || {}).value;
    if (!region || !measure) { if (retries++ < 20) setTimeout(load, 700); return; }
    retries = 0;
    try {
      const d = await getJSON("/harmonized/series", { region, measure, resolution: "year" });
      if (my !== token) return;
      data = d; draw();
    } catch (e) { if (box) box.querySelector(".hzc-chart").textContent = "Data unavailable."; }
  }

  function init() {
    const host = document.getElementById("hz-verdict");
    if (!host) return false;
    if (!box) {
      build(host);
      ["hz-region", "hz-measure"].forEach((id) => { const s = document.getElementById(id); if (s) s.addEventListener("change", load); });
      const sec = host.closest("details"); if (sec) sec.addEventListener("toggle", () => { if (sec.open) load(); });
    }
    load();
    return true;
  }
  let tries = 0;
  const iv = setInterval(() => { if (init() || ++tries > 40) clearInterval(iv); }, 500);
})();
