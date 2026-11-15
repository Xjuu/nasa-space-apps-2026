/* Focus: the Forecast tab keeps its core tools in view and collects the experimental / less validated modules in one collapsed "Laboratory" group.
   Nothing is removed: the sections are only moved (their ids and scripts keep working). Open the group by default with ?lab=open. */
(function () {
  "use strict";
  const EF = window.EquiFire, FX = window.EquiFireFX;
  if (!EF || !FX || !FX.root) return;
  // core stays visible: nextobs, blind, scen, expo, brief, why-forecast, shared-analysis
  const LAB = ["drone", "carbon", "dbnet", "illum", "latency", "scope", "extra", "firebreak", "montecarlo", "nowcast", "priority", "riskdiag", "telegram-alert"];
  const det = document.createElement("details");
  det.id = "fx-sec-lab"; det.className = "fx-sec fx-lab"; det.style.order = 100;
  det.innerHTML = `<summary>${FX.ic("flask-conical")}<span>Laboratory: experimental modules</span><span class="fx-badge" id="fx-lab-count"></span><span class="fx-chev">${FX.ic("chevron-down")}</span></summary>` +
    `<div class="fx-sec-body"><p class="fx-note">Experimental or less validated modules (several have weak or negative results, see the README). They are not part of the main forecast result.</p><div id="fx-lab-list" class="fx-lab-list"></div></div>`;
  FX.root.appendChild(det);
  const list = det.querySelector("#fx-lab-list"), count = det.querySelector("#fx-lab-count");
  if (new URLSearchParams(location.search).get("lab") === "open") det.open = true;
  function sweep() {
    LAB.forEach((id) => { const s = document.getElementById("fx-sec-" + id); if (s && s.parentElement !== list) list.appendChild(s); });
    count.textContent = String(list.children.length);
    count.hidden = !list.children.length;
  }
  sweep();
  new MutationObserver(sweep).observe(FX.root, { childList: true });
  setTimeout(sweep, 1500);
})();
