/* Forecast extras, part 1: shared helpers (window.EquiFireFX), the "Until next pass" section and the incident brief.
   Other parts: ui-forecast-blindspots.js, ui-forecast-scenarios.js, ui-forecast-exposure.js.
   All numbers come from the backend; wording stays honest: experimental, uncalibrated, scenario ranges are not
   confidence intervals, cloud is a visibility indicator. Uses the window.EquiFire bridge from app.js. */
(function () {
  "use strict";
  const EF = window.EquiFire;
  const host = document.getElementById("date-controls");
  if (!EF || !host) return;
  const { esc, icon } = EF;
  const $ = (id) => document.getElementById(id);
  const FX = (window.EquiFireFX = { run: null, win: null, nextobs: null });

  // ------------------------------------------------------------------ small helpers
  const pad = (n) => String(n).padStart(2, "0");
  FX.utc = (ms, withDate) => {
    if (!isFinite(ms)) return "n/a";
    const d = new Date(ms);
    return (withDate ? `${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ` : "") + `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`;
  };
  FX.hours = (h, signed) => (h == null || !isFinite(h) ? "n/a" : `${signed && h >= 0 ? "+" : ""}${Math.abs(h) < 10 && h % 1 ? (+h).toFixed(1) : Math.round(h * 10) / 10} h`);
  FX.num = (v, d) => (v == null || !isFinite(v) ? "n/a" : (+v).toFixed(d == null ? 1 : d));
  const ALIAS = { "triangle-alert": "alert-triangle", "circle-alert": "alert-circle", "external-link": "external-link" };
  FX.ic = (name, cls) => icon(name, cls) || (ALIAS[name] ? icon(ALIAS[name], cls) : "");
  FX.modeHooks = [];
  FX.onModeChange = (fn) => { FX.modeHooks.push(fn); };
  FX.point = () => EF.state.selectedPoint;

  FX.fetchJSON = async function (path, query, o) {
    o = o || {};
    const url = EF.API + path + (query ? "?" + new URLSearchParams(query) : "");
    let res;
    try {
      res = await fetch(url, { method: o.method || "GET", headers: o.body ? { "Content-Type": "application/json" } : undefined,
        body: o.body ? JSON.stringify(o.body) : undefined, signal: o.signal });
    } catch (e) {
      if (e && e.name === "AbortError") throw Object.assign(new Error("Cancelled"), { aborted: true });
      throw new Error("The service is unavailable. Check the connection and try again.");
    }
    const text = await res.text();
    let body = null;
    try { body = text ? JSON.parse(text) : null; } catch (e) { /* not JSON */ }
    if (!res.ok) {
      let detail = body && (body.detail || body.error || body.message);
      if (detail && typeof detail !== "string") detail = JSON.stringify(detail);
      throw new Error(detail || text.slice(0, 200) || `HTTP ${res.status}`);
    }
    if (body === null) throw new Error(`${path}: response is not JSON`);
    return body;
  };

  // Slow-call helper: spinner, elapsed seconds, Cancel. Resolves to null when cancelled or superseded.
  FX.job = function (slot) {
    let ctrl = null, timer = null, node = null;
    function cleanup(mine) {
      if (ctrl !== mine) return;
      clearInterval(timer); if (node) node.remove(); node = null; ctrl = null;
    }
    async function run(label, sub, fn) {
      if (ctrl) ctrl.abort();
      clearInterval(timer); if (node) node.remove();
      const mine = (ctrl = new AbortController());
      const t0 = Date.now();
      node = document.createElement("div");
      node.className = "fx-progress"; node.setAttribute("role", "status");
      node.innerHTML = `<span class="fx-spin" aria-hidden="true"></span><span class="fx-prog-text"><span>${esc(label)} <span class="fx-elapsed" aria-hidden="true">0 s</span></span>` +
        `<span class="fx-prog-sub">${esc(sub || "")}</span></span><button type="button" class="tool-btn small-btn">${FX.ic("x")}<span>Cancel</span></button>`;
      slot.appendChild(node);
      const el = node.querySelector(".fx-elapsed");
      timer = setInterval(() => { el.textContent = `${Math.round((Date.now() - t0) / 1000)} s`; }, 1000);
      node.querySelector("button").addEventListener("click", () => mine.abort());
      try {
        const r = await fn(mine.signal);
        return mine.signal.aborted ? null : r;
      } catch (e) {
        if (e.aborted || mine.signal.aborted) return null;
        throw e;
      } finally { cleanup(mine); }
    }
    return { run, cancel: () => { if (ctrl) ctrl.abort(); }, get busy() { return !!ctrl; } };
  };

  // Shapes: Okabe-Ito colour always paired with a shape.
  const ASSET = {
    hospital: { c: "#D55E00", label: "Hospital", shape: "cross" },
    school: { c: "#0072B2", label: "School", shape: "square" },
    fire_station: { c: "#CC79A7", label: "Fire station", shape: "triangle" },
    power_site: { c: "#E69F00", label: "Power site", shape: "diamond" },
    settlement: { c: "#ffffff", s: "#000000", label: "Settlement", shape: "circle" },
    residential_area: { c: "#009E73", label: "Residential area", shape: "outline" },
    road: { c: "#56B4E9", label: "Road", shape: "line" },
    rail: { c: "#7F7F7F", label: "Railway", shape: "dashline", dash: "7 4" },
    power_line: { c: "#E69F00", label: "Power line", shape: "dotline", dash: "1 5" },
    building_cell: { c: "#3d4a52", label: "Buildings (~500 m cells)", shape: "dot" },
  };
  FX.ASSET = ASSET;
  FX.assetShape = function (cls, size) {
    const a = ASSET[cls] || { c: "#666", shape: "circle" };
    const r = (size || 14) / 2, st = a.s || "#1d2b34", sw = 1.4;
    const body = {
      cross: `<path d="M${-r * .36} ${-r}h${r * .72}v${r * .64}h${r * .64}v${r * .72}h${-r * .64}v${r * .64}h${-r * .72}v${-r * .64}h${-r * .64}v${-r * .72}h${r * .64}z" fill="${a.c}" stroke="${st}" stroke-width="1"/>`,
      square: `<rect x="${-r * .8}" y="${-r * .8}" width="${r * 1.6}" height="${r * 1.6}" fill="${a.c}" stroke="${st}" stroke-width="1"/>`,
      triangle: `<path d="M0 ${-r}L${r} ${r * .85}H${-r}Z" fill="${a.c}" stroke="${st}" stroke-width="1"/>`,
      diamond: `<path d="M0 ${-r}L${r} 0L0 ${r}L${-r} 0Z" fill="${a.c}" stroke="${st}" stroke-width="1"/>`,
      circle: `<circle r="${r * .85}" fill="${a.c}" stroke="${st}" stroke-width="${sw + .4}"/>`,
      dot: `<circle r="${r * .7}" fill="${a.c}" stroke="#fff" stroke-width="1.2"/>`,
      outline: `<rect x="${-r}" y="${-r * .7}" width="${r * 2}" height="${r * 1.4}" fill="${a.c}" fill-opacity=".12" stroke="${a.c}" stroke-width="2"/>`,
      line: `<line x1="${-r}" y1="0" x2="${r}" y2="0" stroke="${a.c}" stroke-width="3.2" stroke-linecap="round"/>`,
      dashline: `<line x1="${-r}" y1="0" x2="${r}" y2="0" stroke="${a.c}" stroke-width="3" stroke-dasharray="${a.dash}"/>`,
      dotline: `<line x1="${-r}" y1="0" x2="${r}" y2="0" stroke="${a.c}" stroke-width="3" stroke-dasharray="${a.dash}" stroke-linecap="round"/>`,
    }[a.shape];
    const w = size || 14;
    return `<svg width="${w}" height="${w}" viewBox="${-w / 2} ${-w / 2} ${w} ${w}" aria-hidden="true" focusable="false">${body}</svg>`;
  };
  const CLOUD = {
    clear: { c: "#0072B2", label: "clear (model)" },
    partly: { c: "#E69F00", label: "partly cloudy" },
    obscured: { c: "#5f6b73", label: "obscured (model)" },
    unknown: { c: "#6b7780", label: "cloud unknown" },
  };
  FX.CLOUD = CLOUD;
  FX.cloudShape = function (vis, r, withGroup) {
    const c = (CLOUD[vis] || CLOUD.unknown).c;
    let s;
    if (vis === "clear") s = `<circle r="${r}" fill="#fff" stroke="${c}" stroke-width="2"/>`;
    else if (vis === "partly") s = `<circle r="${r}" fill="#fff" stroke="${c}" stroke-width="2"/><path d="M${-r} 0A${r} ${r} 0 0 0 ${r} 0Z" fill="${c}"/>`;
    else if (vis === "obscured") s = `<rect x="${-r}" y="${-r}" width="${r * 2}" height="${r * 2}" fill="${c}" stroke="#1d2b34" stroke-width="1"/>`;
    else s = `<circle r="${r}" fill="#fff" stroke="${c}" stroke-width="2" stroke-dasharray="2.5 2.5"/>`;
    return withGroup ? s : `<svg width="${r * 2 + 4}" height="${r * 2 + 4}" viewBox="${-r - 2} ${-r - 2} ${r * 2 + 4} ${r * 2 + 4}" aria-hidden="true" focusable="false">${s}</svg>`;
  };

  // ------------------------------------------------------------------ root container + section factory
  const root = document.createElement("section");
  root.id = "fx-root";
  root.className = "card fx-root";
  root.hidden = true;
  root.setAttribute("aria-label", "Observation gaps, scenarios and exposure");
  root.innerHTML = `<div class="section-title-row"><h2>Next pass, blind spots and exposure</h2></div>` +
    `<p class="fx-intro">Experimental estimates from satellite detections and OpenStreetMap. Not calibrated.</p>` +
    `<div class="fx-calib"><label class="check"><input id="fx-calib" type="checkbox"> Fit to this fire's recent growth</label>` +
    `<p id="fx-calib-note" class="fx-note"></p></div>`;
  host.insertAdjacentElement("afterend", root);
  FX.root = root;

  FX.section = function (o) {
    const det = document.createElement("details");
    det.className = "fx-sec"; det.id = "fx-sec-" + o.id; det.style.order = o.order || 0;
    det.innerHTML = `<summary>${FX.ic(o.icon || "layers")}<span>${esc(o.title)}</span><span class="fx-badge" hidden></span><span class="fx-chev">${FX.ic("chevron-down")}</span></summary><div class="fx-sec-body"></div>`;
    root.appendChild(det);
    const badge = det.querySelector(".fx-badge");
    const api = {
      el: det, body: det.querySelector(".fx-sec-body"),
      setBadge(text, cls) { badge.hidden = !text; badge.textContent = text || ""; badge.className = "fx-badge" + (cls ? " " + cls : ""); },
      onOpen(fn) { det.addEventListener("toggle", () => { if (det.open) fn(); }); },
      get open() { return det.open; },
    };
    return api;
  };
  FX.warn = (msg) => `<p class="fx-warn">${FX.ic("triangle-alert")}<span>${msg}</span></p>`;

  // calibration switch (experimental, off by default)
  EF.options = EF.options || { calibration: "off" };
  const calib = $("fx-calib");
  function calibNote() {
    const on = calib.checked;
    const supported = EF.sim && EF.sim.params && EF.sim.params.includes("calibration");
    $("fx-calib-note").textContent = on
      ? "Fits a spread multiplier to recent observed growth. It did not improve validation results consistently." + (supported ? "" : " The ML + physics player ignores this switch for now.")
      : "Off: physics uses the issued weather and mapped landscape only.";
  }
  calib.addEventListener("change", () => {
    EF.options.calibration = calib.checked ? "on" : "off";
    EF.sim.reset("Settings changed. Run ML + physics to update the timeline.");
    calibNote();
  });
  calibNote();

  // ------------------------------------------------------------------ player integration: scrubber window + events
  FX.lastRun = () => {
    const d = EF.sim && EF.sim.data, fr = (EF.sim && EF.sim.frames) || [];
    return d && fr.length ? FX.run : null;
  };
  EF.on("load", (d, q, opts) => {
    FX.run = { hours: +q.hours, step: +q.step_min, ignition: q.ignition, start: q.start, fuel: q.fuel, tag: opts && opts.tag };
    FX.win = opts && opts.tag === "until_next_pass" ? { untilMs: Date.parse(opts.until), sat: opts.sat, instrument: opts.instrument } : null;
    drawWindow();
    refreshBrief();
  });
  EF.on("reset", () => { FX.win = null; FX.run = null; drawWindow(); });
  EF.on("point", () => { refreshBrief(); calibNote(); });

  function drawWindow() {
    const controls = $("pb-slider") && $("pb-slider").parentElement;
    if (!controls) return;
    controls.querySelectorAll(".fx-scrub-win").forEach((n) => n.remove());
    const note = $("fx-win-note");
    if (note) note.remove();
    const frames = EF.sim.frames;
    if (!FX.win || !frames.length || !isFinite(FX.win.untilMs)) return;
    const t0 = frames[0].t, t1 = frames[frames.length - 1].t, span = t1 - t0;
    if (!(span > 0)) return;
    const sl = $("pb-slider"), thumb = 16;
    const u = Math.min(FX.win.untilMs, t1);
    const bar = document.createElement("div");
    bar.className = "fx-scrub-win";
    bar.style.left = sl.offsetLeft + thumb / 2 + "px";
    bar.style.width = Math.max(4, (sl.offsetWidth - thumb) * ((u - t0) / span)) + "px";
    bar.style.top = sl.offsetTop + sl.offsetHeight - 3 + "px";
    bar.title = `Until the next usable pass (${FX.utc(FX.win.untilMs)})`;
    controls.appendChild(bar);
    const p = document.createElement("p");
    p.id = "fx-win-note"; p.className = "fx-win-note";
    p.textContent = `Bar: start to next usable pass (${FX.win.sat || "satellite"}, ${FX.utc(FX.win.untilMs)}).`;
    $("pb-ramp").insertAdjacentElement("afterend", p);
  }
  FX.drawWindow = drawWindow;
  if (window.ResizeObserver && $("playback")) new ResizeObserver(() => drawWindow()).observe($("playback"));

  // ------------------------------------------------------------------ Section 1: until next pass
  const sec1 = FX.section({ id: "nextobs", title: "Until next pass", icon: "satellite", order: 10 });
  const nob = { data: null, key: null, seq: 0, error: null };
  const nobSlot = document.createElement("div");
  const nobView = document.createElement("div");
  nobView.className = "fx-sec-body";
  nobView.style.padding = "0";
  sec1.body.append(nobSlot, nobView);
  const nobJob = FX.job(nobSlot);
  const ptKey = (p) => p ? `${p.lat.toFixed(3)},${p.lon.toFixed(3)}` : "";

  function ageBucket(h) { return h == null ? "none" : h <= 3 ? "fresh" : h <= 12 ? "mid" : "old"; }
  function whyNot(p) {
    if (p.usable) return "";
    return p.excluded_reason || (p.geometry_ok === false ? "scan angle above 45 degrees (pixels are large)" : p.cloud && p.cloud.visibility === "obscured" ? "cloud cover 70% or more (model)" : "not usable");
  }

  function timelineSVG(d, W) {
    const t0 = Date.parse(d.query.time_utc), su = d.next_usable_observation ? d.simulate_until : null;
    const tU = su ? Date.parse(su.until_utc) : t0 + 6 * 3600e3;
    const f = d.fire || {};
    const last = f.found && f.last_detection_utc ? Date.parse(f.last_detection_utc) : null;
    let tL = last && last < t0 ? Math.max(last, t0 - 36 * 3600e3) : t0 - 1.5 * 3600e3;
    const span = Math.max(tU - tL, 3600e3);
    const padL = 18, padR = 18, axis = 50, H = 144;
    const x = (t) => padL + ((t - tL) / span) * (W - padL - padR);
    const col = { fresh: "#f6c445", mid: "#d9711c", old: "#5b2a86", none: "#fff" }[ageBucket(f.observation_age_hours)];
    let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Timeline from the last detection to the next usable pass">`;
    // observation gap (last detection -> now)
    if (last && last < t0) {
      s += `<line x1="${x(tL)}" x2="${x(t0)}" y1="${axis}" y2="${axis}" stroke="#1d2b34" stroke-width="8" stroke-linecap="butt"/>` +
        `<line x1="${x(tL)}" x2="${x(t0)}" y1="${axis}" y2="${axis}" stroke="${col}" stroke-width="5"/>`;
      s += `<text class="fx-tl-text" x="${x(tL)}" y="${axis - 18}" text-anchor="start">${FX.hours(f.observation_age_hours)} since last detection</text>`;
    }
    // simulated window
    s += su ? `<rect x="${x(t0)}" y="${axis - 10}" width="${Math.max(2, x(tU) - x(t0))}" height="20" fill="#e3f0ef" stroke="#145c64" stroke-width="1.5"/>`
      : `<line x1="${x(t0)}" x2="${x(tU)}" y1="${axis}" y2="${axis}" stroke="#6b7780" stroke-dasharray="4 4"/>`;
    s += `<text class="fx-tl-text" x="${x(tU)}" y="${axis - 34}" text-anchor="end" style="fill:#145c64;font-weight:600">${su ? `simulated window ${FX.hours(su.exact_hours != null ? su.exact_hours : su.hours)}` : "no usable pass found"}</text>`;
    // now
    s += `<line x1="${x(t0)}" x2="${x(t0)}" y1="${axis - 16}" y2="${axis + 16}" stroke="#1d2b34" stroke-width="2"/>`;
    // passes
    const nextU = d.next_usable_observation;
    const list = [];
    (d.recent_passes || []).forEach((p) => { const t = Date.parse(p.time_utc); if (t >= tL && t <= t0) list.push({ p, t, past: true }); });
    (d.passes || []).forEach((p) => { const t = Date.parse(p.time_utc); if (t >= t0 && t <= tU + 60e3) list.push({ p, t, past: false }); });
    const stem = { good: 28, moderate: 19, degraded: 11 };
    const placed = [];
    const order = list.slice().sort((a, b) => (b.p.usable - a.p.usable) || (a.t - b.t));
    const labelled = new Set();
    order.forEach((it) => { const px = x(it.t); if (placed.every((q) => Math.abs(q - px) > 46)) { placed.push(px); labelled.add(it); } });
    list.sort((a, b) => a.t - b.t).forEach((it) => {
      const p = it.p, px = x(it.t), lx = Math.max(32, Math.min(W - 32, x(it.t))), len = stem[p.quality_tier] || 14, vis = (p.cloud && p.cloud.visibility) || "unknown";
      const faded = !p.usable;
      const isNext = nextU && p.time_utc === nextU.time_utc && p.satellite === nextU.satellite;
      const tip = `${p.satellite} (${p.instrument}) ${FX.utc(it.t)}, scan ${FX.num(p.scan_angle_deg, 0)} deg (${p.quality_tier}), cloud ${p.cloud ? p.cloud.cover_pct : "?"}% ${vis}${p.usable ? ", usable" : ", not usable: " + whyNot(p)}`;
      s += `<g opacity="${faded ? 0.6 : 1}"><title>${esc(tip)}</title>` +
        `<line x1="${px}" x2="${px}" y1="${axis + 8}" y2="${axis + 8 + len}" stroke="#1d2b34" stroke-width="${p.usable ? 2 : 1.2}"/>` +
        (isNext ? `<circle cx="${px}" cy="${axis}" r="12" fill="none" stroke="#145c64" stroke-width="2.5"/>` : "") +
        `<g transform="translate(${px} ${axis})">${FX.cloudShape(vis, 6, true)}</g>`;
      if (labelled.has(it)) {
        const dh = (it.t - t0) / 3600e3;
        s += `<text class="fx-tl-text" x="${lx}" y="${axis + 8 + len + 12}" text-anchor="middle">${FX.hours(dh, true)}</text>` +
          `<text class="fx-tl-muted" x="${lx}" y="${axis + 8 + len + 24}" text-anchor="middle">${esc(p.satellite.replace("Suomi NPP", "S-NPP"))}</text>`;
      }
      s += `</g>`;
    });
    // axis labels
    s += `<text class="fx-tl-muted" x="${x(t0)}" y="${H - 18}" text-anchor="end">now ${FX.utc(t0)}</text>`;
    s += `<text class="fx-tl-muted" x="${x(tU)}" y="${H - 3}" text-anchor="end">until ${FX.utc(tU, new Date(tU).getUTCDate() !== new Date(t0).getUTCDate())}</text>`;
    if (last && last < t0) s += `<g transform="translate(${x(tL)} ${axis})"><circle r="6.5" fill="${col}" stroke="#1d2b34" stroke-width="2"/></g>`;
    return s + `</svg>`;
  }

  function legendHTML() {
    const clouds = ["clear", "partly", "obscured", "unknown"].map((v) => `<span>${FX.cloudShape(v, 6)}${esc(CLOUD[v].label)}</span>`).join("");
    return `<div class="fx-legend" aria-label="Timeline legend">${clouds}</div>` +
      `<div class="fx-legend"><span>Long stem = good scan angle</span><span>Faded: not usable</span><span>Ring: next usable pass</span></div>`;
  }

  function passTable(list, label) {
    if (!list.length) return "";
    const rows = list.map((p) => {
      const t = Date.parse(p.time_utc), vis = (p.cloud && p.cloud.visibility) || "unknown";
      const dh = (t - Date.parse(nob.data.query.time_utc)) / 3600e3;
      return `<tr class="${p.usable ? "" : "fx-dimrow"}"><td>${FX.utc(t, true)}<br><span class="muted">${FX.hours(dh, true)}</span></td><td>${esc(p.satellite)}<br><span class="muted">${esc(p.instrument)}</span></td>` +
        `<td>${esc(p.quality_tier)}<br><span class="muted">${FX.num(p.scan_angle_deg, 0)} deg</span></td>` +
        `<td>${(p.pixel_km || []).map((v) => FX.num(v, 1)).join(" x ")} km</td>` +
        `<td><span class="fx-passcell">${FX.cloudShape(vis, 5)}${esc(vis)}${p.cloud && p.cloud.cover_pct != null ? ` ${p.cloud.cover_pct}%` : ""}</span></td>` +
        `<td>${p.usable ? "usable" : esc(whyNot(p))}${p.fire_detected_in_pass === false ? (p.detection_may_be_pending ? "<br><span class=\"muted\">no detection yet (feed can lag)</span>" : "<br><span class=\"muted\">no detection</span>") : p.fire_detected_in_pass ? "<br><span class=\"muted\">fire detected</span>" : ""}</td></tr>`;
    }).join("");
    return `<details class="fx-details"><summary>${esc(label)} (${list.length})</summary><div class="fx-table-wrap"><table><thead><tr><th>Time</th><th>Satellite</th><th>Geometry</th><th>Pixel</th><th>Cloud (model)</th><th>Status</th></tr></thead><tbody>${rows}</tbody></table></div></details>`;
  }

  function renderNextObs() {
    const d = nob.data, p = FX.point();
    if (!p) { nobView.innerHTML = `<p class="fx-empty">Select a fire on the map.</p>`; sec1.setBadge(""); return; }
    if (nob.error) {
      nobView.innerHTML = `<p class="fx-err" role="alert">Lookup failed: ${esc(nob.error)}</p><div class="fx-actions"><button type="button" class="tool-btn" data-act="reload">${FX.ic("refresh-cw")}<span>Retry</span></button></div>`;
      nobView.querySelector("[data-act=reload]").addEventListener("click", () => loadNextObs(true));
      sec1.setBadge("");
      return;
    }
    if (!d) {
      if (!nobJob.busy) nobView.innerHTML = `<div class="fx-actions"><button type="button" class="tool-btn" data-act="reload">${FX.ic("satellite")}<span>Check next pass</span></button></div>`;
      else nobView.innerHTML = "";
      const b = nobView.querySelector("[data-act=reload]"); if (b) b.addEventListener("click", () => loadNextObs(true));
      return;
    }
    const f = d.fire || {}, nu = d.next_usable_observation, su = nu ? d.simulate_until : null, nx = d.next_observation;
    const age = f.observation_age_hours;
    const ageHTML = f.found
      ? `<span class="fx-age" data-age="${ageBucket(age)}"><i class="fx-agedot"></i>Last detection ${FX.hours(age)} ago, ${esc(f.last_detection_instrument || f.last_detection_feed || "satellite")}</span>`
      : `<span class="fx-age" data-age="none"><i class="fx-agedot"></i>No detection within ${FX.num(f.radius_km, 0)} km in the last 7 days</span>`;
    let h = `<div class="fx-row">${ageHTML}${f.found ? `<span class="fx-badge">${f.n_detections_24h} detections in 24 h</span>` : ""}</div>`;
    if (f.found) h += `<p class="fx-note">FIRMS detections can lag the overpass by up to about 3 h.</p>`;
    const recent = (d.recent_passes || []).filter((q) => q.usable && q.fire_detected_in_pass === false)[0];
    if (recent && recent.detection_may_be_pending) h += `<p class="fx-note">Latest usable pass with no detection yet: ${esc(recent.satellite)}, ${FX.utc(Date.parse(recent.time_utc))}. The feed may lag.</p>`;
    if (nu) {
      h += `<p class="fx-line"><b>Next usable pass: ${esc(nu.satellite)} (${esc(nu.instrument)})</b> at ${FX.utc(Date.parse(nu.time_utc))}, in ${FX.hours(d.gap_hours && d.gap_hours.to_next_usable_observation != null ? d.gap_hours.to_next_usable_observation : nu.hours_from_ref)}. ` +
        `${esc(nu.quality_tier)} geometry, scan ${FX.num(nu.scan_angle_deg, 0)} deg, pixel ${esc((nu.pixel_km || []).map((v) => FX.num(v, 1)).join(" x "))} km, cloud: ${esc((nu.cloud || {}).visibility || "unknown")} (model).</p>`;
      if (nx && nx.time_utc !== nu.time_utc) h += `<p class="fx-note">Earlier pass (${esc(nx.satellite)}, ${FX.utc(Date.parse(nx.time_utc))}) not usable: ${esc(whyNot(nx))}.</p>`;
    } else h += `<p class="fx-warn">${FX.ic("triangle-alert")}<span>No usable pass in the next 48 h. Usable means scan angle 45 degrees or less, not cloud-obscured, sensor delivering data.</span></p>`;
    const staleTle = (d.tle || []).filter((t) => t.fresh === false || t.error);
    if (staleTle.length) h += FX.warn(`Orbital data outdated for ${staleTle.map((t) => esc(t.satellite) + (t.tle_age_days != null ? ` (${FX.num(t.tle_age_days, 1)} days old)` : "")).join(", ")}. Pass times may be less accurate.`);
    const staleFeeds = (d.feeds || []).filter((t) => t.stale || t.error);
    if (staleFeeds.length) h += FX.warn(`Detection feed delayed: ${staleFeeds.map((t) => esc(t.satellite || t.feed)).join(", ")}. Age may be understated.`);
    h += `<div class="fx-timeline" id="fx-tl"></div>${legendHTML()}`;
    h += `<p class="fx-note">Cloud is a model indicator (Open-Meteo), not proof a sensor sees or misses a fire. Smoke, thin cirrus and fire size are not modelled.</p>`;
    const viirsSeen = Object.keys(f.last_by_feed || {}).some((k) => k !== "MODIS");
    if (f.found && !viirsSeen) h += FX.warn("Only MODIS has detected this fire. The ML + physics run may start from a MODIS ignition; MODIS sees fewer burning cells than VIIRS, so the start footprint may be too small.");
    const archive = $("sim-ignition") && $("sim-ignition").value === "archive";
    const hrs = su ? Math.min(72, Math.max(1, su.hours)) : null;
    const can = !!su && f.found && !archive;
    h += `<div class="fx-actions"><button type="button" class="btn-primary" id="fx-run-until"${can ? "" : " disabled"}>${FX.ic("play")}<span>Simulate until next usable pass${hrs ? ` (${hrs} h)` : ""}</span></button></div>`;
    if (archive) h += `<p class="fx-note">Set Ignition to Nearest live fire to use this.</p>`;
    else if (!f.found) h += `<p class="fx-note">No recent detection nearby, so no live fire to simulate.</p>`;
    else if (su && su.hours > 72) h += `<p class="fx-note">The window is ${su.hours} h; the run is capped at 72 h.</p>`;
    else if (su) h += `<p class="fx-note">Runs ${hrs} h from the start of the current hour.</p>`;
    h += passTable(d.passes || [], "All passes in the next 48 h");
    h += passTable(d.recent_passes || [], "Recent passes");
    nobView.innerHTML = h;
    drawTimeline();
    const btn = $("fx-run-until");
    if (btn) btn.addEventListener("click", () => {
      if (EF.sim.running) return;
      FX.win = null;
      EF.sim.run({ hours: hrs, tag: "until_next_pass", until: su.until_utc, sat: `${nu.satellite} ${nu.instrument}`, instrument: nu.instrument });
      const pb = $("playback"); if (pb) pb.scrollIntoView({ block: "nearest" });
    });
    sec1.setBadge(f.found ? `${FX.hours(age)} old` : "no detection", age != null && age > 12 ? "warn" : "");
  }
  function drawTimeline() {
    const t = $("fx-tl");
    if (!t || !nob.data) return;
    t.innerHTML = timelineSVG(nob.data, Math.max(280, Math.floor(t.clientWidth || 340)));
  }
  window.addEventListener("resize", () => { if (sec1.open) drawTimeline(); });

  async function loadNextObs(force) {
    const p = FX.point();
    if (!p) { renderNextObs(); return; }
    const key = ptKey(p);
    if (!force && nob.data && nob.key === key) { renderNextObs(); return; }
    const seq = ++nob.seq;
    nob.data = null; nob.error = null; nob.key = key;
    renderNextObs();
    try {
      const d = await nobJob.run("Looking up satellite passes…", "First lookup can take up to 2 min.",
        (signal) => FX.fetchJSON("/nextobs", { lat: p.lat.toFixed(4), lon: p.lon.toFixed(4) }, { signal }));
      if (seq !== nob.seq) return;
      if (d === null) { nob.error = null; nobView.innerHTML = `<p class="fx-empty">Cancelled.</p><div class="fx-actions"><button type="button" class="tool-btn" data-act="reload">${FX.ic("refresh-cw")}<span>Check again</span></button></div>`; nobView.querySelector("[data-act=reload]").addEventListener("click", () => loadNextObs(true)); return; }
      nob.data = d; FX.nextobs = d; document.dispatchEvent(new CustomEvent("fx-nextobs"));
      renderNextObs(); refreshBrief();
    } catch (e) {
      if (seq !== nob.seq) return;
      nob.error = e.message; renderNextObs();
    }
  }
  FX.loadNextObs = loadNextObs;
  sec1.onOpen(() => loadNextObs(false));
  EF.on("point", () => { nob.seq++; nobJob.cancel(); nob.data = null; nob.error = null; FX.nextobs = null; nob.key = null; if (sec1.open) loadNextObs(true); else renderNextObs(); });
  renderNextObs();

  // ------------------------------------------------------------------ Section 5: incident brief
  const sec5 = FX.section({ id: "brief", title: "Incident brief", icon: "file-text", order: 50 });
  sec5.body.innerHTML = `<p class="fx-note">One-page brief: footprint, estimated exposure, limits, sources. First request can take 1-2 min.</p>` +
    `<label class="input-field"><span>Horizon</span><select id="fx-brief-hours" name="fx-brief-hours"></select></label>` +
    `<div class="fx-brief-links"><a id="fx-brief-html" class="btn-download" target="_blank" rel="noopener" aria-disabled="true">${FX.ic("external-link")}<span>Open brief (HTML)</span></a>` +
    `<a id="fx-brief-pdf" class="btn-download" aria-disabled="true" download="firespot-brief.pdf">${FX.ic("download")}<span>Download brief (PDF)</span></a></div>` +
    `<p id="fx-brief-note" class="fx-note"></p>` +
    `<p class="fx-note">The PDF is generated when you click: usually about 50 seconds, up to 3 minutes in harder cases. Keep this tab open.</p>`;
  let briefTouched = false;   // keep the user's horizon; otherwise default to the current player run
  function refreshBrief() {
    const sel = $("fx-brief-hours"), p = FX.point();
    const prev = sel.value;
    const opts = [];
    const run = FX.lastRun();
    if (run) opts.push([String(run.hours), `Current player run (${run.hours} h)`]);
    if (nob.data && nob.data.next_usable_observation && nob.data.simulate_until) opts.push([String(Math.min(168, nob.data.simulate_until.hours)), `Until next usable pass (${nob.data.simulate_until.hours} h)`]);
    [24, 48, 72].forEach((h) => { if (!opts.some((o) => o[0] === String(h))) opts.push([String(h), `${h} hours`]); });
    sel.innerHTML = opts.map((o) => `<option value="${o[0]}">${esc(o[1])}</option>`).join("");
    sel.value = briefTouched && opts.some((o) => o[0] === prev) ? prev : opts[0][0];
    const a = $("fx-brief-html"), b = $("fx-brief-pdf");
    if (!p) { a.removeAttribute("href"); b.removeAttribute("href"); a.setAttribute("aria-disabled", "true"); b.setAttribute("aria-disabled", "true"); $("fx-brief-note").textContent = "Select a fire on the map first."; return; }
    const startVal = ($("sim-start") && $("sim-start").value) || "";
    const q = new URLSearchParams({ lat: p.lat.toFixed(4), lon: p.lon.toFixed(4), hours: sel.value, step_min: (run && run.step) || 60, ignition: $("sim-ignition").value === "archive" ? "archive" : "live" });
    if (startVal) q.set("start", startVal.slice(0, 16));
    if ($("sim-fuel")) q.set("fuel", $("sim-fuel").value);
    a.href = `${EF.API}/brief.html?${q}`; b.href = `${EF.API}/brief.pdf?${q}`;
    a.setAttribute("aria-disabled", "false"); b.setAttribute("aria-disabled", "false");
    $("fx-brief-note").textContent = `Point ${p.lat.toFixed(4)}, ${p.lon.toFixed(4)}, start ${startVal ? startVal.replace("T", " ") + " UTC" : "now"}, ${sel.value} h.`;
  }
  // Warm the server cache while the user reads: the slow part of a brief is the first OpenStreetMap/Overpass query for a new area.
  let warmedFor = null;
  sec5.onOpen(() => {
    const d = EF.sim && EF.sim.data;
    if (!d || !(EF.sim.frames || []).length || warmedFor === d) return;
    warmedFor = d;
    const b = { ...d }; delete b.guidance;
    fetch(`${EF.API}/exposure?top_n=25&buildings=true&population=true`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) }).catch(() => {});
  });
  // PDF: when a forecast is already in the player, send it to the server (no recomputation) and download the result.
  let pdfBusy = false;
  $("fx-brief-pdf").addEventListener("click", async (ev) => {
    const d = EF.sim && EF.sim.data;
    if (!d || !(EF.sim.frames || []).length) return;      // no run yet: the plain link (GET) does the work
    ev.preventDefault();
    if (pdfBusy) return;
    pdfBusy = true;
    const btn = $("fx-brief-pdf"), note = $("fx-brief-note"), lbl = btn.querySelector("span"), old = lbl.textContent;
    btn.setAttribute("aria-disabled", "true"); lbl.textContent = "Generating PDF…";
    note.textContent = "Generating the PDF: usually about 50 seconds, up to 3 minutes. Keep this tab open.";
    try {
      const body = { ...d }; delete body.guidance;
      const res = await fetch(`${EF.API}/brief.pdf?buildings=true&population=true`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (!res.ok) { let m = res.statusText; try { m = (await res.json()).detail || m; } catch (e) { /* not JSON */ } throw new Error(m); }
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a"); a.href = url; a.download = "firespot-brief.pdf"; document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      note.textContent = "PDF ready: the download has started.";
    } catch (e) {
      note.textContent = "PDF failed: " + e.message + ". Try the HTML brief and print it to PDF.";
    } finally { pdfBusy = false; lbl.textContent = old; btn.setAttribute("aria-disabled", "false"); }
  });
  $("fx-brief-hours").addEventListener("change", () => { briefTouched = true; refreshBrief(); });
  ["sim-start", "sim-ignition", "sim-fuel"].forEach((id) => $(id) && $(id).addEventListener("change", refreshBrief));
  refreshBrief();

  // ------------------------------------------------------------------ visibility with the workspace
  function onMode(mode) {
    root.hidden = mode !== "date";
    if (mode !== "date") { FX.win = null; drawWindow(); }
    else { calibNote(); refreshBrief(); }
    FX.modeHooks.forEach((fn) => { try { fn(mode); } catch (e) { console.error(e); } });
  }
  EF.onModeChange(onMode);
  onMode(EF.state.mode);
  EF.hydrateIcons(root);
})();
