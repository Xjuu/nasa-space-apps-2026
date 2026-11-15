/* Theme toggle (dark = default, VS Code style; light = original Operate). Choice kept in localStorage. */
(function () {
  "use strict";
  var KEY = "equifire:theme";
  var root = document.documentElement;
  var btn = document.getElementById("theme-toggle");

  /* Leaflet draws vectors on <canvas> (preferCanvas), so CSS cannot recolour them. In dark theme the canvas renderer swaps a
     few colours at draw time: near-black outlines become light, and dark/low-lightness data colours are lifted (same hue). */
  var STROKE = { "#010658": "#e6e6e6", "#1d2b34": "#cccccc", "#1b0430": "#e3cff5", "#514cf1": "#8ab4ff", "#2f6db5": "#5ea5ff",
    "#a14d00": "#e8912f", "#0072b2": "#3aa0e6", "#d55e00": "#ee7d2c", "#009e73": "#2fc79a", "#7f7f7f": "#a8a8a8",
    "#9aa7ae": "#9aa7ae", "#145c64": "#4fd1c0", "#b42318": "#f2574a", "#b2182b": "#ff6b6b", "#000000": "#cccccc" };
  var FILL = { "#514cf1": "#8ab4ff", "#2f6db5": "#5ea5ff", "#0072b2": "#3aa0e6", "#d55e00": "#ee7d2c", "#009e73": "#2fc79a",
    "#7f7f7f": "#a8a8a8", "#b42318": "#f2574a" };
  var L = window.L;
  if (L && L.Canvas && L.Canvas.prototype._fillStroke) {
    var orig = L.Canvas.prototype._fillStroke;
    L.Canvas.prototype._fillStroke = function (ctx, layer) {
      if (current() !== "dark") return orig.call(this, ctx, layer);
      var o = layer.options, c0 = o.color, f0 = o.fillColor;
      var k = typeof c0 === "string" ? c0.toLowerCase() : "", fk = typeof (f0 || c0) === "string" ? (f0 || c0).toLowerCase() : "";
      if (STROKE[k]) o.color = STROKE[k];
      if (FILL[fk]) o.fillColor = FILL[fk]; else if (!f0 && o.color !== c0) o.fillColor = c0;
      try { return orig.call(this, ctx, layer); } finally { o.color = c0; o.fillColor = f0; }
    };
  }
  function redrawVectors() {
    try {
      var m = window.EquiFire && window.EquiFire.map;
      if (m && m._renderers) Object.keys(m._renderers).forEach(function (k) { var r = m._renderers[k]; if (r && r._draw) { r._redrawBounds = null; r._clear(); r._draw(); } });
    } catch (_) {}
  }
  function current() { return root.getAttribute("data-theme") === "light" ? "light" : "dark"; }
  function apply(t, persist) {
    root.setAttribute("data-theme", t);
    var m = document.querySelector('meta[name="theme-color"]');
    if (m) m.content = t === "dark" ? "#181818" : "#ffffff";
    if (persist) { try { localStorage.setItem(KEY, t); } catch (_) {} }
    if (btn) {
      var next = t === "dark" ? "light" : "dark";
      btn.setAttribute("aria-label", "Switch to " + next + " theme");
      btn.title = "Switch to " + next + " theme";
    }
    redrawVectors();
    try { window.dispatchEvent(new CustomEvent("equifire:theme", { detail: t })); } catch (_) {}
  }
  if (btn) btn.addEventListener("click", function () { apply(current() === "dark" ? "light" : "dark", true); });
  apply(current(), false);
})();
