/* "Extra data" container (Forecast tab). One collapsed section for auxiliary data that must not be mixed with the main
   forecast results (user decision): MODIS/VIIRS disagreement map, independent burned area (EFFIS), static heat sources,
   observation quality. Other modules add a panel with:
       const p = window.EquiFireFX.extraPanel({ id: "disagreement", title: "MODIS vs VIIRS disagreement", note: "..." });
       p.body.innerHTML = "...";   // p.el is the <details>, p.onOpen(fn) runs when the panel opens
   Every panel is labelled as auxiliary data. */
(function () {
  "use strict";
  const EF = window.EquiFire, FX = window.EquiFireFX;
  if (!EF || !FX || FX.extraPanel) return;
  const { esc } = EF;
  const sec = FX.section({ id: "extra", title: "Extra data", icon: "database", order: 90 });
  sec.body.innerHTML = `<p class="fx-note">Auxiliary data for context and checks. These panels are not part of the forecast result and are not mixed into its scores.</p><div id="fx-extra-list"></div>`;
  const list = sec.body.querySelector("#fx-extra-list");
  FX.extraPanel = function (o) {
    const d = document.createElement("details");
    d.className = "fx-extra-panel"; d.id = "fx-extra-" + o.id;
    d.innerHTML = `<summary>${esc(o.title)}<span class="fx-badge">${esc(o.tag || "extra data")}</span></summary><div class="fx-extra-body"></div>`;
    list.appendChild(d);
    const body = d.querySelector(".fx-extra-body");
    if (o.note) body.insertAdjacentHTML("beforeend", `<p class="fx-note">${esc(o.note)}</p>`);
    const content = document.createElement("div"); body.appendChild(content);
    return { el: d, body: content, onOpen(fn) { d.addEventListener("toggle", () => { if (d.open) fn(); }); } };
  };
  FX.extraSection = sec;
})();
