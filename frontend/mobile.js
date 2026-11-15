/* FireSpot mobile layer (phones + small tablets, <= 800 px).
   Map-first layout: the analysis panel becomes a bottom sheet (collapsed handle / ~70 % / full),
   tabs become a bottom bar, the map legend becomes a button + popover, Layers becomes a sheet,
   the forecast player is docked above the handle, the on-screen keyboard never hides a focused field.
   Everything is additive: this file only adds a class on <html>, a handle element and a legend button.
   Desktop (> 800 px) is untouched. */
(function () {
  "use strict";
  var MQ = window.matchMedia("(max-width: 800px)");
  var root = document.documentElement;
  var STORE = "firespot:sheet:";
  var DEFAULTS = { live: "collapsed", date: "collapsed", history: "half", alerts: "half", replay: "half" };
  var inited = false, handle, panel, container, header, nav;
  var els = {}, baseH = window.innerHeight, suppressClick = false, mirrorTarget = null;

  // Set the class synchronously (script is in <head>) so the first paint already has the phone layout.
  root.classList.toggle("m", MQ.matches);
  try {
    var vp = document.querySelector('meta[name="viewport"]');
    if (vp && !/viewport-fit/.test(vp.content)) vp.content += ", viewport-fit=cover, interactive-widget=resizes-content";
  } catch (e) {}

  function $(id) { return document.getElementById(id); }
  function isPl() { return (root.lang || "pl").slice(0, 2) !== "en"; }
  function T(en, pl) { return isPl() ? pl : en; }
  function store(k, v) { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) {} return null; }
  function ws() {
    var a = document.querySelector('.mode-tab[aria-current="page"]');
    return a ? a.id.replace("tab-", "") : "live";
  }
  function state() { return root.getAttribute("data-sheet") || "collapsed"; }

  var ICON = {
    up: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 15 6-6 6 6"/></svg>',
    full: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/></svg>',
    shrink: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 10l7-7M10 14l-7 7M20 10h-6V4M4 14h6v6"/></svg>',
    legend: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/></svg>'
  };

  /* ------------------------------------------------------------ sheet state */
  function setState(s, persist) {
    if (s !== "collapsed" && s !== "half" && s !== "full") s = "half";
    root.setAttribute("data-sheet", s);
    root.style.removeProperty("--sheet-h");
    if (persist !== false) store(STORE + ws(), s);
    syncHandle();
    if (s === "collapsed" && panel) panel.scrollTop = 0;
  }
  function applyWorkspace() {
    var saved = store(STORE + ws());
    setState(saved === "collapsed" || saved === "half" || saved === "full" ? saved : DEFAULTS[ws()] || "half", false);
    if (panel) panel.scrollTop = 0;
    syncAction(); syncTitle(); scrollTabIntoView();
  }
  function toggle() { setState(state() === "collapsed" ? "half" : "collapsed"); }
  function toggleFull() { setState(state() === "full" ? "half" : "full"); }

  function syncHandle() {
    if (!handle) return;
    var s = state(), open = s !== "collapsed";
    els.toggle.setAttribute("aria-expanded", String(open));
    els.toggle.setAttribute("aria-label", (open ? T("Collapse panel: ", "Zwiń panel: ") : T("Expand panel: ", "Rozwiń panel: ")) + els.title.textContent);
    els.full.setAttribute("aria-label", s === "full" ? T("Shrink panel", "Zmniejsz panel") : T("Full-height panel", "Panel na całą wysokość"));
    els.full.innerHTML = s === "full" ? ICON.shrink : ICON.full;
    els.full.hidden = !open;
    els.chev.style.transform = open ? "rotate(180deg)" : "";
    panel.setAttribute("data-sheet-state", s);
  }
  function syncTitle() {
    if (!handle) return;
    var h1 = $("workspace-title");
    els.title.textContent = (h1 && h1.textContent.trim()) || "FireSpot";
    var pill = $("status-pill");
    if (pill) {
      els.dot.className = "m-dot" + (pill.classList.contains("ready") ? " ready" : "");
      els.dot.title = pill.textContent.trim();
    }
    syncHandle();
  }
  function actionButton() {
    var id = { live: "btn-show-fires", date: "btn-forecast" }[ws()];
    return id ? $(id) : null;
  }
  function syncAction() {
    if (!handle) return;
    var b = actionButton();
    mirrorTarget = b;
    els.action.hidden = !b;
    if (!b) return;
    var sp = b.querySelector("span");
    els.action.textContent = ((sp || b).textContent || "").trim();
    els.action.disabled = b.disabled;
  }

  /* ------------------------------------------------------------ build UI */
  function build() {
    panel = $("panel"); container = $("viewport-container"); header = $("workspace-header"); nav = document.querySelector(".mode-tabs");
    if (!panel || !container) return;
    handle = document.createElement("div");
    handle.id = "m-handle";
    handle.setAttribute("data-i18n-skip", "");
    handle.innerHTML =
      '<span class="m-grip" aria-hidden="true"></span>' +
      '<div class="m-row">' +
        '<button type="button" class="m-toggle" aria-controls="panel"><span class="m-dot"></span><span class="m-title"></span><span class="m-chev">' + ICON.up + '</span></button>' +
        '<button type="button" class="m-action btn-primary" hidden></button>' +
        '<button type="button" class="m-full" hidden></button>' +
      '</div>';
    panel.insertBefore(handle, panel.firstChild);
    els = { toggle: handle.querySelector(".m-toggle"), title: handle.querySelector(".m-title"), dot: handle.querySelector(".m-dot"),
            chev: handle.querySelector(".m-chev"), action: handle.querySelector(".m-action"), full: handle.querySelector(".m-full") };

    els.toggle.addEventListener("click", function () { if (!suppressClick) toggle(); });
    els.full.addEventListener("click", toggleFull);
    els.action.addEventListener("click", function () {
      var b = actionButton(); if (!b || b.disabled) return;
      b.click();
      setState("collapsed", false); // show the map: results/prediction are drawn there
    });

    // drag the handle
    var drag = null;
    function limits() {
      var hh = handle.getBoundingClientRect().height || 64;
      var top = header ? header.getBoundingClientRect().bottom : 48;
      var navH = nav ? nav.getBoundingClientRect().height : 56;
      var max = Math.max(hh + 80, window.innerHeight - top - navH);
      return { min: hh, half: Math.round((window.innerHeight - top - navH) * 0.7), max: max };
    }
    handle.addEventListener("pointerdown", function (e) {
      if (e.target.closest(".m-action,.m-full")) return;
      if (e.pointerType === "mouse" && e.button !== 0) return;
      drag = { id: e.pointerId, y: e.clientY, h: panel.getBoundingClientRect().height, moved: false, ly: e.clientY, lt: e.timeStamp, v: 0, lim: limits() };
    });
    handle.addEventListener("pointermove", function (e) {
      if (!drag || e.pointerId !== drag.id) return;
      var dy = e.clientY - drag.y;
      if (!drag.moved) {
        if (Math.abs(dy) < 6) return;
        drag.moved = true; root.classList.add("m-drag");
        try { handle.setPointerCapture(e.pointerId); } catch (_) {}
      }
      var dt = e.timeStamp - drag.lt;
      if (dt > 0) drag.v = 0.7 * drag.v + 0.3 * ((e.clientY - drag.ly) / dt);
      drag.ly = e.clientY; drag.lt = e.timeStamp;
      var h = Math.min(drag.lim.max, Math.max(drag.lim.min, drag.h - dy));
      drag.cur = h;
      root.style.setProperty("--sheet-h", h + "px");
      e.preventDefault();
    });
    function end(e, cancel) {
      if (!drag || e.pointerId !== drag.id) return;
      var d = drag; drag = null;
      if (!d.moved) {
        // robust tap: some touch stacks drop the synthesized click right after a drag gesture
        if (!cancel && e.pointerType === "touch" && e.target.closest && e.target.closest(".m-toggle") && !suppressClick) {
          suppressClick = true; setTimeout(function () { suppressClick = false; }, 400); toggle();
        }
        return;
      }
      root.classList.remove("m-drag");
      try { handle.releasePointerCapture(e.pointerId); } catch (_) {}
      suppressClick = true; setTimeout(function () { suppressClick = false; }, 350);
      var proj = (d.cur == null ? d.h : d.cur) - d.v * 160; // fling momentum (v > 0 = moving down)
      var L = d.lim, best = "collapsed", bd = Math.abs(proj - L.min);
      [["half", L.half], ["full", L.max]].forEach(function (p) { var dd = Math.abs(proj - p[1]); if (dd < bd) { bd = dd; best = p[0]; } });
      setState(cancel ? state() : best);
    }
    handle.addEventListener("pointerup", function (e) { end(e, false); });
    handle.addEventListener("pointercancel", function (e) { end(e, true); });
    handle.addEventListener("lostpointercapture", function () { root.classList.remove("m-drag"); });

    // legend button + popover
    var lb = document.createElement("button");
    lb.id = "m-legend-btn"; lb.type = "button"; lb.className = "float-btn"; lb.setAttribute("data-i18n-skip", "");
    lb.setAttribute("aria-controls", "m-legend-pop");
    function legendLabel() { lb.innerHTML = ICON.legend + "<span>" + T("Legend", "Legenda") + "</span>"; lb.setAttribute("aria-expanded", String(root.classList.contains("m-legend-open"))); }
    legendLabel();
    lb.addEventListener("click", function () { root.classList.toggle("m-legend-open"); legendLabel(); });
    container.appendChild(lb);
    var lg = container.querySelector(".map-legend");
    if (lg) lg.id = lg.id || "m-legend-pop";
    // tap on the map closes popovers
    container.addEventListener("pointerdown", function (e) {
      if (root.classList.contains("m-legend-open") && !e.target.closest("#m-legend-btn,.map-legend")) { root.classList.remove("m-legend-open"); legendLabel(); }
    });

    // Layers menu as a sheet (+ Done button)
    var menu = $("layers-menu"), lbtn = $("layers-btn");
    if (menu && lbtn) {
      var done = document.createElement("button");
      done.type = "button"; done.className = "btn-primary m-layers-done"; done.setAttribute("data-i18n-skip", "");
      function doneLabel() { done.textContent = T("Done", "Gotowe"); }
      doneLabel(); menu.appendChild(done);
      done.addEventListener("click", function () { lbtn.click(); });
      new MutationObserver(function () {
        root.classList.toggle("m-layers-open", lbtn.getAttribute("aria-expanded") === "true" && !menu.hidden);
      }).observe(lbtn, { attributes: true, attributeFilter: ["aria-expanded"] });
      new MutationObserver(function () { root.classList.toggle("m-layers-open", !menu.hidden); }).observe(menu, { attributes: true, attributeFilter: ["hidden"] });
      new MutationObserver(function () { doneLabel(); legendLabel(); syncHandle(); }).observe(root, { attributes: true, attributeFilter: ["lang"] });
    }

    // tab / title / status / action mirroring
    var tabObs = new MutationObserver(function () { applyWorkspace(); });
    document.querySelectorAll(".mode-tab").forEach(function (t) { tabObs.observe(t, { attributes: true, attributeFilter: ["aria-current"] }); });
    var h1 = $("workspace-title"); if (h1) new MutationObserver(syncTitle).observe(h1, { childList: true, characterData: true, subtree: true });
    var pill = $("status-pill"); if (pill) new MutationObserver(syncTitle).observe(pill, { childList: true, characterData: true, subtree: true, attributes: true });
    ["btn-show-fires", "btn-forecast"].forEach(function (id) {
      var b = $(id); if (b) new MutationObserver(syncAction).observe(b, { childList: true, characterData: true, subtree: true, attributes: true, attributeFilter: ["disabled"] });
    });

    // forecast player: dock above the handle, collapse the sheet when a prediction appears
    var pbNode = $("playback");
    if (pbNode) {
      new MutationObserver(function () { if (MQ.matches && !pbNode.hidden) setState("collapsed", false); }).observe(pbNode, { attributes: true, attributeFilter: ["hidden"] });
      if (window.ResizeObserver) new ResizeObserver(function () { root.style.setProperty("--m-pb-h", pbNode.getBoundingClientRect().height + "px"); }).observe(pbNode);
    }
    var showTl = $("sim-show"); if (showTl) showTl.addEventListener("click", function () { if (MQ.matches) setState("collapsed", false); }, true);
    new MutationObserver(fixPlayback).observe(panel, { childList: true });
    fixPlayback();
  }

  function fixPlayback() {
    if (!MQ.matches || !container) return;
    var pb = $("playback");
    if (pb && pb.parentNode !== container) { pb.classList.remove("docked"); container.appendChild(pb); }
  }

  function scrollTabIntoView() {
    if (!nav) return;
    var a = nav.querySelector('.mode-tab[aria-current="page"]');
    if (!a) return;
    var target = a.offsetLeft - (nav.clientWidth - a.offsetWidth) / 2;
    nav.scrollLeft = Math.max(0, target);
  }

  /* ------------------------------------------------------------ keyboard (visualViewport) */
  function onViewport() {
    if (!MQ.matches) return;
    var vv = window.visualViewport;
    var focused = document.activeElement && /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName) && !/checkbox|radio|range|button/.test(document.activeElement.type || "");
    var overlay = vv ? Math.max(0, window.innerHeight - vv.height - vv.offsetTop) : 0;
    var shrink = Math.max(0, baseH - window.innerHeight);
    var open = focused && (overlay + shrink > 120);
    if (!open && !focused) baseH = Math.max(baseH, window.innerHeight);
    root.classList.toggle("m-kb", open);
    root.style.setProperty("--m-kb", (open ? overlay : 0) + "px");
    root.style.setProperty("--m-vvh", (vv ? vv.height : window.innerHeight) + "px");
    if (open && document.activeElement && document.activeElement.scrollIntoView) {
      var el = document.activeElement;
      setTimeout(function () { try { el.scrollIntoView({ block: "center", inline: "nearest" }); } catch (e) {} }, 60);
    }
  }
  function initKeyboard() {
    if (window.visualViewport) {
      window.visualViewport.addEventListener("resize", onViewport);
      window.visualViewport.addEventListener("scroll", onViewport);
    }
    window.addEventListener("resize", onViewport);
    document.addEventListener("focusin", function (e) {
      if (!MQ.matches || !panel || !panel.contains(e.target)) return;
      // a field was focused in a collapsed sheet (e.g. keyboard navigation): open it
      if (state() === "collapsed" && /^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName)) setState("half", false);
      setTimeout(onViewport, 250); setTimeout(onViewport, 600);
    });
    document.addEventListener("focusout", function () { setTimeout(onViewport, 150); });
    window.addEventListener("orientationchange", function () { setTimeout(function () { if (!/^(INPUT|SELECT|TEXTAREA)$/.test((document.activeElement || {}).tagName || "")) baseH = window.innerHeight; onViewport(); }, 400); });
  }

  /* ------------------------------------------------------------ init */
  function init() {
    if (inited) return;
    inited = true;
    baseH = window.innerHeight;
    build();
    initKeyboard();
    applyWorkspace();
    syncTitle();
    window.addEventListener("load", function () { baseH = Math.max(baseH, window.innerHeight); fixPlayback(); syncAction(); syncTitle(); scrollTabIntoView(); });
    window.addEventListener("resize", scrollTabIntoView);
    MQ.addEventListener && MQ.addEventListener("change", function () {
      root.classList.toggle("m", MQ.matches);
      if (MQ.matches) { applyWorkspace(); setTimeout(fixPlayback, 0); }
      root.classList.remove("m-kb", "m-legend-open", "m-layers-open", "m-drag");
      try { window.dispatchEvent(new Event("resize")); } catch (e) {}
    });
    // let Leaflet re-measure once the phone layout is applied
    setTimeout(function () { try { window.dispatchEvent(new Event("resize")); } catch (e) {} }, 50);
  }
  function ready() { init(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ready); else ready();
})();
