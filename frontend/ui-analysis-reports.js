/* Export the displayed analysis, its tables and charts without running it again. */
(function () {
  "use strict";
  const EF = window.EquiFire;
  if (!EF) return;
  const $ = (id) => document.getElementById(id);
  const pl = () => document.documentElement.lang === "pl";
  const tr = (a, b) => pl() ? a : b;
  const clean = (s) => String(s || "").replace(/\s+/g, " ").trim();
  const text = (node) => {
    if (!node) return "";
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT), parts = [];
    let leaf;
    while ((leaf = walker.nextNode())) if (!leaf.parentElement.closest("[hidden], svg")) parts.push(leaf.textContent);
    return clean(parts.join(" "));
  };
  const has = (id) => !!clean($(id)?.textContent);
  const shown = (id) => $(id) && !$(id).hidden;
  const entries = new Map();
  const defs = [
    { id: "live-results", title: ["Monitoring pożarów", "Fire monitoring"], result: "spots-container", ready: () => $("spots-container")?.hasAttribute("aria-busy") && $("spots-container").getAttribute("aria-busy") !== "true" && !shown("error") },
    { id: "results", title: ["Prognoza ML + fizyka", "ML + physics forecast"], result: "sim-result", ready: () => !!EF.sim.data && EF.sim.frames.length > 0 && !EF.sim.running },
    { id: "history", title: ["Historia aktywności", "Activity history"], result: "hist-body", ready: () => shown("hist-body") && !has("hist-status"), exclude: "#hz-section" },
    { id: "hz-section", title: ["Zharmonizowany zapis wieloletni", "Harmonized historical record"], result: "hz-body", ready: () => shown("hz-body") && !has("hz-status") && !shown("hz-error") },
    { id: "alerts", title: ["Alerty aktywności", "Activity alerts"], result: "alerts-list", ready: () => $("alerts-geojson")?.href.includes("/live/") },
    { id: "replay", title: ["Odtworzenie prognozy", "Forecast replay"], result: "rp-body", ready: () => shown("rp-body") && !has("rp-status") && !shown("rp-error") },
    { id: "fx-sec-nextobs", title: ["Kolejna obserwacja satelitarna", "Next satellite observation"], ready: () => !!window.EquiFireFX?.nextobs && !!$("fx-tl") },
    ...[
      ["blind", "fx-blind-body", "Luki obserwacji", "Observation gaps"],
      ["scen", "fx-scen-body", "Scenariusze pogodowe", "Weather scenarios"],
      ["expo", "fx-expo-body", "Ekspozycja w czasie", "Exposure over time"],
      ["drone", "fx-dr-out", "Trasa rozpoznania dronem", "Drone reconnaissance route"],
      ["carbon", "fx-cb-out", "Szacowane emisje CO₂", "Estimated CO₂ emissions"],
      ["dbnet", "fx-db-out", "Geometria Direct Broadcast", "Direct Broadcast geometry"],
      ["illum", "fx-il-out", "Geometria oświetlenia", "Illumination geometry"],
      ["firebreak", "fx-fb-out", "Scenariusz pasa ochronnego", "Firebreak scenario"],
      ["montecarlo", "fx-mc-out", "Częstość w zespole symulacji", "Simulation ensemble frequency"],
      ["nowcast", "fx-nc-out", "Nowcasting następnego przelotu", "Next-pass nowcast"],
      ["riskdiag", "risk-result", "Kalibracja dziennego ryzyka", "Daily risk calibration"],
    ].map(([suffix, result, a, b]) => ({ id: `fx-sec-${suffix}`, result, title: [a, b], ready: () => has(result) })),
  ];

  function available(rec) {
    return !rec.busy && !rec.stale && rec.def.ready() &&
      !rec.root.querySelector('.fx-progress, [aria-busy="true"]') &&
      ![...rec.root.querySelectorAll('.fx-err, [role="alert"]')].some((el) => !hidden(el, rec.root) && clean(el.textContent));
  }
  function refresh(rec) {
    const label = rec.busy ? tr("Tworzenie PDF…", "Generating PDF…") : tr("Pobierz PDF widoku", "Download view PDF");
    if (rec.label.textContent !== label) rec.label.textContent = label;
    const disabled = !available(rec);
    if (rec.button.disabled !== disabled) rec.button.disabled = disabled;
    const hint = disabled ? tr("Najpierw ukończ analizę dla bieżących ustawień.", "First finish the analysis for the current settings.") :
      tr("Zapisuje wynik, tabele i wykresy tego widoku. Bez ponownego liczenia.", "Saves the results, tables and charts in this view. No recalculation.");
    if (rec.button.title !== hint) rec.button.title = hint;
  }
  function hidden(node, root) {
    for (let p = node; p && p !== root.parentElement; p = p.parentElement) {
      if (p.hidden || getComputedStyle(p).display === "none") return true;
    }
    return false;
  }
  function tableBlock(table) {
    const all = [...table.rows];
    if (!all.length) return null;
    const row = (r) => [...r.cells].flatMap((c) => [text(c), ...Array(Math.max(0, Math.min(15, c.colSpan - 1))).fill("")]).slice(0, 16);
    const headed = !!all[0].querySelector("th");
    const width = Math.min(16, Math.max(...all.map((r) => row(r).length)));
    if (!width) return null;
    const pad = (r) => [...r, ...Array(Math.max(0, width - r.length)).fill("")].slice(0, width);
    return { kind: "table", headers: pad(headed ? row(all[0]) : Array.from({ length: width }, (_, i) => tr("Kolumna ", "Column ") + (i + 1))),
      rows: all.slice(headed ? 1 : 0, (headed ? 1 : 0) + 500).map((r) => pad(row(r))),
      caption: all.length > 501 ? tr("Pierwsze 500 wierszy widoku; pełne dane są w eksporcie źródłowym.", "First 500 displayed rows; use the source export for the full data.") : "" };
  }
  function collect(root, exclude) {
    const blocks = [];
    function walk(node) {
      if (node.nodeType === Node.TEXT_NODE) {
        const value = clean(node.textContent);
        if (value) blocks.push({ kind: "paragraph", text: value });
        return;
      }
      if (node.nodeType !== Node.ELEMENT_NODE || hidden(node, root) || node.matches(".analysis-report-actions, script, style, input, select, textarea, .downloads, .fx-progress, [data-icon]")) return;
      if (exclude && node.matches(exclude)) return;
      if (node.id === "spots-container" && node.querySelector(".spot-item")) {
        const items = [...node.querySelectorAll(".spot-item")];
        blocks.push({ kind: "table", headers: [tr("Współrzędne", "Coordinates"), tr("Ostatnia obserwacja", "Last observed"), tr("Dane detekcji", "Detection data")],
          rows: items.slice(0, 500).map((item) => [text(item.querySelector(".spot-coord")), text(item.querySelector(".spot-time")), text(item.querySelector(".spot-details"))]) });
        if (items.length > 500) blocks.push({ kind: "paragraph", text: tr("Pierwsze 500 pozycji listy detekcji.", "First 500 entries in the detection list.") });
        return;
      }
      if (node.matches("button, a.btn-download, .fx-actions")) {
        if (node.matches(".spot-item, .alert-item")) blocks.push({ kind: "paragraph", text: text(node) });
        return;
      }
      if (node.matches("svg")) {
        const bounds = node.getBoundingClientRect();
        if (bounds.width >= 100 && bounds.height >= 50) blocks.push({ kind: "figure", source: node, caption: node.getAttribute("aria-label") || node.closest(".chart-block")?.querySelector("h3")?.textContent || tr("Wykres z widoku analizy", "Chart from the analysis view") });
        return;
      }
      if (node.matches("table")) { const b = tableBlock(node); if (b) { blocks.push(b); if (b.caption) blocks.push({ kind: "paragraph", text: b.caption }); } return; }
      if (node.matches("h2, h3, h4, summary, .fx-h")) { if (text(node)) blocks.push({ kind: "heading", text: text(node) }); return; }
      if (node.matches("label")) return; // parameters are included separately with their values
      if (node.matches("p, li, .kpi, .stat-card, .rp-cutoff, .fx-kpis > div, .fx-ctrs > div")) {
        if (text(node)) blocks.push({ kind: "paragraph", text: text(node) });
        return;
      }
      [...node.childNodes].forEach(walk);
    }
    walk(root);
    return blocks;
  }

  async function chartPNG(svg) {
    const box = svg.getBoundingClientRect(), clone = svg.cloneNode(true);
    clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    const styles = ["fill", "fill-opacity", "stroke", "stroke-width", "stroke-opacity", "stroke-dasharray", "font-family", "font-size", "font-weight", "text-anchor", "opacity", "color"];
    const targets = [clone, ...clone.querySelectorAll("*")];
    [svg, ...svg.querySelectorAll("*")].forEach((source, i) => {
      const target = targets[i];
      if (!target) return;
      const css = getComputedStyle(source);
      for (const key of styles) target.style.setProperty(key, css.getPropertyValue(key));
      for (const a of [...target.attributes]) {
        if (/^on/i.test(a.name) || ((a.name === "href" || a.name === "xlink:href") && !a.value.startsWith("#"))) target.removeAttribute(a.name);
      }
    });
    clone.querySelectorAll("script, foreignObject, image, use").forEach((n) => n.remove());
    clone.setAttribute("width", box.width); clone.setAttribute("height", box.height);
    let background = "#fff";
    for (let node = svg.parentElement; node; node = node.parentElement) {
      const c = getComputedStyle(node).backgroundColor;
      if (c && c !== "transparent" && c !== "rgba(0, 0, 0, 0)") { background = c; break; }
    }
    const blob = new Blob([new XMLSerializer().serializeToString(clone)], { type: "image/svg+xml" });
    const url = URL.createObjectURL(blob);
    try {
      const img = new Image(); img.src = url; await img.decode();
      const scale = Math.min(2, 1200 / box.width, 1200 / box.height);
      const canvas = document.createElement("canvas"); canvas.width = Math.round(box.width * scale); canvas.height = Math.round(box.height * scale);
      const ctx = canvas.getContext("2d"); ctx.fillStyle = background; ctx.fillRect(0, 0, canvas.width, canvas.height); ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      return canvas.toDataURL("image/png");
    } finally { URL.revokeObjectURL(url); }
  }

  function context(rec) {
    const rows = [{ label: tr("Zakres raportu", "Report scope"), value: tr("Stan widoku w chwili pobrania; bez ponownego obliczania analizy. Mapa podkładowa nie jest częścią eksportu.", "View at the time of download; analysis is not recalculated. The basemap is not included.") }];
    rec.root.querySelectorAll("label").forEach((label) => {
      const input = label.querySelector("select, input:not([type=checkbox]):not([type=range])");
      if (!input || hidden(input, rec.root)) return;
      const value = input.tagName === "SELECT" ? input.selectedOptions[0]?.textContent : input.value;
      if (value) rows.push({ label: clean(label.querySelector("span")?.textContent || input.name || input.id), value: clean(value) });
    });
    if (rec.def.id === "results") {
      const d = EF.sim.data, f = EF.sim.frames[EF.sim.idx];
      const q = d.query || {};
      if (q.lat != null && q.lon != null) rows.push({ label: tr("Punkt obliczonej prognozy", "Computed forecast location"), value: `${Number(q.lat).toFixed(5)}, ${Number(q.lon).toFixed(5)}` });
      if (q.start) rows.push({ label: tr("Początek prognozy UTC", "Forecast start UTC"), value: String(q.start) });
      if (q.hours != null) rows.push({ label: tr("Horyzont i krok", "Horizon and step"), value: `${q.simulated_hours ?? q.hours} h · ${q.step_min ?? "—"} min` });
      rows.push({ label: tr("Klatka osi czasu", "Timeline frame"), value: `${f?.time_utc || f?.time || ""} · ${f?.t_hours ?? ""} h` });
    }
    if (["live-results", "history"].includes(rec.def.id)) rows.push({ label: tr("Bieżący obszar widoku W,S,E,N", "Current view area W,S,E,N"), value: (EF.state.bbox || []).join(", ") });
    return rows.slice(0, 40);
  }
  function forecastTimeline() {
    const frames = EF.sim.data.frames || [];
    return { kind: "table", headers: ["UTC", "h", "km²", tr("Wiatr km/h", "Wind km/h"), tr("Temperatura °C", "Temperature °C"), "RH %"],
      rows: frames.slice(0, 500).map((f) => [f.time_utc || f.time || "", f.t_hours, f.burned_km2, f.weather?.wind_kmh, f.weather?.temp_c, f.weather?.rh].map((v) => v == null ? "—" : String(v))) };
  }

  async function download(rec) {
    if (!available(rec)) return;
    // Read all text and query values before awaiting chart rendering or the network.
    const title = rec.def.title[pl() ? 0 : 1];
    const payload = { title, language: pl() ? "pl" : "en", generated_at: new Date().toISOString(), context: context(rec), blocks: collect(rec.root, rec.def.exclude) };
    if (rec.def.id === "results") payload.blocks.push({ kind: "heading", text: tr("Pełna fizyczna oś czasu", "Complete physical timeline") }, forecastTimeline());
    rec.busy = true; refresh(rec);
    rec.status.textContent = tr("Przygotowywanie raportu…", "Preparing report…");
    try {
      let figures = 0;
      // Starting every chart promise in this synchronous map captures all SVG clones,
      // dimensions and styles before the event loop can advance the timeline or reveal.
      const pending = payload.blocks.map((block) => {
        if (block.kind === "figure") {
          if (figures >= 8) return { kind: "paragraph", text: tr("Dodatkowy wykres dostępny w aplikacji.", "Additional chart is available in the app.") };
          figures++;
          const caption = clean(block.caption).slice(0, 500);
          return chartPNG(block.source).then((png) => ({ kind: "figure", png, caption })).catch(() =>
            ({ kind: "paragraph", text: tr("Wykres nie został dołączony; sprawdź go w aplikacji.", "Chart could not be included; view it in the app.") }));
        }
        return block;
      });
      const blocks = await Promise.all(pending);
      payload.blocks = blocks.slice(0, 119);
      if (blocks.length > 119) payload.blocks.push({ kind: "paragraph", text: tr("Raport obejmuje pierwsze 119 elementów widoku. Pełne dane są dostępne w aplikacji i eksportach źródłowych.", "Report includes the first 119 view elements. Full data is available in the app and source exports.") });
      const res = await fetch(`${EF.API}/reports/pdf`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      if (!res.ok) {
        let detail = ""; try { const data = await res.json(); detail = typeof data.detail === "string" ? data.detail : ""; } catch (_) { /* non-JSON service error */ }
        throw new Error(detail || `HTTP ${res.status}`);
      }
      const blob = await res.blob();
      if (!blob.type.includes("application/pdf")) throw new Error(tr("Serwer nie zwrócił pliku PDF.", "The server did not return a PDF."));
      const url = URL.createObjectURL(blob), a = document.createElement("a");
      a.href = url; a.download = `firespot-${rec.def.id}-${payload.generated_at.slice(0, 10)}.pdf`;
      document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 60000);
      rec.status.textContent = tr("PDF gotowy — rozpoczęto pobieranie.", "PDF ready — download started.");
    } catch (err) {
      rec.status.textContent = tr("Nie udało się utworzyć PDF. Spróbuj ponownie: ", "PDF could not be generated. Retry: ") + err.message;
    } finally { rec.busy = false; refresh(rec); }
  }

  function attach(def) {
    const root = $(def.id);
    if (!root || entries.get(def.id)?.root === root) return;
    const wrapper = document.createElement("div"); wrapper.className = "analysis-report-actions";
    const button = document.createElement("button"); button.type = "button"; button.className = "btn-download analysis-report-button";
    button.innerHTML = EF.icon("download") + "<span></span>";
    const status = document.createElement("p"); status.className = "analysis-report-status"; status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
    wrapper.append(button, status);
    (root.matches("details") ? root.querySelector(".fx-sec-body") || root : root).appendChild(wrapper);
    const rec = { root, def, button, label: button.querySelector("span"), status, busy: false, stale: false };
    entries.set(def.id, rec);
    button.addEventListener("click", () => download(rec));
    rec.changeHandler = (event) => {
      if (!event.target.matches("input:not([type=checkbox]), select")) return;
      // These controls redraw already-computed data, rather than request another analysis.
      if (/^(rp-(day|s-|m-)|fx-rose-|fx-expo-show|hist-(year|metric))/.test(event.target.id)) return;
      if (def.id.startsWith("fx-sec-") || (def.id === "replay" && /^(rp-incident|rp-lat|rp-lon|rp-date)$/.test(event.target.id)) || def.id === "alerts") { rec.stale = true; refresh(rec); }
    };
    root.addEventListener("change", rec.changeHandler);
    rec.observer = new MutationObserver((mutations) => {
      if (mutations.every((m) => wrapper.contains(m.target))) return;
      const result = def.result ? $(def.result) : root.querySelector(".fx-sec-body > .fx-sec-body");
      if (result && mutations.some((m) => m.type === "childList" && result === m.target)) rec.stale = false;
      if (root.querySelector(".fx-progress")) rec.stale = true;
      if (def.id === "alerts") {
        // renderAlerts updates the download link only after a complete successful render.
        // Loading/error also replace the list, but deliberately do not refresh that link.
        if (mutations.some((m) => m.target === $("alerts-list") && m.type === "childList")) rec.stale = true;
        if (mutations.some((m) => m.target === $("alerts-geojson") && m.attributeName === "href")) rec.stale = false;
      }
      if (def.id === "replay" && mutations.some((m) => m.target === $("rp-body") && m.attributeName === "hidden") && shown("rp-body")) rec.stale = false;
      refresh(rec);
    });
    rec.observer.observe(root, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ["hidden", "aria-busy", "href"] });
    refresh(rec); EF.hydrateIcons();
  }
  function scan() { defs.forEach(attach); entries.forEach(refresh); }
  EF.onModeChange(scan);
  ["load", "reset", "point"].forEach((name) => EF.on(name, () => {
    entries.forEach((rec) => {
      if (name === "point" && rec.def.id.startsWith("fx-sec-")) rec.stale = true;
      refresh(rec);
    });
  }));
  document.addEventListener("equifire:langchange", () => entries.forEach(refresh));
  // Replay and harmonization create their markup lazily when their tab first opens.
  new MutationObserver(() => {
    if (defs.some((def) => $(def.id) && (!entries.has(def.id) || !entries.get(def.id).button.isConnected))) {
      for (const def of defs) if (entries.has(def.id) && !entries.get(def.id).button.isConnected) {
        const old = entries.get(def.id); old.observer.disconnect(); old.root.removeEventListener("change", old.changeHandler); entries.delete(def.id);
      }
      scan();
    }
  }).observe($("panel"), { childList: true, subtree: true });
  scan();
})();
