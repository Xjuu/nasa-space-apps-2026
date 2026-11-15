/* Extra data panel: where the MODIS -> VIIRS-equivalent reconstruction disagrees with real VIIRS (GET /extra/disagreement).
   Auxiliary data, not a forecast result. */
(function () {
  "use strict";
  const EF = window.EquiFire, FX = window.EquiFireFX;
  if (!EF || !FX || !FX.extraPanel) return;
  const { esc, map } = EF;
  const p = FX.extraPanel({ id: "disagreement", title: "MODIS vs VIIRS disagreement map", tag: "extra data",
    note: "Reconstruction from MODIS compared with real VIIRS cell-days in the test years, per 1-degree tile, only where the reconstruction exists (5 processed countries). Not split by scan angle or time of day." });
  p.body.innerHTML = `<div class="fx-actions"><label class="fx-switch"><input type="checkbox" id="fx-ds-map"><span>Show tiles on the map</span></label></div>` +
    `<p id="fx-ds-status" class="fx-note" role="status"></p><div id="fx-ds-out"></div>`;
  const st = p.body.querySelector("#fx-ds-status"), out = p.body.querySelector("#fx-ds-out"), chk = p.body.querySelector("#fx-ds-map");
  const group = L.layerGroup();
  let data = null;
  const color = (r) => { const x = Math.max(-1, Math.min(1, Math.log2(Math.max(0.01, r)) / 1.5)); return x >= 0 ? `rgba(217,48,37,${0.15 + 0.5 * x})` : `rgba(26,115,232,${0.15 + 0.5 * -x})`; };
  function draw() {
    group.clearLayers();
    if (!chk.checked || !data) { group.remove(); return; }
    data.tiles.forEach((t) => L.rectangle([[t.lat0, t.lon0], [t.lat0 + 1, t.lon0 + 1]], { weight: 0, fillColor: "#000", fillOpacity: 0, color: "#000" })
      .setStyle({ stroke: false, fill: true, fillColor: color(t.ratio).replace(/rgba\((\d+),(\d+),(\d+),[\d.]+\)/, "rgb($1,$2,$3)"), fillOpacity: parseFloat(color(t.ratio).match(/,([\d.]+)\)$/)[1]) })
      .bindTooltip(`real ${t.real} · reconstructed ${t.recon} cell-days<br>ratio ${t.ratio} · recall ${t.recall} · precision ${t.precision}`).addTo(group));
    group.addTo(map);
  }
  async function load() {
    if (data) return;
    st.textContent = "Loading… (first load can take about half a minute)";
    try {
      data = await FX.fetchJSON("/extra/disagreement", {});
      const o = data.overall, top = data.tiles.slice().sort((a, b) => Math.abs(Math.log(b.ratio)) * Math.sqrt(b.real) - Math.abs(Math.log(a.ratio)) * Math.sqrt(a.real)).slice(0, 8);
      st.textContent = "";
      out.innerHTML = `<p class="fx-note">${data.years}: ${data.n_tiles} tiles. Overall the reconstruction has ${o.recon.toLocaleString("en-US")} cell-days against ${o.real.toLocaleString("en-US")} real (ratio ${o.ratio}), recall ${o.recall}, precision ${o.precision}. Red = reconstruction over-predicts, blue = under-predicts.</p>` +
        `<div class="fx-table-wrap"><table class="fx-scen-table"><thead><tr><th>Tile (lat, lon)</th><th>Real</th><th>Recon.</th><th>Ratio</th><th>Recall</th></tr></thead><tbody>` +
        top.map((t) => `<tr><td>${t.lat0}, ${t.lon0}</td><td>${t.real}</td><td>${t.recon}</td><td>${t.ratio}</td><td>${t.recall}</td></tr>`).join("") + `</tbody></table></div><p class="fx-note">${esc(data.note)}</p>`;
      draw();
    } catch (e) { st.innerHTML = `<span class="fx-err" role="alert">Failed: ${esc(e.message)}</span>`; }
  }
  chk.addEventListener("change", async () => { await load(); draw(); });
  p.onOpen(load);
})();
