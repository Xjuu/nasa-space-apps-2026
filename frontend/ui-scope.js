/* Forecast extras: model scope flag (GET /scope/check). A RANGE flag, not a confidence. */
(function () {
  "use strict";
  const EF = window.EquiFire, FX = window.EquiFireFX;
  if (!EF || !FX) return;
  const { esc } = EF;
  const $ = (id) => document.getElementById(id);
  const sec = FX.section({ id: "scope", title: "Model scope flag", icon: "ruler", order: 16 });
  sec.body.innerHTML = `<p class="fx-note">Is this fire similar to the cases the spread model was trained on (size, recent burn history, amount of training data nearby)? This is a range flag, not a confidence: a case inside the range can still be predicted badly.</p>` +
    `<div class="fx-actions"><button type="button" id="fx-sc-run" class="btn-primary" disabled>${FX.ic("ruler")}<span>Check the selected fire</span></button></div><p id="fx-sc-status" class="fx-note" role="status">Select a fire on the map.</p><div id="fx-sc-out"></div>`;
  const btn = $("fx-sc-run"), st = $("fx-sc-status"), out = $("fx-sc-out");
  const refresh = () => { btn.disabled = !FX.point(); };
  EF.on("point", refresh); sec.onOpen(refresh);
  btn.addEventListener("click", async () => {
    const p = FX.point(); if (!p) return;
    btn.disabled = true; st.textContent = "Checking…";
    try {
      const d = await FX.fetchJSON("/scope/check", { lat: p.lat.toFixed(4), lon: p.lon.toFixed(4) });
      st.textContent = "";
      out.innerHTML = `<p class="fx-expo-label">${d.in_range ? "INSIDE the training range" : "OUTSIDE the training range"}</p>` +
        (d.reasons.length ? `<ul class="fx-note">${d.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : "") +
        `<p class="fx-note">Burning cells now: ${d.descriptors.n0}; burned in the last 30 days: ${d.descriptors.h30}; last 365 days: ${d.descriptors.h365}. ${esc(d.meaning)}</p>`;
    } catch (e) { st.innerHTML = `<span class="fx-err" role="alert">Failed: ${esc(e.message)}</span>`; }
    finally { refresh(); }
  });
  refresh();
})();
