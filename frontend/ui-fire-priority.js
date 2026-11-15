/* Fire Priority Ranking & Multi-Incident Triage (#20).
   Connects to backend/fire_priority.py: GET /priority/presets, GET/POST /priority/rank. */
(function () {
  "use strict";
  const EF = window.EquiFire, FX = window.EquiFireFX;
  if (!EF || !FX) return;
  const { map, esc } = EF;
  const $ = (id) => document.getElementById(id);

  const sec = FX.section({ id: "priority", title: "Fire priority ranking (#20)", icon: "list", order: 56 });
  sec.body.innerHTML = FX.warn("Multi-attribute screening triage (settlement proximity 40%, spread rate 30%, radiative power 18%, fuel dryness 12%). Uncalibrated decision-support heuristic.") +
    `<p class="fx-note">Regional wildfire incident triage: classifies concurrent fires into relative screening tiers (P1 Critical to P4 Routine). Strictly advisory: requires certified incident commander assessment; DO NOT use as an automated evacuation order or tactical dispatch directive.</p>` +
    `<div class="fx-grid2">` +
    `<label class="input-field"><span>Regional scenario</span><select id="fx-pr-preset"></select></label>` +
    `<button type="button" id="fx-pr-run-btn" class="btn-primary" style="align-self:flex-end;margin-bottom:2px;">📋 <span>Compute priority ranking</span></button>` +
    `</div>` +
    `<div id="fx-pr-slot"></div><p id="fx-pr-status" class="fx-note" role="status">Ready for priority analysis.</p><div id="fx-pr-out"></div>`;

  const job = FX.job($("fx-pr-slot")), statusEl = $("fx-pr-status"), out = $("fx-pr-out");
  const presetSel = $("fx-pr-preset"), runBtn = $("fx-pr-run-btn");
  const priorityGroup = L.layerGroup();

  async function loadPresets() {
    try {
      const data = await FX.fetchJSON("/priority/presets");
      if (data && data.presets) {
        presetSel.innerHTML = "";
        data.presets.forEach((p) => {
          const opt = document.createElement("option");
          opt.value = p.id;
          const isPl = (document.documentElement.lang === "pl");
          const label = isPl ? (p.name_pl || p.name) : p.name;
          opt.textContent = `${label} (${p.incident_count})`;
          presetSel.appendChild(opt);
        });
      }
    } catch (e) {
      console.warn("Failed to load priority presets:", e);
    }
  }

  async function executeRanking() {
    if (job.busy) return;
    runBtn.disabled = true;
    statusEl.textContent = "Computing multi-attribute priority scores…";

    try {
      const data = await job.run(
        "Incident priority triage…",
        "Weighted multi-attribute aggregation of exposure, forward ROS and FRP.",
        (signal) => FX.fetchJSON(`/priority/rank?preset=${presetSel.value}`, undefined, { signal })
      );
      if (data === null) { statusEl.textContent = "Cancelled."; return; }
      statusEl.textContent = "";
      renderResults(data);
      plotOnMap(data);
    } catch (e) {
      statusEl.innerHTML = `<span class="fx-err">Priority evaluation failed: ${esc(e.message)}</span>`;
    } finally {
      runBtn.disabled = false;
    }
  }

  function plotOnMap(data) {
    priorityGroup.clearLayers();
    const incs = data.top_priority_incidents || [];
    incs.forEach((inc) => {
      if (inc.lat && inc.lon) {
        const marker = L.circleMarker([inc.lat, inc.lon], {
          radius: inc.priority_tier === "P1" ? 10 : (inc.priority_tier === "P2" ? 8 : 6),
          color: inc.badge_color,
          fillColor: inc.badge_color,
          fillOpacity: 0.85,
          weight: 2,
        }).bindPopup(
          `<b>#${inc.priority_rank} ${esc(inc.name)}</b><br>` +
          `Priority: <b style="color:${inc.badge_color}">${inc.priority_tier} (${inc.composite_score} pts)</b><br>` +
          `Nearest settlement: ${esc(inc.raw_attributes.nearest_settlement)} (${inc.raw_attributes.dist_to_settlement_km} km)<br>` +
          `ROS: ${inc.raw_attributes.ros_m_min} m/min | FRP: ${inc.raw_attributes.frp_mw} MW`
        );
        priorityGroup.addLayer(marker);
      }
    });
    priorityGroup.addTo(map);
  }

  function renderResults(data) {
    const sum = data.summary;
    const incs = data.top_priority_incidents || [];
    const isPl = (document.documentElement.lang === "pl");

    let html = `<div class="fx-kpis" style="margin:10px 0;">` +
      `<div><b style="color:#dc2626">${sum.p1_critical_count}</b><span>P1 Critical</span></div>` +
      `<div><b style="color:#ea580c">${sum.p2_high_count}</b><span>P2 High</span></div>` +
      `<div><b style="color:#eab308">${sum.p3_elevated_count}</b><span>P3 Elevated</span></div>` +
      `<div><b style="color:#65a30d">${sum.p4_monitoring_count}</b><span>P4 Routine</span></div>` +
      `</div>`;

    html += `<div style="overflow-x:auto;margin-top:10px;"><table class="fx-table" style="width:100%;font-size:0.85rem;border-collapse:collapse;">` +
      `<thead><tr style="text-align:left;border-bottom:1px solid rgba(255,255,255,0.15);">` +
      `<th style="padding:6px;">#</th>` +
      `<th style="padding:6px;">Incident / Settlement</th>` +
      `<th style="padding:6px;">Tier</th>` +
      `<th style="padding:6px;">Score</th>` +
      `<th style="padding:6px;">ROS / FRP</th>` +
      `<th style="padding:6px;">Advisory classification</th>` +
      `</tr></thead><tbody>`;

    incs.forEach((inc) => {
      const raw = inc.raw_attributes;
      const note = isPl ? (inc.advisory_notes_pl || inc.advisory_notes) : inc.advisory_notes;
      html += `<tr style="border-bottom:1px solid rgba(255,255,255,0.06);cursor:pointer;" onclick="window.EquiFire.map.setView([${inc.lat}, ${inc.lon}], 9)">` +
        `<td style="padding:6px;font-weight:700;">#${inc.priority_rank}</td>` +
        `<td style="padding:6px;"><b>${esc(inc.name)}</b><br><span class="fx-note" style="font-size:0.75rem;">${esc(raw.nearest_settlement)} (${raw.dist_to_settlement_km} km)</span></td>` +
        `<td style="padding:6px;"><span style="background:${inc.badge_color};color:#fff;padding:2px 6px;border-radius:10px;font-weight:600;font-size:0.75rem;">${inc.priority_tier}</span></td>` +
        `<td style="padding:6px;font-weight:700;">${inc.composite_score}</td>` +
        `<td style="padding:6px;">${raw.ros_m_min} m/min<br><span class="fx-note" style="font-size:0.75rem;">${raw.frp_mw} MW</span></td>` +
        `<td style="padding:6px;font-size:0.8rem;line-height:1.3;">${esc(note)}</td>` +
        `</tr>`;
    });

    html += `</tbody></table></div>`;
    html += `<p class="fx-note" style="margin-top:8px;font-size:0.75rem;">Click an incident row to center the map on the fire front. Weights: settlement proximity 40%, forward ROS 30%, radiative power 18%, fuel dryness 12%. Uncalibrated heuristic — not an automated evacuation or dispatch order.</p>`;

    out.innerHTML = html;
  }

  runBtn.addEventListener("click", executeRanking);
  loadPresets();

  EF.on("reset", () => {
    priorityGroup.clearLayers();
    out.innerHTML = "";
    statusEl.textContent = "Ready for priority analysis.";
  });
})();
