/* Forecast extras, part 2: blind-spot overlay (GET /blindspots).
   Two layers on one canvas under the fire markers:
   - observation tiles (1 deg): insufficient_observation = GREY HATCH (never green), good_observation = faint outline only;
   - fire cells (0.05 deg): fire_detected = solid red, recently_burned_not_seen = orange hatch,
     no_fire_detected_good_observation = muted outline.
   "No fire detected" (well observed) and "insufficient observation" (unknown) are always kept apart. */
(function () {
  "use strict";
  const EF = window.EquiFire, FX = window.EquiFireFX;
  if (!EF || !FX) return;
  const { map, esc } = EF;
  const $ = (id) => document.getElementById(id);

  const sec = FX.section({ id: "blind", title: "Blind spots on the map", icon: "eye-off", order: 20 });
  sec.body.innerHTML = `<p class="fx-note">Hatched areas were poorly observed. No detection there does not mean no fire.</p>` +
    `<div class="fx-actions"><button type="button" id="fx-blind-toggle" class="fx-switch" aria-pressed="false"><span class="fx-box" aria-hidden="true"></span>Show blind-spot overlay</button>` +
    `<button type="button" id="fx-blind-update" class="tool-btn">${FX.ic("refresh-cw")}<span>Update for this view</span></button></div>` +
    `<label class="check"><input id="fx-blind-inspect" type="checkbox" checked> Click the map to inspect tiles (off: select fires)</label>` +
    `<div id="fx-blind-slot"></div><p id="fx-blind-status" class="fx-note" role="status"></p><div id="fx-blind-body"></div>`;
  const slot = $("fx-blind-slot"), job = FX.job(slot), bodyEl = $("fx-blind-body"), statusEl = $("fx-blind-status");

  const COL = { fire: "#b42318", fireLine: "#7a1010", burn: "#E69F00", burnLine: "#b06800", grey: "#5d6a73", good: "#2c7a73" };
  const st = { on: false, data: null, tiles: [], cells: new Map(), cellList: [], bbox: null, sel: null, seq: 0, popup: null };

  // ------------------------------------------------------------------ canvas layer
  const pane = map.createPane("fxBlind");
  pane.id = "fx-blind-pane";
  pane.style.zIndex = 390;
  const cv = document.createElement("canvas");
  cv.setAttribute("aria-hidden", "true");
  pane.appendChild(cv);
  const pat = {};
  function hatch(key, color, dir, gap, lw, bg) {
    if (pat[key]) return pat[key];
    const c = document.createElement("canvas");
    c.width = c.height = gap;
    const g = c.getContext("2d");
    if (bg) { g.fillStyle = bg; g.fillRect(0, 0, gap, gap); }
    g.strokeStyle = color; g.lineWidth = lw; g.beginPath();
    const o = dir === "up" ? [[0, gap, gap, 0], [-gap / 2, gap / 2, gap / 2, -gap / 2], [gap / 2, gap * 1.5, gap * 1.5, gap / 2]] : [[0, 0, gap, gap], [-gap / 2, gap / 2, gap / 2, gap * 1.5], [gap / 2, -gap / 2, gap * 1.5, gap / 2]];
    o.forEach((l) => { g.moveTo(l[0], l[1]); g.lineTo(l[2], l[3]); });
    g.stroke();
    return (pat[key] = cv.getContext("2d").createPattern(c, "repeat"));
  }

  function redraw() {
    if (!st.on || !st.data) { const g = cv.getContext("2d"); g.clearRect(0, 0, cv.width, cv.height); return; }
    const size = map.getSize(), dpr = window.devicePixelRatio || 1;
    L.DomUtil.setPosition(cv, map.containerPointToLayerPoint([0, 0]));
    if (cv.width !== Math.round(size.x * dpr) || cv.height !== Math.round(size.y * dpr)) {
      cv.width = Math.round(size.x * dpr); cv.height = Math.round(size.y * dpr);
      cv.style.width = size.x + "px"; cv.style.height = size.y + "px";
    }
    const g = cv.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, size.x, size.y);
    cv.style.opacity = "1";
    const rect = (b) => {
      const a = map.latLngToContainerPoint([b.n, b.w]), z = map.latLngToContainerPoint([b.s, b.e]);
      return [a.x, a.y, Math.max(1.5, z.x - a.x), Math.max(1.5, z.y - a.y)];
    };
    const vis = (r) => r[0] < size.x && r[1] < size.y && r[0] + r[2] > 0 && r[1] + r[3] > 0;
    // observation tiles
    st.tiles.forEach((t) => {
      const r = rect(t);
      if (!vis(r)) return;
      if (t.cls === "insufficient_observation") {
        g.fillStyle = hatch("grey", "#4f5b64", "down", 8, 1.6, "rgba(120,132,140,.16)");
        g.fillRect(r[0], r[1], r[2], r[3]);
        g.strokeStyle = "rgba(79,91,100,.75)"; g.lineWidth = 1; g.setLineDash([5, 3]);
        g.strokeRect(r[0] + .5, r[1] + .5, r[2] - 1, r[3] - 1); g.setLineDash([]);
      } else {
        g.strokeStyle = "rgba(44,122,115,.35)"; g.lineWidth = 1; g.setLineDash([1, 4]);
        g.strokeRect(r[0] + .5, r[1] + .5, r[2] - 1, r[3] - 1); g.setLineDash([]);
      }
    });
    // fire layer
    ["no_fire_detected_good_observation", "recently_burned_not_seen", "fire_detected"].forEach((cls) => {
      st.cellList.forEach((c) => {
        if (c.cls !== cls) return;
        const r = rect(c);
        if (!vis(r)) return;
        if (cls === "fire_detected") {
          g.fillStyle = "rgba(180,35,24,.82)"; g.fillRect(r[0], r[1], r[2], r[3]);
          g.strokeStyle = COL.fireLine; g.lineWidth = 1; g.strokeRect(r[0] + .5, r[1] + .5, Math.max(1, r[2] - 1), Math.max(1, r[3] - 1));
        } else if (cls === "recently_burned_not_seen") {
          g.fillStyle = hatch("orange", "#b06800", "up", 5, 1.7, "rgba(230,159,0,.45)"); g.fillRect(r[0], r[1], r[2], r[3]);
          g.strokeStyle = COL.burnLine; g.lineWidth = 1.2; g.strokeRect(r[0] + .5, r[1] + .5, Math.max(1, r[2] - 1), Math.max(1, r[3] - 1));
        } else {
          g.fillStyle = "rgba(44,122,115,.10)"; g.fillRect(r[0], r[1], r[2], r[3]);
          g.strokeStyle = "rgba(44,122,115,.55)"; g.lineWidth = 1; g.strokeRect(r[0] + .5, r[1] + .5, Math.max(1, r[2] - 1), Math.max(1, r[3] - 1));
        }
      });
    });
    if (st.sel) {
      const r = rect(st.sel);
      g.strokeStyle = "#1d2b34"; g.lineWidth = 3; g.strokeRect(r[0] + 1.5, r[1] + 1.5, r[2] - 3, r[3] - 3);
      g.strokeStyle = "#fff"; g.lineWidth = 1; g.strokeRect(r[0] + 3, r[1] + 3, r[2] - 6, r[3] - 6);
    }
  }
  map.on("moveend zoomend resize", () => { redraw(); markStale(); });
  map.on("zoomstart", () => { cv.style.opacity = ".35"; });

  // ------------------------------------------------------------------ data
  function bounds(f) {
    const ring = f.geometry.coordinates[0];
    let w = 1e9, e = -1e9, s = 1e9, n = -1e9;
    ring.forEach(([x, y]) => { w = Math.min(w, x); e = Math.max(e, x); s = Math.min(s, y); n = Math.max(n, y); });
    return { w, e, s, n };
  }
  function ingest(d) {
    st.tiles = []; st.cellList = []; st.cells = new Map();
    (d.features || []).forEach((f) => {
      if (!f.geometry || f.geometry.type !== "Polygon") return;
      const p = f.properties || {}, b = bounds(f);
      if (p.layer === "observation") st.tiles.push({ ...b, cls: p.class, p });
      else if (p.layer === "fire") {
        const c = { ...b, cls: p.class, p };
        st.cellList.push(c);
        st.cells.set(`${Math.round(b.w / 0.05)},${Math.round(b.s / 0.05)}`, c);
      }
    });
  }
  function viewBox() {
    const b = map.getBounds();
    let w = b.getWest(), e = b.getEast(), s = Math.max(-90, b.getSouth()), n = Math.min(90, b.getNorth()), clipped = false;
    if (e - w > 10) { const c = (e + w) / 2; w = c - 5; e = c + 5; clipped = true; }
    if (n - s > 10) { const c = (n + s) / 2; s = c - 5; n = c + 5; clipped = true; }
    const wrap = (x) => Math.max(-180, Math.min(180, x));
    return { bbox: [wrap(w), s, wrap(e), n].map((v) => +v.toFixed(3)), clipped };
  }
  function covers(box) {
    const b = map.getBounds().getCenter();
    return st.bbox && b.lng >= st.bbox[0] && b.lng <= st.bbox[2] && b.lat >= st.bbox[1] && b.lat <= st.bbox[3];
  }
  function markStale() {
    if (!st.on || !st.data) return;
    const stale = !covers();
    $("fx-blind-update").classList.toggle("active", stale);
    if (stale) statusEl.textContent = "Map moved. Use Update for this view.";
  }

  async function load() {
    const seq = ++st.seq;
    const v = viewBox();
    bodyEl.innerHTML = ""; statusEl.textContent = "";
    try {
      const d = await job.run("Loading observation quality…", `Area ${v.bbox.join(", ")}`,
        (signal) => FX.fetchJSON("/blindspots", { bbox: v.bbox.join(",") }, { signal }));
      if (seq !== st.seq) return;
      if (d === null) { statusEl.textContent = "Cancelled."; return; }
      st.data = d; st.bbox = d.query && d.query.bbox || v.bbox; st.sel = null;
      ingest(d);
      redraw();
      render(d, v.clipped);
      mapLegend();
    } catch (e) {
      if (seq !== st.seq) return;
      statusEl.innerHTML = `<span class="fx-err" role="alert" style="display:block">Blind-spot data unavailable: ${esc(e.message)}</span>`;
    }
  }

  // ------------------------------------------------------------------ rendering
  function sw(kind, size) {
    const s = size || 18, id = "fxp" + kind + Math.round(Math.random() * 1e6);
    const defs = { ins: `<pattern id="${id}" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="6" height="6" fill="#dfe4e7"/><line x1="0" y1="0" x2="0" y2="6" stroke="#4f5b64" stroke-width="2"/></pattern>`,
      burn: `<pattern id="${id}" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(-45)"><rect width="5" height="5" fill="#f4d58a"/><line x1="0" y1="0" x2="0" y2="5" stroke="#b06800" stroke-width="2"/></pattern>` }[kind] || "";
    const body = {
      fire: `<rect x="1" y="1" width="${s - 2}" height="${s - 2}" fill="${COL.fire}" stroke="${COL.fireLine}" stroke-width="1.5"/>`,
      burn: `<rect x="1" y="1" width="${s - 2}" height="${s - 2}" fill="url(#${id})" stroke="${COL.burnLine}" stroke-width="1.5"/>`,
      good: `<rect x="1" y="1" width="${s - 2}" height="${s - 2}" fill="rgba(44,122,115,.12)" stroke="${COL.good}" stroke-width="1.5"/>`,
      ins: `<rect x="1" y="1" width="${s - 2}" height="${s - 2}" fill="url(#${id})" stroke="#4f5b64" stroke-width="1.2" stroke-dasharray="4 2"/>`,
    }[kind];
    return `<svg width="${s}" height="${s}" viewBox="0 0 ${s} ${s}" aria-hidden="true" focusable="false"><defs>${defs}</defs>${body}</svg>`;
  }
  const LABEL = {
    fire_detected: ["fire", "Fire detected"],
    recently_burned_not_seen: ["burn", "Recently burned, not seen now"],
    no_fire_detected_good_observation: ["good", "No fire detected (well observed)"],
    insufficient_observation: ["ins", "Insufficient observation (unknown)"],
  };
  function render(d, clipped) {
    const c = d.counts || {};
    const q = d.query || {};
    let h = `<div class="fx-counts" aria-label="Cell counts">` +
      [["fire_detected", c.fire_detected], ["recently_burned_not_seen", c.recently_burned_not_seen], ["no_fire_detected_good_observation", c.no_fire_detected_good_observation], ["insufficient_observation", c.insufficient_observation]]
        .map(([k, v]) => `<div class="fx-count">${sw(LABEL[k][0], 22)}<div><b>${v == null ? "n/a" : v.toLocaleString("en")}</b><span>${esc(LABEL[k][1])}</span></div></div>`).join("") +
      `</div><p class="fx-note">0.05 degree cells (about 5 km), last ${esc(q.window_hours || 24)} h, as of ${esc(String(q.time_utc || "").replace("T", " ").replace("Z", " UTC"))}.` +
      (c.previously_detected_now_quiet_good_observation ? ` ${c.previously_detected_now_quiet_good_observation.toLocaleString("en")} quiet cells burned earlier in the last ${q.history_hours || 72} h.` : "") + `</p>`;
    if (clipped) h += `<p class="fx-note">View is over 10 x 10 degrees: only the centre was loaded. Zoom in for the rest.</p>`;
    const L2 = d.legend || {};
    h += `<details class="fx-details"><summary>What the colours mean</summary><ul class="fx-legend-list">` +
      `<li>${sw("good", 18)}<span><b>No fire detected (well observed).</b> ${esc(L2.no_fire_detected_good_observation || "")}</span></li>` +
      `<li>${sw("ins", 18)}<span><b>Insufficient observation (unknown, not "no fire").</b> ${esc(L2.insufficient_observation || "")}</span></li>` +
      `<li>${sw("burn", 18)}<span><b>Recently burned, not seen now.</b> ${esc(L2.recently_burned_not_seen || "")}</span></li>` +
      `<li>${sw("fire", 18)}<span><b>Fire detected.</b> ${esc(L2.fire_detected || "")}</span></li></ul></details>`;
    const ins = st.tiles.filter((t) => t.cls === "insufficient_observation");
    h += `<div class="fx-h">Tile details</div><p class="fx-note">Insufficient observation: ${ins.length} of ${st.tiles.length} tiles. Click one to inspect.</p>`;
    h += `<div class="fx-actions" id="fx-tile-list">${ins.slice(0, 12).map((t, i) => `<button type="button" class="tool-btn small-btn" data-i="${i}">${sw("ins", 14)}<span>${t.p.centre ? `${(+t.p.centre[1]).toFixed(1)}, ${(+t.p.centre[0]).toFixed(1)}` : "tile"}</span></button>`).join("")}</div>`;
    h += `<div id="fx-tilebox"></div>`;
    h += `<details class="fx-details"><summary>How this is computed</summary>${(d.notes || []).map((n) => `<p class="fx-note">${esc(n)}</p>`).join("")}<p class="fx-note">Pass geometry from orbital elements; cloud from the Open-Meteo model.</p></details>`;
    const sf = (d.feeds || []).filter((f) => f.stale || f.error);
    if (sf.length) h += FX.warn(`Detection feed delayed or unavailable: ${sf.map((f) => esc(f.feed)).join(", ")}. Fire cells may be incomplete.`);
    bodyEl.innerHTML = h;
    statusEl.textContent = "";
    $("fx-blind-update").classList.remove("active");
    bodyEl.querySelectorAll("#fx-tile-list button").forEach((b) => b.addEventListener("click", () => {
      const t = ins[+b.dataset.i];
      map.fitBounds([[t.s, t.w], [t.n, t.e]], { maxZoom: 8, padding: [30, 30] });
      inspect(L.latLng((t.s + t.n) / 2, (t.w + t.e) / 2));
    }));
    EF.hydrateIcons(bodyEl);
  }

  function inspect(ll) {
    if (!st.data) return;
    const tile = st.tiles.find((t) => ll.lng >= t.w && ll.lng < t.e && ll.lat >= t.s && ll.lat < t.n);
    const cell = st.cells.get(`${Math.floor(ll.lng / 0.05 + 1e-9)},${Math.floor(ll.lat / 0.05 + 1e-9)}`);
    st.sel = tile || cell || null;
    redraw();
    let h = "";
    if (tile) {
      const p = tile.p, ins = tile.cls === "insufficient_observation";
      h += `<h4>${sw(ins ? "ins" : "good", 14)} ${ins ? "Insufficient observation" : "Good observation"}, 1 degree tile</h4>`;
      h += ins ? `<p>No detection here means <b>unknown</b>, not "no fire".</p>` : `<p>A usable pass saw this tile recently. No detection suggests no active fire, but is not a guarantee.</p>`;
      if ((p.reasons || []).length) h += `<ul>${p.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>`;
      h += `<p>${p.n_good_passes} of ${p.n_passes_total} passes in the window were usable${p.hours_since_last_good_pass != null ? `; last usable pass ${FX.hours(p.hours_since_last_good_pass)} ago` : ""}.</p>`;
      const ps = (p.passes || []).slice(0, 8);
      if (ps.length) h += `<details class="fx-details"><summary>Passes considered (${(p.passes || []).length})</summary><ul>${ps.map((q) => `<li>${esc(q.satellite)} ${FX.utc(Date.parse(q.time_utc), true)}, scan ${FX.num(q.scan_angle_deg, 0)} deg, cloud ${q.cloud_pct == null ? "?" : q.cloud_pct + "%"} (${esc(q.visibility)})</li>`).join("")}</ul></details>`;
    } else h += `<p>This point is outside the loaded observation tiles.</p>`;
    if (cell) {
      const p = cell.p;
      h += `<h4>${sw(LABEL[cell.cls][0], 14)} ${esc(LABEL[cell.cls][1])}, 0.05 degree cell</h4><p>` +
        (cell.cls === "fire_detected" ? `${p.n_detections} detection${p.n_detections === 1 ? "" : "s"}, ${FX.num(p.frp_mw, 1)} MW, last ${FX.hours(p.age_hours)} ago.` :
          cell.cls === "recently_burned_not_seen" ? `Burned earlier (${p.n_detections_earlier} detection${p.n_detections_earlier === 1 ? "" : "s"}, last ${FX.hours(p.age_hours)} ago), poorly observed since: it may still be burning unseen.` :
            `Burned earlier (${p.n_detections_earlier} detections, last ${FX.hours(p.age_hours)} ago) and quiet in a well-observed window.`) + `</p>`;
    } else if (tile) h += `<p class="muted">No fire cell here: no detection in this 0.05 degree cell during the window.</p>`;
    const box = $("fx-tilebox");
    if (box) { box.innerHTML = `<div class="fx-tilebox" tabindex="-1">${h}</div>`; }
    if (!sec.open) sec.el.open = true;
    if (st.popup) st.popup.remove();
    const short = tile ? `${tile.cls === "insufficient_observation" ? "Insufficient observation (unknown)" : "Good observation"}${cell ? " · " + LABEL[cell.cls][1] : ""}` : "Outside loaded area";
    st.popup = L.popup({ className: "fx-popup", maxWidth: 240 }).setLatLng(ll).setContent(`<b>${esc(short)}</b>${tile && tile.p.reasons && tile.p.reasons[0] ? `<br>${esc(tile.p.reasons[0])}` : ""}<br><span class="muted">Details in the panel.</span>`).openOn(map);
  }
  // Clicks are taken in the capture phase: the app's own fire canvas sits above this pane and would swallow them.
  map.getContainer().addEventListener("click", (e) => {
    if (EF.state.isDrawing || !st.on || !st.data || !$("fx-blind-inspect").checked) return;
    if (e.target.closest && e.target.closest(".leaflet-control, .leaflet-popup, .leaflet-marker-icon, #playback, #floating-bar, #fleet-panel, .map-legend")) return;
    e.stopPropagation();
    inspect(map.mouseEventToLatLng(e));
  }, true);

  // legend items appended to the existing map legend bar while the overlay is on
  function mapLegend() {
    document.querySelectorAll(".map-legend .fx-lg").forEach((n) => n.remove());
    if (!st.on) return;
    const bar = document.querySelector(".map-legend");
    if (!bar) return;
    ["fire_detected", "recently_burned_not_seen", "no_fire_detected_good_observation", "insufficient_observation"].forEach((k) => {
      const sp = document.createElement("span");
      sp.className = "fx-lg";
      sp.innerHTML = `${sw(LABEL[k][0], 14)}${esc(LABEL[k][1])}`;
      bar.appendChild(sp);
    });
  }

  // ------------------------------------------------------------------ toggle
  function setOn(on) {
    st.on = on;
    $("fx-blind-toggle").setAttribute("aria-pressed", String(on));
    document.getElementById("viewport-container").classList.toggle("fx-blind-on", on);
    if (on) {
      if (!st.data) load(); else { redraw(); mapLegend(); markStale(); }
    } else {
      job.cancel(); st.seq++; redraw();
      mapLegend();
      if (st.popup) { st.popup.remove(); st.popup = null; }
    }
  }
  $("fx-blind-toggle").addEventListener("click", () => setOn(!st.on));
  $("fx-blind-update").addEventListener("click", () => { if (!st.on) setOn(true); else load(); });
  sec.onOpen(() => { /* loaded on demand via the switch */ });
  let resume = false;
  FX.onModeChange((mode) => {
    if (mode !== "date") { if (st.on) { resume = true; setOn(false); } }
    else if (resume) { resume = false; setOn(true); }
  });
})();
