/* Detection-age filter + time-lapse for the Fire detections card.
   Slider: show only detections from the last N hours (1-24). Time-lapse: detections appear oldest first, so the last day unfolds on the map. */
(function () {
  "use strict";
  const EF = window.EquiFire;
  const host = document.getElementById("btn-global-fires");
  if (!EF || !EF.refilterFires || !host) return;
  const wrap = document.createElement("div");
  wrap.className = "af-wrap";
  wrap.innerHTML = `<div class="af-row"><label for="af-range" class="af-lbl">Detected within the last <b id="af-val">24</b> h</label><button type="button" id="af-play" class="cd-btn" aria-pressed="false">▶ Time-lapse</button></div>` +
    `<input id="af-range" type="range" min="1" max="24" step="1" value="24" aria-describedby="af-count"><p id="af-count" class="muted small" role="status"></p>`;
  host.insertAdjacentElement("beforebegin", wrap);
  const rng = wrap.querySelector("#af-range"), val = wrap.querySelector("#af-val"), cnt = wrap.querySelector("#af-count"), play = wrap.querySelector("#af-play"), st = EF.state;
  let timer = null;
  function show(extra) {
    const r = EF.refilterFires();
    cnt.textContent = r.total ? `${r.shown} of ${r.total} detections shown${extra || ""}` : "";
  }
  function reset() { st.ageMaxH = 24; st.ageMinH = 0; rng.value = "24"; val.textContent = "24"; }
  function stop(keep) {
    clearInterval(timer); timer = null; play.setAttribute("aria-pressed", "false"); play.textContent = "▶ Time-lapse";
    if (!keep) { st.ageMinH = 0; }
  }
  rng.addEventListener("input", () => { stop(); st.ageMaxH = +rng.value; val.textContent = rng.value; show(); });
  play.addEventListener("click", () => {
    if (timer) { stop(true); show(" (paused)"); return; }
    play.setAttribute("aria-pressed", "true"); play.textContent = "⏸ Pause";
    st.ageMaxH = 24; rng.value = "24"; val.textContent = "24";
    let floor = st.ageMinH > 0 ? st.ageMinH : 24;       // detections at least `floor` hours old are visible; the floor falls to 0
    const tick = () => {
      st.ageMinH = floor;
      show(floor > 0 ? ` · showing ${Math.round(floor)} h ago and older` : " · now");
      if (floor <= 0) { stop(); st.ageMinH = 0; return; }
      floor -= 1;
    };
    tick(); timer = setInterval(tick, 450);
  });
  EF.on("area", () => { stop(); reset(); cnt.textContent = ""; });
})();
