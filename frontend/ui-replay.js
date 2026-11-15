/* EquiFire Replay workspace: issued forecast first, then reveal what satellites saw afterwards.
   Add-on module; relies on window.EquiFire (map, getJSON, esc, icon, hydrateIcons, switchMode, onModeChange). */
(function () {
  "use strict";
  const EF = window.EquiFire;
  const root = document.getElementById("replay");
  const tab = document.getElementById("tab-replay");
  if (!EF || !root || !tab) return;
  const { map, getJSON, esc, icon } = EF;

  // Okabe-Ito classes (colour-blind safe) + non-colour cues: hit solid, near hit light, miss thick outline,
  // false alarm dashed outline, unverifiable hatch.
  const CLASS_COLOURS = { hit: "#0072B2", near_hit: "#56B4E9", miss: "#E69F00", false_alarm: "#CC79A7", unverifiable: "#999999" };
  const CLASS_LABELS = { hit: "Hit", near_hit: "Near hit (within 5 km)", miss: "Miss", false_alarm: "False alarm", unverifiable: "Unverifiable (cloud)" };
  const RAMP = ["#00204d", "#31446b", "#666970", "#958f78", "#cbba69", "#ffea46"]; // cividis, probability

  const st = { incidents: [], inc: null, data: null, day: 1, method: "ml_physics", mode: "strict", revealed: false, seq: 0, busy: false, loadedList: false };
  const layers = { group: L.layerGroup(), issued: null, reveal: null, init: null };
  let built = false;

  const pct = (v, d) => (v == null || !isFinite(v) ? "n/a" : (v * 100).toFixed(d == null ? 0 : d) + "%");
  const num = (v) => (v == null ? "n/a" : Number(v).toLocaleString("en-US"));
  const utc = (iso) => (iso ? String(iso).replace("T", " ").replace(/:\d\dZ$/, " UTC").replace(/Z$/, " UTC") : "n/a");
  const lerp = (a, b, t) => Math.round(a + (b - a) * t);
  function ramp(t) {
    t = Math.max(0, Math.min(1, t));
    const x = t * (RAMP.length - 1), i = Math.min(RAMP.length - 2, Math.floor(x)), f = x - i;
    const a = RAMP[i], b = RAMP[i + 1];
    const c = [1, 3, 5].map((k) => lerp(parseInt(a.substr(k, 2), 16), parseInt(b.substr(k, 2), 16), f));
    return "#" + c.map((v) => v.toString(16).padStart(2, "0")).join("");
  }
  const el = (id) => document.getElementById(id);

  // ---------------------------------------------------------------- DOM
  function build() {
    if (built) return;
    built = true;
    root.innerHTML = `
      <svg class="rp-defs" width="0" height="0" aria-hidden="true" focusable="false">
        <defs><pattern id="rp-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="6" height="6" fill="#ffffff" fill-opacity="0.55"/><line x1="0" y1="0" x2="0" y2="6" stroke="#6b6b6b" stroke-width="2.2"/></pattern></defs>
      </svg>
      <p class="muted small">Forecast as issued, using only data up to the cutoff. Reveal compares it with later satellite detections.</p>
      <div class="rp-controls">
        <label class="input-field"><span>Incident</span><select id="rp-incident" name="rp-incident"><option>Loading incidents…</option></select></label>
        <div id="rp-custom" class="rp-custom" hidden>
          <label class="input-field"><span>Latitude</span><input id="rp-lat" type="number" min="-90" max="90" step="any" inputmode="decimal" value="54.77" name="rp-lat" autocomplete="off"></label>
          <label class="input-field"><span>Longitude</span><input id="rp-lon" type="number" min="-180" max="180" step="any" inputmode="decimal" value="-101.87" name="rp-lon" autocomplete="off"></label>
          <label class="input-field"><span>Date (UTC)</span><input id="rp-date" type="date" value="2025-05-28" name="rp-date" autocomplete="off"></label>
          <p class="muted small rp-custom-note">Computed on demand, up to 1 min. Dates before 2024: ML only.</p>
          <button id="rp-run" class="tool-btn" type="button">${icon("play")}<span>Run replay</span></button>
        </div>
      </div>
      <div id="rp-incident-info" class="rp-incident-info" aria-live="polite"></div>
      <div id="rp-error" class="alert-box error" role="alert" hidden></div>
      <p id="rp-status" class="status-msg" role="status" aria-live="polite"></p>

      <div id="rp-body" hidden>
        <div class="rp-cutoff" id="rp-cutoff"></div>

        <div class="rp-row">
          <div class="rp-field">
            <span class="rp-label" id="rp-method-label">Forecast method</span>
            <div class="seg" role="group" aria-labelledby="rp-method-label">
              <button id="rp-m-comb" class="seg-btn active" type="button" aria-pressed="true">ML + physics</button>
              <button id="rp-m-ml" class="seg-btn" type="button" aria-pressed="false">ML only</button>
            </div>
          </div>
          <div class="rp-field rp-field-day">
            <label class="rp-label" for="rp-day">Forecast day <output id="rp-day-out" for="rp-day">1</output></label>
            <input id="rp-day" type="range" min="1" max="3" step="1" value="1" name="rp-day" aria-describedby="rp-day-date">
            <div class="rp-ticks" aria-hidden="true"><span>Day 1</span><span>Day 2</span><span>Day 3</span></div>
            <span id="rp-day-date" class="rp-day-date muted small"></span>
          </div>
        </div>

        <div class="rp-reveal-row">
          <button id="rp-reveal" class="btn-primary" type="button" aria-pressed="false">${icon("eye")}<span>Reveal what happened</span></button>
        </div>
        <p id="rp-issued-note" class="rp-note small muted"></p>

        <div id="rp-revealed" hidden>
          <div class="rp-row rp-row-tight">
            <div class="rp-field">
              <span class="rp-label" id="rp-mode-label">Matching rule</span>
              <div class="seg" role="group" aria-labelledby="rp-mode-label">
                <button id="rp-s-strict" class="seg-btn active" type="button" aria-pressed="true">Strict (same cell)</button>
                <button id="rp-s-tol" class="seg-btn" type="button" aria-pressed="false">Within 5 km</button>
              </div>
            </div>
          </div>
          <div id="rp-summary" class="rp-summary" aria-live="polite"></div>
          <h3>Per day, ${"<span id='rp-mode-name'>strict</span>"} matching</h3>
          <div class="rp-table-wrap"><table class="rp-table" id="rp-counts"></table></div>
          <p id="rp-counts-note" class="rp-note small muted"></p>
          <h3>Score against the ring baseline</h3>
          <div class="rp-table-wrap"><table class="rp-table" id="rp-scores"></table></div>
          <p class="rp-note small muted">Ring baseline: marks every cell within about 5 km of the burning area as burned. Not a forecast model. Within 5 km is an overlap score, not accuracy.</p>
          <div id="rp-verdict" class="rp-note small"></div>
          <div id="rp-cloud" class="rp-cloud"></div>
          <p class="rp-note small muted"><strong>One event is not a benchmark.</strong> Scores are uncalibrated; see the model card for the walk-forward benchmark.</p>
        </div>

        <details class="methodology rp-details" id="rp-method-details"><summary>Method and information cutoff</summary><div id="rp-method" class="small"></div></details>
        <details class="methodology rp-details"><summary>No-leakage audit</summary><div id="rp-leak" class="small"></div></details>
        <details class="methodology rp-details"><summary>Limitations of this replay</summary><ul id="rp-limits" class="rp-list small"></ul></details>
      </div>`;
    // Map legend overlay (kept in the map area so it stays visible beside the cells).
    const vp = document.getElementById("viewport-container");
    const lg = document.createElement("div");
    lg.id = "rp-legend";
    lg.className = "map-legend rp-legend";
    lg.setAttribute("aria-label", "Replay legend");
    lg.hidden = true;
    const phone = window.matchMedia("(max-width:800px)");
    const placeLegend = () => (phone.matches ? root : vp).appendChild(lg);
    phone.addEventListener("change", placeLegend);
    placeLegend();

    el("rp-incident").addEventListener("change", onIncidentChange);
    el("rp-run").addEventListener("click", () => run());
    el("rp-m-comb").addEventListener("click", () => setMethod("ml_physics"));
    el("rp-m-ml").addEventListener("click", () => setMethod("ml"));
    el("rp-s-strict").addEventListener("click", () => setMode("strict"));
    el("rp-s-tol").addEventListener("click", () => setMode("tolerant"));
    el("rp-day").addEventListener("input", () => { st.day = Number(el("rp-day").value); renderAll(); });
    el("rp-reveal").addEventListener("click", toggleReveal);
    EF.hydrateIcons(root);
  }

  // ---------------------------------------------------------------- data loading
  async function loadIncidents() {
    if (st.loadedList) return;
    try {
      const r = await getJSON("/replay/incidents");
      st.incidents = r.incidents || [];
      st.loadedList = true;
      const sel = el("rp-incident");
      sel.innerHTML = st.incidents.map((i) => `<option value="${esc(i.id)}">${esc(i.name)}</option>`).join("") +
        `<option value="custom">Custom location and date…</option>`;
      const def = st.incidents.find((i) => i.id === "flin_flon_2025") || st.incidents[0];
      if (def) sel.value = def.id;
      onIncidentChange();
    } catch (e) {
      showErr(e.message || String(e));
      el("rp-incident").innerHTML = `<option>Incidents unavailable</option>`;
    }
  }

  function showErr(msg) { const b = el("rp-error"); b.textContent = msg || ""; b.hidden = !msg; }
  function setStatus(msg) { el("rp-status").textContent = msg || ""; }

  function onIncidentChange() {
    const v = el("rp-incident").value;
    const custom = v === "custom";
    el("rp-custom").hidden = !custom;
    const info = el("rp-incident-info");
    if (custom) { info.innerHTML = ""; return; }
    const i = st.incidents.find((x) => x.id === v);
    if (!i) return;
    st.inc = i;
    info.innerHTML = `<p class="small"><strong>${esc(i.biome)}, forecast issued ${esc(i.date)}.</strong> ${esc(i.description)}</p><details class="rp-why"><summary class="small">Why this incident was chosen</summary><p class="small muted">${esc(i.why_chosen)}</p></details>`;
    run();
  }

  async function run() {
    const seq = ++st.seq;
    const v = el("rp-incident").value;
    const params = v === "custom"
      ? { lat: el("rp-lat").value, lon: el("rp-lon").value, date: el("rp-date").value, days: 3 }
      : { incident: v };
    if (v === "custom" && (el("rp-lat").value === "" || el("rp-lon").value === "" || !el("rp-date").value)) { showErr("Enter latitude, longitude and date."); return; }
    showErr(""); clearLayers(); el("rp-body").hidden = true; el("rp-legend").hidden = true;
    st.revealed = false; st.data = null;
    setStatus("Loading saved replay…");
    el("rp-run").disabled = true;
    try {
      let d;
      try {
        d = await getJSON("/replay/run", { ...params, cached_only: 1 });
      } catch (e) {
        if (seq !== st.seq) return;
        if (!/no saved replay|404|not found/i.test(e.message || "") && v !== "custom") throw e;
        setStatus("Not saved. Computing: usually about 50 seconds, up to 3 minutes in harder cases…");
        d = await getJSON("/replay/run", params);
      }
      if (seq !== st.seq) return;
      st.data = d;
      st.day = 1;
      const avail = (d.issued && d.issued.methods_available) || [];
      st.method = d.method && d.method.used === "ml" ? "ml" : "ml_physics";
      el("rp-m-comb").disabled = !avail.includes("ml_physics");
      el("rp-day").value = "1";
      setStatus("");
      el("rp-body").hidden = false;
      renderAll(true);
    } catch (e) {
      if (seq !== st.seq) return;
      setStatus("");
      showErr("Replay unavailable: " + (e.message || e));
    } finally {
      if (seq === st.seq) el("rp-run").disabled = false;
    }
  }

  // ---------------------------------------------------------------- state changes
  function setMethod(m) { st.method = m; renderAll(); }
  function setMode(m) { st.mode = m; renderAll(); }
  function toggleReveal() {
    st.revealed = !st.revealed;
    renderAll();
    if (st.revealed) { const s = el("rp-revealed"); if (s && s.scrollIntoView && window.innerWidth <= 800) s.scrollIntoView({ block: "start", behavior: "smooth" }); }
  }

  // ---------------------------------------------------------------- map layers
  function clearLayers() {
    layers.group.clearLayers();
    if (map.hasLayer(layers.group)) map.removeLayer(layers.group);
  }

  function cellTooltip(f, kind, extra) { return `<strong>${esc(kind)}</strong>${extra ? "<br>" + extra : ""}`; }

  function drawMap(fit) {
    const d = st.data;
    if (!d) return;
    layers.group.clearLayers();
    if (!map.hasLayer(layers.group)) layers.group.addTo(map);
    const day = st.day, comb = st.method === "ml_physics";
    const feats = (d.issued.geojson.features || []).filter((f) => f.properties.day === day);
    const pk = comb ? "combined_probability" : "ml_probability", sk = comb ? "selected" : "ml_selected";
    const pmax = Math.max(0.001, ...((d.issued.geojson.features || []).map((f) => f.properties[pk] || 0)));
    const dim = st.revealed;
    const issued = L.geoJSON({ type: "FeatureCollection", features: feats }, {
      style: (f) => {
        const p = f.properties, sel = !!p[sk];
        return {
          fillColor: ramp(Math.sqrt((p[pk] || 0) / pmax)), fillOpacity: dim ? 0.12 : (sel ? 0.7 : 0.4),
          color: dim ? "#9aa7ae" : (sel ? "#1d2b34" : "#ffffff"), weight: dim ? 0.6 : (sel ? 2 : 0.5), opacity: dim ? 0.6 : 1,
        };
      },
      onEachFeature: (f, l) => {
        const p = f.properties;
        l.bindTooltip(cellTooltip(f, p[sk] ? "Flagged as likely to burn" : "Not flagged", `${comb ? "Combined" : "ML"} score ${pct(p[pk], 1)} (uncalibrated)`), { sticky: true });
      },
    });
    issued.addTo(layers.group);
    const init = L.geoJSON(d.reveal && d.reveal.burning_at_init || { type: "FeatureCollection", features: [] }, {
      style: { fillColor: "#000000", fillOpacity: 0.55, color: "#000000", weight: 1 }, interactive: false,
    });
    init.addTo(layers.group);
    layers.issued = issued; layers.init = init;

    if (st.revealed && d.reveal && d.reveal.geojson) {
      const rv = L.geoJSON(d.reveal.geojson, {
        filter: (f) => classOf(f) !== "none",
        style: (f) => classStyle(classOf(f)),
        onEachFeature: (f, l) => {
          const c = classOf(f);
          l.bindTooltip(cellTooltip(f, CLASS_LABELS[c] || c, `First observed on day ${f.properties.observed_first_day != null ? esc(f.properties.observed_first_day) : "n/a"}, cumulative to day ${day}`), { sticky: true });
        },
      });
      rv.addTo(layers.group);
      layers.reveal = rv;
    } else layers.reveal = null;
    if (fit) {
      map.invalidateSize();
      try {
        const b = L.featureGroup([layers.issued, layers.init]).getBounds();
        if (b.isValid()) map.fitBounds(b, { padding: [40, 40], maxZoom: 9, animate: false });
      } catch (e) { /* ignore */ }
    }
  }

  function classOf(f) {
    const c = f.properties.classes && f.properties.classes[st.method];
    const arr = c && c[st.mode];
    return (arr && arr[st.day - 1]) || "none";
  }
  function classStyle(c) {
    const col = CLASS_COLOURS[c] || "#999";
    switch (c) {
      case "hit": return { fillColor: col, fillOpacity: 0.85, color: col, weight: 1, opacity: 1 };
      case "near_hit": return { fillColor: col, fillOpacity: 0.5, color: col, weight: 1, opacity: 1 };
      case "miss": return { fill: false, fillOpacity: 0, color: col, weight: 4, opacity: 1 };
      case "false_alarm": return { fill: false, fillOpacity: 0, color: col, weight: 2.5, opacity: 1, dashArray: "6 4" };
      case "unverifiable": return { fillColor: "url(#rp-hatch)", fillOpacity: 1, color: col, weight: 1.5, opacity: 1 };
      default: return { fill: false, stroke: false };
    }
  }

  // ---------------------------------------------------------------- legend
  function swatch(kind) {
    const c = CLASS_COLOURS[kind];
    const inner = {
      hit: `<rect x="1" y="1" width="14" height="14" fill="${c}" stroke="${c}" stroke-width="1"/>`,
      near_hit: `<rect x="1" y="1" width="14" height="14" fill="${c}" fill-opacity=".5" stroke="${c}" stroke-width="1"/>`,
      miss: `<rect x="2" y="2" width="12" height="12" fill="none" stroke="${c}" stroke-width="3.5"/>`,
      false_alarm: `<rect x="1.5" y="1.5" width="13" height="13" fill="none" stroke="${c}" stroke-width="2.2" stroke-dasharray="3.5 2.5"/>`,
      unverifiable: `<rect x="1" y="1" width="14" height="14" fill="url(#rp-hatch)" stroke="${c}" stroke-width="1.5"/>`,
    }[kind];
    return `<svg class="rp-sw" width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false">${inner}</svg>`;
  }
  function renderLegend() {
    const lg = el("rp-legend");
    lg.hidden = !st.data;
    if (!st.data) return;
    const ramp5 = RAMP.map((c) => c).join(",");
    let h = `<span><svg class="rp-sw" width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><rect x="1" y="1" width="14" height="14" fill="#000" fill-opacity=".55" stroke="#000"/></svg>Burning at forecast time</span>`;
    if (!st.revealed) {
      h += `<span><i class="rp-ramp" style="background:linear-gradient(90deg,${ramp5})"></i>Score low to high (uncalibrated)</span>`;
      h += `<span><svg class="rp-sw" width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><rect x="2" y="2" width="12" height="12" fill="#666970" fill-opacity=".6" stroke="#1d2b34" stroke-width="2"/></svg>Dark outline: flagged cell</span>`;
    } else {
      const kinds = st.mode === "tolerant" ? ["hit", "near_hit", "miss", "false_alarm", "unverifiable"] : ["hit", "miss", "false_alarm", "unverifiable"];
      h += kinds.map((k) => `<span>${swatch(k)}${esc(CLASS_LABELS[k])}</span>`).join("");
    }
    lg.innerHTML = h;
  }

  // ---------------------------------------------------------------- panel
  function renderAll(fit) {
    const d = st.data;
    if (!d) return;
    const comb = st.method === "ml_physics";
    el("rp-m-comb").classList.toggle("active", comb);
    el("rp-m-comb").setAttribute("aria-pressed", String(comb));
    el("rp-m-ml").classList.toggle("active", !comb);
    el("rp-m-ml").setAttribute("aria-pressed", String(!comb));
    el("rp-s-strict").classList.toggle("active", st.mode === "strict");
    el("rp-s-strict").setAttribute("aria-pressed", String(st.mode === "strict"));
    el("rp-s-tol").classList.toggle("active", st.mode === "tolerant");
    el("rp-s-tol").setAttribute("aria-pressed", String(st.mode === "tolerant"));
    el("rp-mode-name").textContent = st.mode === "strict" ? "strict (same cell)" : "within-5 km";
    const rb = el("rp-reveal");
    rb.setAttribute("aria-pressed", String(st.revealed));
    rb.querySelector("span").textContent = st.revealed ? "Hide what happened" : "Reveal what happened";
    rb.firstElementChild && (rb.firstElementChild.outerHTML = icon(st.revealed ? "eye-off" : "eye"));
    el("rp-revealed").hidden = !st.revealed;
    el("rp-day-out").textContent = st.day;
    const pd = d.reveal && d.reveal.per_day && d.reveal.per_day[st.day - 1];
    el("rp-day-date").textContent = pd ? "Forecast for " + pd.date + " (cumulative from the cutoff)" : "";

    const init = d.init || {};
    el("rp-cutoff").innerHTML = `${icon("clock")}<div><strong>Information cutoff: ${esc(utc(init.information_cutoff_utc))}</strong><br><span class="muted small">${esc(init.semantics || "")}</span></div>`;
    const sel = d.issued.per_day_selected && d.issued.per_day_selected[comb ? "comb" : "ml"];
    el("rp-issued-note").textContent = st.revealed ? "" :
      `Day ${st.day}${sel ? `: ${sel[st.day - 1]} cells flagged as newly burning` : ""}. Black cells were already burning at the cutoff.`;
    renderMethod();
    if (st.revealed) renderReveal(); else { el("rp-counts").innerHTML = ""; }
    renderLegend();
    drawMap(!!fit);
  }

  function renderMethod() {
    const d = st.data, m = d.method || {}, mm = m.model || {}, ph = m.physics || {};
    const label = m.used === "ml" ? "ML only (no ML + physics blender exists for this date)" : "ML + physics, experimental blend";
    const ws = (ph.weather_sources || []).map((w) => `${esc(w.model || w.api)}`).join(", ");
    el("rp-method").innerHTML = `
      <p><span class="method-tag">${esc(label)}</span></p>
      <p>${esc(m.description || "")}</p>
      <ul class="rp-list">
        ${mm.ml_model ? `<li>ML model: ${esc(mm.ml_model)}, trained on years before ${esc(mm.ml_training_before_year)}.${mm.blender ? ` Blender: ${esc(mm.blender)} (fit on ${esc(mm.fit_year)}).` : ""}</li>` : ""}
        ${m.scoring ? `<li>Scoring: ${esc(m.scoring)}</li>` : ""}
        ${ph.fuel ? `<li>Physics: fuel ${esc(ph.fuel)}, terrain ${esc(ph.terrain || "n/a")}${ws ? `, weather ${ws}` : ""}${ph.weather_fallback ? " (fallback weather used)" : ""}.</li>` : ""}
        <li>Shading shows relative, uncalibrated scores, not chances.</li>
      </ul>
      ${(d.issued.warnings || []).length ? `<p class="muted">${(d.issued.warnings || []).map(esc).join(" ")}</p>` : ""}`;
    const la = d.leakage_audit || {};
    el("rp-leak").innerHTML = `<ul class="rp-list">
      ${la.fire_observations ? `<li><strong>Fire observations:</strong> ${esc(la.fire_observations)}</li>` : ""}
      ${la.models ? `<li><strong>Models:</strong> ${esc(la.models)}</li>` : ""}
      ${la.weather ? `<li><strong>Weather:</strong> ${esc(la.weather)}</li>` : ""}
      <li><strong>Truth used in the forecast:</strong> ${la.truth_used_in_forecast === false ? "no" : esc(la.truth_used_in_forecast)}</li>
      <li><strong>Reveal computed after the forecast:</strong> ${la.reveal_computed_after_forecast ? "yes" : "n/a"}</li></ul>`;
    el("rp-limits").innerHTML = (d.limitations || []).map((t) => `<li>${esc(t)}</li>`).join("");
  }

  function countsFor(m, mode) {
    const s = m && m[mode];
    if (!s) return null;
    return mode === "strict"
      ? { hits: s.hits, misses: s.misses, fa: s.false_alarms, p: s.precision, r: s.recall, iou: s.iou }
      : { hits: s.observed_matched, misses: s.misses, fa: s.false_alarms, p: s.precision, r: s.recall, iou: s.iou };
  }

  function renderReveal() {
    const d = st.data, rv = d.reveal || {}, days = rv.per_day || [], M = st.method, mode = st.mode;
    // counts
    let h = `<thead><tr><th scope="col">Day</th><th scope="col" class="num">Hits</th><th scope="col" class="num">Misses</th><th scope="col" class="num">False alarms</th><th scope="col" class="num">Precision</th><th scope="col" class="num">Recall</th></tr></thead><tbody>`;
    days.forEach((p) => {
      const c = p.scored ? countsFor(p.methods && p.methods[M], mode) : null;
      const cur = p.day === st.day ? ' aria-current="true" class="rp-cur"' : "";
      h += c ? `<tr${cur}><th scope="row">${p.day}<span class="rp-date"> ${esc(p.date.slice(5))}</span></th><td class="num">${num(c.hits)}</td><td class="num">${num(c.misses)}</td><td class="num">${num(c.fa)}</td><td class="num">${pct(c.p)}</td><td class="num">${pct(c.r)}</td></tr>`
        : `<tr${cur}><th scope="row">${p.day}</th><td class="num muted" colspan="5">not scored (no archive data for this day)</td></tr>`;
    });
    el("rp-counts").innerHTML = h + "</tbody>";
    el("rp-counts-note").textContent = mode === "strict"
      ? "Hit: newly burning cell predicted in the same 0.05 degree cell (about 5.5 km). Cumulative by day."
      : "Hits: observed cells with a flagged cell within one grid cell. False alarms: flagged cells with no observed fire within one grid cell.";
    // scores incl. ring baseline
    let s = `<thead><tr><th scope="col">Day</th><th scope="col" class="num">Forecast IoU</th><th scope="col" class="num">Forecast within 5 km</th><th scope="col" class="num">Ring IoU</th><th scope="col" class="num">Ring within 5 km</th></tr></thead><tbody>`;
    days.forEach((p) => {
      const m = p.methods && p.methods[M], rg = p.methods && p.methods.baseline_ring;
      const cur = p.day === st.day ? ' aria-current="true" class="rp-cur"' : "";
      if (!p.scored || !m) { s += `<tr${cur}><th scope="row">${p.day}</th><td class="num muted" colspan="4">not scored</td></tr>`; return; }
      s += `<tr${cur}><th scope="row">${p.day}</th><td class="num">${pct(m.strict && m.strict.iou, 1)}</td><td class="num">${pct(m.tolerant && m.tolerant.within_5km_score, 1)}</td><td class="num">${pct(rg && rg.strict && rg.strict.iou, 1)}</td><td class="num">${pct(rg && rg.tolerant && rg.tolerant.within_5km_score, 1)}</td></tr>`;
    });
    el("rp-scores").innerHTML = s + "</tbody>";
    // verdict for current day from the returned numbers
    const cp = days[st.day - 1], cm = cp && cp.methods && cp.methods[M], crg = cp && cp.methods && cp.methods.baseline_ring;
    let vtxt = "";
    if (cp && cp.scored && cm && crg) {
      const a = cm.strict.iou, b = crg.strict.iou, a5 = cm.tolerant.within_5km_score, b5 = crg.tolerant.within_5km_score;
      const cmp = (x, y) => (x > y ? "above" : x < y ? "below" : "equal to");
      vtxt = `Day ${cp.day}: strict IoU ${pct(a, 1)} is ${cmp(a, b)} the ring baseline (${pct(b, 1)}); within-5 km score ${pct(a5, 1)} is ${cmp(a5, b5)} it (${pct(b5, 1)}).`;
    }
    el("rp-verdict").textContent = vtxt;
    // reveal summary for current day
    const c = cp && cp.scored ? countsFor(cm, mode) : null;
    el("rp-summary").innerHTML = c ? `<div class="kpi-grid rp-kpis">
        <div class="kpi"><span class="kpi-val">${num(c.hits)}</span><span class="kpi-lbl">hits, day ${cp.day}</span></div>
        <div class="kpi"><span class="kpi-val">${num(c.misses)}</span><span class="kpi-lbl">misses</span></div>
        <div class="kpi"><span class="kpi-val">${num(c.fa)}</span><span class="kpi-lbl">false alarms</span></div></div>` : "";
    // cloud caveat
    renderCloud(cp);
  }

  function renderCloud(cp) {
    const rv = st.data.reveal || {}, oq = rv.observation_quality;
    const box = el("rp-cloud");
    if (!oq || oq.available === false || !cp) { box.innerHTML = `<p class="small muted">Cloud and overpass proxy not available for this replay: absence of a detection is not proof of no fire.</p>`; return; }
    const q = (oq.per_day || []).find((x) => x.day === cp.day);
    const adj = cp.methods && cp.methods[st.method] && cp.methods[st.method].adjusted;
    const uv = adj ? (st.mode === "strict" ? adj.unverifiable_predicted_cells_strict : adj.unverifiable_predicted_cells_tolerant) : null;
    let t = `<h3>Cloud caveat</h3><p class="small">`;
    if (q) t += `Day ${q.day}: modelled cloud ${q.mean_cloud_pct_day_pass != null ? q.mean_cloud_pct_day_pass.toFixed(1) : "n/a"}% (day pass), ${q.mean_cloud_pct_night_pass != null ? q.mean_cloud_pct_night_pass.toFixed(1) : "n/a"}% (night pass). ${pct(q.window_fraction_obscured, 0)} of the window obscured; ${pct(q.window_fraction_unverifiable_cumulative, 0)} unverifiable cumulatively. `;
    if (uv != null) t += `${num(uv)} flagged cell${uv === 1 ? "" : "s"} could not be verified (grey hatch) and ${uv === 1 ? "is" : "are"} excluded from adjusted false alarms. `;
    t += `</p>`;
    if (adj && adj[st.mode] && uv > 0) t += `<p class="small muted">Adjusted (${st.mode === "strict" ? "strict" : "within 5 km"}): precision ${pct(adj[st.mode].precision, 1)}, IoU ${pct(adj[st.mode].iou, 1)}.</p>`;
    t += `<p class="small muted">${esc(oq.source || "")}. Threshold for obscured: ${esc(oq.threshold_pct)}% cloud. ${adj && adj.note ? esc(adj.note) : ""}</p>`;
    box.innerHTML = t;
  }

  // A rectangle selects the custom replay centre; the replay model remains point-based.
  EF.on("area", (area) => {
    if (area.mode !== "replay") return;
    build();
    const date = st.inc && st.inc.date;
    st.seq++; st.data = null; st.revealed = false; st.inc = null;
    clearLayers();
    el("rp-body").hidden = true; el("rp-legend").hidden = true;
    el("rp-incident").value = "custom"; el("rp-custom").hidden = false;
    el("rp-lat").value = area.center.lat.toFixed(5);
    el("rp-lon").value = area.center.lon.toFixed(5);
    if (date) el("rp-date").value = date;
    el("rp-incident-info").innerHTML = "";
    el("rp-run").disabled = false;
    showErr("");
    setStatus("Area set. Replay starts at its centre; choose a date and run it.");
  });

  // ---------------------------------------------------------------- workspace wiring
  tab.addEventListener("click", () => EF.switchMode("replay"));
  EF.onModeChange((mode) => {
    const on = mode === "replay";
    tab.classList.toggle("active", on);
    root.hidden = !on;
    document.body.classList.toggle("mode-replay", on);
    if (on) {
      build();
      const gc = document.getElementById("globe-container");
      if (gc && gc.style.display !== "none" && gc.offsetParent !== null) { const b = document.getElementById("btn-view-map"); if (b) b.click(); }
      loadIncidents();
      if (st.data) { setTimeout(() => { el("rp-legend").hidden = false; renderAll(true); }, 60); }
    } else {
      clearLayers();
      const lg = document.getElementById("rp-legend");
      if (lg) lg.hidden = true;
    }
  });
})();
