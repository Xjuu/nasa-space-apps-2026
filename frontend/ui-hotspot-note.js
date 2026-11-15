/* "How to read the dots": short reading guide above the side-panel footer (collapsed by default). */
(function () {
  "use strict";
  const panel = document.getElementById("panel");
  const foot = panel && panel.querySelector(".panel-footer");
  if (!foot || document.getElementById("hotspot-note")) return;
  const d = document.createElement("details");
  d.id = "hotspot-note"; d.className = "hotspot-note";
  d.innerHTML = `<summary>How to read the dots</summary>
    <p>Each dot is a single satellite reading of heat on the ground. It tells you where something was hot at that moment, not where a fire's edge is.</p>
    <ul>
      <li><b>Clusters of dots:</b> a large fire lights up many pixels, so a rising dot count does not always mean more fires.</li>
      <li><b>Blind moments:</b> cloud, thick smoke and the hours between satellite passes can hide a fire completely.</li>
      <li><b>Below the threshold:</b> small, cool or short-lived fires may never register, most often in the coarser MODIS record.</li>
    </ul>
    <p class="hn-not"><b>Limits of FireSpot:</b> no official fire perimeters, no replacement for the NASA FIRMS map or local authorities, forecasts are uncalibrated estimates, and burned area is not measured.</p>`;
  foot.parentNode.insertBefore(d, foot);
})();
