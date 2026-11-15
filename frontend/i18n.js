/* EquiFire runtime translation layer (Polish default, English switch).
   The English source strings in index.html / app.js / ui-*.js are NOT changed. This file walks text nodes and a few
   attributes, looks them up in the dictionary from i18n-pl.js (exact strings + {placeholder} patterns) and writes the
   Polish text in place. The English original is kept in a WeakMap so switching back to EN is exact and idempotent.
   A MutationObserver translates everything the app renders later (tables, popups, SVG charts, Leaflet controls ...).
   Anything that is not in the dictionary stays English (graceful fallback).
   Opt out of an element with the attribute data-i18n-skip. Loaded in <head>, before app.js, so nothing flashes. */
(function () {
  "use strict";
  var KEY = "equifire:lang";
  var DICT = window.EQUI_I18N_PL || { exact: {}, patterns: [] };
  var SKIP_SEL = "script,style,code,pre,textarea,noscript,[data-i18n-skip]";
  var ATTRS = ["placeholder", "title", "aria-label", "alt", "data-tip", "aria-description"];
  var ATTR_SEL = "[placeholder],[title],[aria-label],[alt],[data-tip],[aria-description],input[type=button],input[type=submit]";
  var LETTER = /[A-Za-z]/;

  // ---------------------------------------------------------------- language state
  function readLang() {
    try {
      var q = /[?&]lang=(pl|en)\b/.exec(location.search);
      if (q) { try { localStorage.setItem(KEY, q[1]); } catch (e) {} return q[1]; }
    } catch (e) {}
    try { var v = localStorage.getItem(KEY); if (v === "pl" || v === "en") return v; } catch (e) {}
    return "en";
  }
  var lang = readLang();
  document.documentElement.lang = lang;

  // ---------------------------------------------------------------- dictionary compile
  var exact = new Map();
  Object.keys(DICT.exact || {}).forEach(function (k) { exact.set(norm(k), DICT.exact[k]); });
  function norm(s) { return s.replace(/\s+/g, " ").trim(); }
  function esc(s) { return s.replace(/[.*+?^${}()|[\]\\\/]/g, "\\$&"); }

  var patterns = (DICT.patterns || []).map(function (p) {
    var src = norm(p[0]), names = [], lits = [], re = "";
    var parts = src.split(/(\{#?[A-Za-z0-9_]+\})/);
    parts.forEach(function (part) {
      var m = /^\{(#?[A-Za-z0-9_]+)\}$/.exec(part);
      if (m) { names.push(m[1]); re += m[1].charAt(0) === "#" ? "(-?[\\d][\\d.,]*[kKM]?)" : "([\\s\\S]+?)"; }
      else if (part) { re += esc(part); lits.push(part); }
    });
    var spec = lits.join("").length;
    return { re: new RegExp("^" + re + "$"), names: names, out: p[1], lit: lits.sort(function (a, b) { return b.length - a.length; })[0] || "", spec: spec };
  }).sort(function (a, b) { return b.spec - a.spec; });

  // Polish short month names for "20 Jan 2012" style dates the app formats with en-GB
  var MON = { Jan: "sty", Feb: "lut", Mar: "mar", Apr: "kwi", May: "maj", Jun: "cze", Jul: "lip", Aug: "sie", Sep: "wrz", Oct: "paź", Nov: "lis", Dec: "gru" };
  var DATE_RE = /\b(\d{1,2}) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b/g;

  var cache = new Map();

  // Polish plural: 1 -> f1, 2-4 (not 12-14) -> f2, otherwise f5
  function plural(v, forms) {
    var n = Math.abs(parseInt(String(v).replace(/[^\d]/g, ""), 10)), f = forms.split(",");
    if (isNaN(n)) return f[2] || f[0];
    if (n === 1) return f[0];
    var d = n % 10, t = n % 100;
    return d >= 2 && d <= 4 && !(t >= 12 && t <= 14) ? f[1] : f[2];
  }

  function fill(tpl, names, m) {
    return tpl.replace(/\{(#?[A-Za-z0-9_]+)(?:\|(t|pl:[^}]*))?\}/g, function (_, n, mod) {
      var i = names.indexOf(n);
      if (i < 0) i = names.indexOf("#" + n);
      if (i < 0) return "";
      var v = m[i + 1];
      if (!mod) return v;
      if (mod === "t") return trCore(norm(v), 0);
      return plural(v, mod.slice(3));
    });
  }

  // translate a whitespace-normalised string; depth guards recursion
  function trCore(s, depth) {
    if (!s) return s;
    var hit = exact.get(s);
    if (hit !== undefined) return hit;
    for (var i = 0; i < patterns.length; i++) {
      var p = patterns[i];
      if (p.lit && s.indexOf(p.lit) < 0) continue;
      var m = p.re.exec(s);
      if (m) return fill(p.out, p.names, m);
    }
    if ((depth || 0) > 2) return s;
    // fall back: translate sentence by sentence / segment by segment
    var re = (depth || 0) === 0 ? /(?<=[.!?])\s+(?=[A-Za-z0-9"'(\u2018])| · /g : /; /g, parts = [], seps = [], last = 0, mm;
    while ((mm = re.exec(s))) { parts.push(s.slice(last, mm.index)); seps.push(mm[0]); last = mm.index + mm[0].length; }
    if (seps.length) {
      parts.push(s.slice(last));
      var any = false, out = "";
      for (var k = 0; k < parts.length; k++) {
        var t = trCore(parts[k], (depth || 0) + 1);
        if (t === parts[k] && /[.!?]$/.test(t)) { var t2 = trCore(t.slice(0, -1), (depth || 0) + 1); if (t2 !== t.slice(0, -1)) t = t2 + t.slice(-1); }
        if (t !== parts[k]) any = true;
        out += t + (k < seps.length ? seps[k] : "");
      }
      if (any) return out;
    }
    return s;
  }

  function tr(s) {
    if (!s || !LETTER.test(s)) return s;
    var c = cache.get(s);
    if (c !== undefined) return c;
    var m = /^(\s*)([\s\S]*?)(\s*)$/.exec(s);
    var core = norm(m[2]);
    var out = core ? trCore(core, 0) : core;
    out = out.replace(DATE_RE, function (_, d, mo) { return d + " " + MON[mo]; });
    var res = m[1] + out + m[3];
    if (cache.size > 4000) cache.clear();
    cache.set(s, res);
    return res;
  }

  // Single-letter month initials on calendar axes (J F M A M J J A S O N D -> S L M K M C L S W P L G)
  var MI_PL = "SLMKMCLSWPLG";
  function monthInitial(node, s) {
    if (!/^[JFMASOND]$/.test(s)) return null;
    var p = node.parentNode;
    if (!p || !p.namespaceURI || p.nodeName.toLowerCase() !== "text") return null;
    var chart = p.closest && p.closest("#cal-chart,#wk-chart");
    if (!chart) return null;
    var list = [].filter.call(chart.querySelectorAll("text.ax"), function (t) {
      var r = textRec.get(t.firstChild);
      var o = r ? r.orig : t.textContent;
      return /^[JFMASOND]$/.test(o) && t.childNodes.length === 1;
    });
    var i = list.indexOf(p);
    return i < 0 ? null : MI_PL.charAt(i % 12);
  }

  // ---------------------------------------------------------------- apply
  var textRec = new WeakMap();   // Text node -> {orig, pl}
  var attrRec = new WeakMap();   // Element   -> {attr: {orig, pl}}
  var observer = null;

  function skipped(el) { return !!(el && el.closest && el.closest(SKIP_SEL)); }

  function atTail(n) {
    for (; n && n !== document.documentElement; n = n.parentNode) if (n.nextSibling) return false;
    return true;
  }

  function doText(node) {
    var data = node.data, rec = textRec.get(node);
    if (lang === "en") {
      if (rec && data === rec.pl) node.data = rec.orig;
      textRec.delete(node);
      return;
    }
    if (rec && data === rec.pl) return;           // already translated (never translate twice)
    if (document.readyState === "loading" && atTail(node)) return;   // parser may still append text; DOMContentLoaded pass catches it
    if (!LETTER.test(data)) { if (rec) textRec.delete(node); return; }
    var par = node.parentNode;
    if (par && skipped(par.nodeType === 1 ? par : par.parentElement)) return;
    var pl = monthInitial(node, data.trim());
    if (pl === null) pl = tr(data);
    if (pl !== data) { node.data = pl; textRec.set(node, { orig: data, pl: pl }); }
    else if (rec) textRec.delete(node);
  }

  function attrApplicable(el, a) {
    if (a === "title" && el.namespaceURI === "http://www.w3.org/2000/svg" && el.localName === "title") return false;
    return true;
  }

  function doAttrs(el, only) {
    if (el.nodeType !== 1) return;
    var names = only ? [only] : ATTRS.slice();
    if (!only && el.localName === "input" && /^(button|submit|reset)$/.test(el.type)) names.push("value");
    if (only === "value" && !(el.localName === "input" && /^(button|submit|reset)$/.test(el.type))) return;
    if (el.localName === "meta" && el.getAttribute("name") === "description") names.push("content");
    if (el.localName === "meta" && only === "content" && el.getAttribute("name") !== "description") return;
    var rec = attrRec.get(el);
    for (var i = 0; i < names.length; i++) {
      var a = names[i];
      if (!el.hasAttribute(a) || !attrApplicable(el, a)) continue;
      var cur = el.getAttribute(a), r = rec && rec[a];
      if (lang === "en") {
        if (r && cur === r.pl) el.setAttribute(a, r.orig);
        if (rec) delete rec[a];
        continue;
      }
      if (r && cur === r.pl) continue;
      if (skipped(el)) continue;
      var pl = tr(cur);
      if (pl !== cur) {
        if (!rec) { rec = {}; attrRec.set(el, rec); }
        rec[a] = { orig: cur, pl: pl };
        el.setAttribute(a, pl);
      } else if (rec) delete rec[a];
    }
  }

  function walk(root) {
    if (!root) return;
    if (root.nodeType === 3) { doText(root); return; }
    if (root.nodeType !== 1 && root.nodeType !== 9 && root.nodeType !== 11) return;
    if (root.nodeType === 1) {
      if (skipped(root)) return;
      doAttrs(root);
    }
    var tw = document.createTreeWalker(root, 4 /* SHOW_TEXT */);
    var n, list = [];
    while ((n = tw.nextNode())) list.push(n);
    for (var i = 0; i < list.length; i++) doText(list[i]);
    var els = root.querySelectorAll ? root.querySelectorAll(ATTR_SEL) : [];
    for (var j = 0; j < els.length; j++) doAttrs(els[j]);
    if (root.nodeType === 1 && root.localName === "meta") doAttrs(root);
  }

  function flush() { if (observer) observer.takeRecords(); }   // drop our own mutations: no feedback loop

  function onMutations(records) {
    if (lang === "en") return;
    for (var i = 0; i < records.length; i++) {
      var r = records[i];
      if (r.type === "childList") {
        for (var k = 0; k < r.addedNodes.length; k++) walk(r.addedNodes[k]);
      } else if (r.type === "characterData") {
        if (r.target.nodeType === 3) doText(r.target);
      } else if (r.type === "attributes") {
        doAttrs(r.target, r.attributeName);
      }
    }
    flush();
  }

  function start() {
    observer = new MutationObserver(onMutations);
    observer.observe(document.documentElement, {
      childList: true, subtree: true, characterData: true,
      attributes: true, attributeFilter: ATTRS.concat(["value", "content"]),
    });
  }

  function applyAll() {
    walk(document.documentElement);
    flush();
  }

  function setLang(l, persist) {
    if (l !== "pl" && l !== "en") return;
    lang = l;
    document.documentElement.lang = l;
    if (persist !== false) { try { localStorage.setItem(KEY, l); } catch (e) {} }
    if (l === "pl") cache.clear();
    applyAll();
    syncSwitch();
    try { document.dispatchEvent(new CustomEvent("equifire:langchange", { detail: { lang: l } })); } catch (e) {}
  }

  // ---------------------------------------------------------------- PL | EN switch in the header
  function syncSwitch() {
    var box = document.getElementById("lang-switch");
    if (!box) return;
    [].forEach.call(box.querySelectorAll("button[data-lang]"), function (b) {
      var on = b.getAttribute("data-lang") === lang;
      b.setAttribute("aria-pressed", on ? "true" : "false");
      b.classList.toggle("active", on);
    });
  }

  function buildSwitch() {
    if (document.getElementById("lang-switch")) { syncSwitch(); return; }
    var host = document.getElementById("workspace-header");
    if (!host) return;
    var box = document.createElement("div");
    box.id = "lang-switch";
    box.className = "lang-switch";
    box.setAttribute("role", "group");
    box.setAttribute("aria-label", "Język / Language");
    box.setAttribute("data-i18n-skip", "");
    box.innerHTML =
      '<button type="button" data-lang="pl" lang="pl" title="Polski" aria-label="Polski"><span aria-hidden="true">PL</span></button>' +
      '<button type="button" data-lang="en" lang="en" title="English" aria-label="English"><span aria-hidden="true">EN</span></button>';
    box.addEventListener("click", function (e) {
      var b = e.target.closest && e.target.closest("button[data-lang]");
      if (b) setLang(b.getAttribute("data-lang"));
    });
    var theme = document.getElementById("theme-toggle");
    if (theme && theme.parentNode === host) host.insertBefore(box, theme); else host.appendChild(box);
    syncSwitch();
  }

  // ---------------------------------------------------------------- QA helper: strings still in English in PL mode
  function untranslated(root) {
    var out = {}, tw = document.createTreeWalker(root || document.body, 4), n;
    while ((n = tw.nextNode())) {
      var p = n.parentElement;
      if (!p || skipped(p)) continue;
      var s = n.data.replace(/\s+/g, " ").trim();
      if (s.length < 3 || !LETTER.test(s) || textRec.has(n) && textRec.get(n).pl === n.data) continue;
      if (exact.has(s) || !/[A-Za-z]{3,}/.test(s.replace(UNITS, ""))) continue;
      if (tr(s) === s) out[s] = 1;
    }
    (root || document.body).querySelectorAll(ATTR_SEL).forEach(function (el) {
      ATTRS.forEach(function (a) {
        if (!el.hasAttribute(a) || skipped(el) || !attrApplicable(el, a)) return;
        var v = el.getAttribute(a).trim();
        var r = attrRec.get(el);
        if (v.length > 2 && LETTER.test(v) && !(r && r[a] && r[a].pl === v) && tr(v) === v) out["[" + a + "] " + v] = 1;
      });
    });
    return Object.keys(out);
  }

  var UNITS = /\b(km|MW|UTC|FRP|MODIS|VIIRS|RH|mm|min|NOAA|NASA|FIRMS|GOES|NPP|ESA|OSM|HRSL|Terra|Aqua|S-NPP|Leaflet|FireSpot|CAP|XML|JSON|CSV|GeoJSON|HTML|PDF|ML|API|HTTP|ROS|IoU|ISO|DEM|FWI|CIESIN|Meta)\b|[A-Za-z]{1,2}\b/g;

  window.EquiI18n = {
    get lang() { return lang; },
    setLang: setLang,
    t: function (s) { return lang === "pl" ? tr(s) : s; },
    tr: tr,
    apply: applyAll,
    untranslated: untranslated,
    why: function (s) { s = norm(s); for (var i = 0; i < patterns.length; i++) { if (patterns[i].re.test(s)) return patterns[i].re.source + " => " + patterns[i].out; } return null; },
    stats: function () { return { exact: exact.size, patterns: patterns.length }; },
  };

  start();
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { buildSwitch(); if (lang === "pl") applyAll(); });
  } else { buildSwitch(); if (lang === "pl") applyAll(); }
})();
