/* Class badge next to the quick-location chips: current-or-latest-complete month vs the median of the same calendar month in other years
   (GET /harmonized/series, harmonized cell-days). Only locations with a matching harmonized region get a badge; nothing is invented for the others. */
(function () {
  "use strict";
  const EF = window.EquiFire;
  const bar = document.getElementById("map-quickbar");
  if (!EF || !bar) return;
  const REGION = { Amazon: "amazon_arc", Australia: "australia_north_savanna" };
  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const RULE = "Ranking of this month's harmonized cell-days among the same calendar month in other years: lowest 20 % Quiet, middle 60 % Typical, top 20 % Busy, top 5 % and at least 1.5x the median Exceptional. Needs 8 other years.";
  function classify(vals, periods) {
    let i = vals.length - 1; while (i >= 0 && (vals[i] == null || !isFinite(vals[i]))) i--;
    if (i < 0) return null;
    const [y, m] = periods[i].split("-").map(Number);
    const same = vals.filter((v, k) => v != null && isFinite(v) && k !== i && +periods[k].split("-")[1] === m);
    if (same.length < 8) return null;
    const v = vals[i], s = same.slice().sort((a, b) => a - b), med = s.length % 2 ? s[(s.length - 1) / 2] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2, ratio = med > 0 ? v / med : null;
    const pct = (same.filter((x) => x < v).length + 0.5 * same.filter((x) => x === v).length) / same.length;
    const c = pct < 0.2 ? "Quiet" : pct < 0.8 ? "Typical" : pct < 0.95 ? "Busy" : (ratio != null && ratio >= 1.5 ? "Exceptional" : "Busy");
    return { cls: c, label: `${MON[m - 1]} ${y}`, v, med, ratio, last: periods[periods.length - 1] };
  }
  bar.querySelectorAll(".preset-chip").forEach((chip) => {
    const rid = REGION[chip.dataset.label];
    if (!rid) return;
    const b = document.createElement("span");
    b.className = "qc-badge"; b.hidden = true; chip.insertAdjacentElement("afterend", b);
    EF.getJSON("/harmonized/series", { region: rid, measure: "cell_days", resolution: "month" }).then((d) => {
      const s = (d.series.find((x) => x.id === "harmonized") || {}).values;
      const r = s && classify(s, d.periods); if (!r) return;
      const now = new Date().toISOString().slice(0, 7);
      b.textContent = `${r.cls} · ${r.label}`; b.dataset.cls = r.cls.toLowerCase().replace(" ", "-"); b.hidden = false;
      b.title = `${r.label}: ${Math.round(r.v)} cell-days vs median ${Math.round(r.med)}${r.ratio != null ? " (" + r.ratio.toFixed(1) + "x)" : ""}. ${r.last < now ? "The record ends in " + r.last + ", so this is the latest month available, not the current one. " : ""}${RULE}`;
    }).catch(() => {});
  });
})();
