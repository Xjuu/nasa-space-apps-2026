/* Headline card (top of the side panel): the main result of the challenge in three lines, with numbers read live from GET /harmonized/series.
   Problem -> solution -> proof. Dismissal is remembered per browser. */
(function () {
  "use strict";
  const EF = window.EquiFire;
  if (!EF) return;
  const KEY = "firespot:headline-closed";
  const panel = document.getElementById("panel");
  const head = panel && panel.querySelector(".panel-header");
  if (!head) return;
  try { if (localStorage.getItem(KEY) === "1") return; } catch (e) { /* storage blocked: keep showing */ }
  const card = document.createElement("section");
  card.id = "headline-card"; card.className = "headline-card"; card.setAttribute("aria-label", "Main result");
  head.insertAdjacentElement("afterend", card);
  let d = null;
  const pl = () => (document.documentElement.lang || "en").startsWith("pl");
  const n = (v, k) => (v == null || !isFinite(v) ? "n/a" : (pl() ? v.toFixed(k).replace(".", ",") : v.toFixed(k)));
  const sg = (v) => (v >= 0 ? "+" : "−") + n(Math.abs(v), 1);
  function render() {
    if (!d) return;
    const raw = d.discontinuity.raw_splice.first_viirs_year_2012_over_2011, har = d.discontinuity.harmonized.first_viirs_year_2012_over_2011;
    const tr = d.trend_2003_2025.raw_splice.trend_pct_per_year, th = d.trend_2003_2025.harmonized.trend_pct_per_year;
    const T = (a, b) => (pl() ? a : b);
    card.innerHTML = `<button type="button" class="hl-x" aria-label="${T("Zamknij", "Close")}">×</button>` +
      `<div class="hl-eyebrow">${T("Wynik główny: harmonizacja MODIS → VIIRS", "Main result: MODIS → VIIRS harmonization")}</div>` +
      `<p><b>${T("Problem.", "Problem.")}</b> ${T(`Po zwykłym sklejeniu archiwów liczba wykryć skacze w 2012 r. ×${n(raw, 2)}, a pozorny trend to ${sg(tr)} %/rok. To zmiana instrumentu, nie pożarów.`, `Splicing the archives makes detections jump ×${n(raw, 2)} in 2012 and the apparent trend ${sg(tr)} %/yr. That is the instrument, not the fires.`)}</p>` +
      `<p><b>${T("Rozwiązanie.", "Solution.")}</b> ${T(`Model przelicza MODIS na ekwiwalent VIIRS: skok spada do ×${n(har, 2)}, trend do ${sg(th)} %/rok (nieistotny statystycznie).`, `A model converts MODIS to a VIIRS-equivalent: the step drops to ×${n(har, 2)} and the trend to ${sg(th)} %/yr (not significant).`)}</p>` +
      `<button type="button" class="hl-go tool-btn">${T("Zobacz dowód w zakładce Historia", "See the proof in the History tab")}</button>`;
    card.querySelector(".hl-x").addEventListener("click", () => { card.remove(); try { localStorage.setItem(KEY, "1"); } catch (e) { /* ignore */ } });
    card.querySelector(".hl-go").addEventListener("click", () => EF.switchMode("history"));
    sync();
  }
  const tab = document.getElementById("tab-history");
  const sync = () => { const b = card.querySelector(".hl-go"); if (b) b.hidden = !!(tab && tab.getAttribute("aria-current") === "page"); };
  if (tab) new MutationObserver(sync).observe(tab, { attributes: true, attributeFilter: ["aria-current"] });
  EF.getJSON("/harmonized/series", { region: "five_countries", measure: "detections", resolution: "year" }).then((r) => { d = r; render(); }).catch(() => card.remove());
  new MutationObserver(render).observe(document.documentElement, { attributes: true, attributeFilter: ["lang"] });
})();
