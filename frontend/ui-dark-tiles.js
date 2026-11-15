/* Dark theme basemap without a CSS filter.
   The dark theme used to invert the OSM tiles with a chain of five CSS filters over the whole tile layer, which repaints very slowly while panning
   in Firefox on Windows. Now the dark theme swaps the base layer to the ready-made keyless Esri "World Dark Gray" basemap (plus its label layer);
   the light theme keeps the standard OSM tiles. The old CSS filter rule only matches openstreetmap.org tiles, so it no longer applies in dark mode. */
(function () {
  "use strict";
  const EF = window.EquiFire;
  if (!EF || !EF.map || !window.L) return;
  const OSM = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
  const BASE = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}";
  const REF = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}";
  const ESRI = "Tiles &copy; Esri, HERE, Garmin, &copy; OpenStreetMap contributors";
  let layer = null, ref = null, added = false;
  function find() {
    if (layer) return layer;
    EF.map.eachLayer((l) => { if (!layer && l._url && /tile\.openstreetmap\.org|World_Dark_Gray_Base/.test(l._url)) layer = l; });
    return layer;
  }
  function apply() {
    const l = find(); if (!l) return;
    const dark = document.documentElement.getAttribute("data-theme") === "dark";
    const want = dark ? BASE : OSM;
    if (l._url !== want) {
      l.options.maxNativeZoom = dark ? 16 : undefined;
      l.setUrl(want);
    }
    if (dark) {
      if (!ref) ref = L.tileLayer(REF, { maxNativeZoom: 16, maxZoom: 19, pane: "tilePane", zIndex: 2, opacity: 0.9 });
      if (!EF.map.hasLayer(ref) && EF.map.hasLayer(l)) ref.addTo(EF.map);
      if (!added) { EF.map.attributionControl.addAttribution(ESRI); added = true; }
    } else {
      if (ref && EF.map.hasLayer(ref)) EF.map.removeLayer(ref);
      if (added) { EF.map.attributionControl.removeAttribution(ESRI); added = false; }
    }
  }
  new MutationObserver(apply).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  apply();
  setTimeout(apply, 800);
})();
