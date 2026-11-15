/* Forecast extras: detection latency measured by our own poller (GET /latency/stats).
   Latency = our first_seen minus satellite acquisition time, resolution = polling interval. Not FIRMS' internal processing time. */
(function () {
  "use strict";
  const EF = window.EquiFire, FX = window.EquiFireFX;
  if (!EF || !FX) return;
  const { esc } = EF;
  const $ = (id) => document.getElementById(id);
  const sec = FX.section({ id: "latency", title: "Detection latency (our poller)", icon: "timer", order: 13 });
  sec.body.innerHTML = `<p class="fx-note">How long after a satellite observes a fire it shows up in the public FIRMS near-real-time feed, as seen by this server's poller (every 5 minutes). It is not FIRMS' internal processing time and not a direct-broadcast latency.</p>` +
    `<div class="fx-actions"><button type="button" id="fx-lt-run" class="btn-primary">${FX.ic("refresh-cw")}<span>Refresh</span></button></div><p id="fx-lt-status" class="fx-note" role="status"></p><div id="fx-lt-out"></div>`;
  const out = $("fx-lt-out"), st = $("fx-lt-status");
  async function load() {
    st.textContent = "Loading…";
    try {
      const d = await FX.fetchJSON("/latency/stats", {});
      st.textContent = d.note || `${d.n_detections} detections measured, ${d.polls} polls, last poll ${d.last_poll_utc || "n/a"}.`;
      const rows = Object.entries(d.per_feed || {}).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${v.n}</td><td>${v.median_min} min</td><td>${v.p90_min} min</td><td>${v.min_min}–${v.max_min} min</td></tr>`).join("");
      out.innerHTML = (rows ? `<div class="fx-table-wrap"><table class="fx-scen-table"><thead><tr><th>Feed</th><th>Detections</th><th>Median</th><th>90th percentile</th><th>Range</th></tr></thead><tbody>${rows}</tbody></table></div>` : "") +
        `<p class="fx-note">${esc(d.how_to_read)}</p>` + (d.last_error ? `<p class="fx-note">Last poll error: ${esc(d.last_error)}</p>` : "");
    } catch (e) { st.innerHTML = `<span class="fx-err" role="alert">Failed: ${esc(e.message)}</span>`; }
  }
  $("fx-lt-run").addEventListener("click", load);
  sec.onOpen(load);
})();
