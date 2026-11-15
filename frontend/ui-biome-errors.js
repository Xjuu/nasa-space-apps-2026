/* Biome Error Analysis (#27).
   Systematic error decomposition across 6 distinct terrestrial biomes from historical replays.
   Connects to GET /replay/biome-errors and /replay/biome-errors/summary.
*/
(function () {
  "use strict";
  const EF = window.EquiFire;
  if (!EF) return;
  const { esc, icon, getJSON } = EF;

  let mounted = false;
  let currentData = null;
  let selectedDay = "all"; // "1", "2", "3", or "all"
  let selectedMetric = "strict"; // "strict" or "tol"

  function fmtPct(v) {
    if (v == null || !isFinite(v)) return "b/d";
    return Number(v).toFixed(1) + "%";
  }

  function getBadgeColor(val) {
    if (val >= 40) return "#15803d"; // green
    if (val >= 25) return "#2563eb"; // blue
    if (val >= 15) return "#d97706"; // amber
    return "#dc2626"; // red
  }

  function buildUI(container) {
    if (mounted) return;
    mounted = true;

    const wrap = document.createElement("details");
    wrap.className = "methodology rp-details";
    wrap.id = "rp-biome-details";
    wrap.setAttribute("open", "");
    wrap.innerHTML = `
      <summary style="cursor:pointer;font-weight:600;display:flex;align-items:center;gap:8px;">
        <span style="font-size:1.15em;">🌍</span>
        <span data-i18n="Biome error analysis">Biome error analysis (Idea #27)</span>
        <span style="font-size:0.75rem;padding:2px 6px;border-radius:999px;background:rgba(37,99,235,0.15);color:var(--primary,#2563eb);font-weight:500;">6 Biomes</span>
      </summary>
      <div style="padding:10px 0;">
        <p class="small muted" style="margin-bottom:10px;" data-i18n="Systematic error decomposition across 6 distinct terrestrial biomes from historical replays.">
          Systematic error decomposition across 6 distinct terrestrial biomes from historical replays. Strictly diagnostic: NO online test-set retraining or weight modification.
        </p>

        <!-- Filter bar -->
        <div style="display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin-bottom:12px;padding:8px 12px;background:rgba(128,128,128,0.06);border-radius:6px;">
          <div style="display:flex;align-items:center;gap:6px;">
            <span class="small" style="font-weight:600;" data-i18n="Day:">Horizon:</span>
            <div class="seg-btn-group" role="group" aria-label="Horizon selection">
              <button type="button" class="seg-btn active" data-bday="all">All Days</button>
              <button type="button" class="seg-btn" data-bday="1">Day 1</button>
              <button type="button" class="seg-btn" data-bday="2">Day 2</button>
              <button type="button" class="seg-btn" data-bday="3">Day 3</button>
            </div>
          </div>
          <div style="display:flex;align-items:center;gap:6px;">
            <span class="small" style="font-weight:600;" data-i18n="Metric:">Metric:</span>
            <div class="seg-btn-group" role="group" aria-label="Metric mode">
              <button type="button" class="seg-btn active" data-bmetric="strict">Strict IoU</button>
              <button type="button" class="seg-btn" data-bmetric="tol">Within 5 km</button>
            </div>
          </div>
          <button type="button" id="rp-biome-reload" class="tool-btn" style="margin-left:auto;padding:3px 8px;font-size:0.8rem;">
            ${icon ? icon("refresh-cw") : "🔄"} <span>Refresh</span>
          </button>
        </div>

        <!-- Cross-biome KPI benchmarks -->
        <div id="rp-biome-benchmarks" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px;margin-bottom:14px;">
          <!-- Loaded dynamically -->
        </div>

        <!-- Main Comparative Table -->
        <div class="rp-table-wrap" style="overflow-x:auto;margin-bottom:14px;">
          <table class="rp-table" id="rp-biome-table" style="width:100%;font-size:0.85rem;">
            <thead>
              <tr style="border-bottom:1px solid rgba(128,128,128,0.2);text-align:left;">
                <th style="padding:6px 8px;">Biome / Incident</th>
                <th style="padding:6px 8px;text-align:center;">Selected Score</th>
                <th style="padding:6px 8px;text-align:center;">Precision</th>
                <th style="padding:6px 8px;text-align:center;">Recall</th>
                <th style="padding:6px 8px;text-align:center;" title="Fraction of predicted false alarms falling under ERA5 cloud proxy > 80%">Unverified FP Share</th>
                <th style="padding:6px 8px;">Dominant Failure Mode</th>
              </tr>
            </thead>
            <tbody id="rp-biome-table-body">
              <tr><td colspan="6" style="padding:12px;text-align:center;color:var(--muted,#888);">Loading biome error benchmark…</td></tr>
            </tbody>
          </table>
        </div>

        <!-- Detailed Card Breakdown -->
        <div id="rp-biome-cards" style="display:grid;grid-template-columns:1fr;gap:10px;">
          <!-- Dynamically populated -->
        </div>

        <p class="small muted" style="margin-top:12px;font-style:italic;">
          * Note: Cell-level strict IoU inherently penalizes dispersed agricultural ignitions (Punjab) and cloud-masked peatlands (Kalimantan). Within-5km neighborhood matching uses 3×3 binary dilation on the 0.05° grid (~5.5 km orthogonal, ~7.8 km diagonal) to measure regional plume proximity without requiring sub-kilometer perfection. Cloud adjustment is an asymmetric filter on predicted false alarms under ERA5 cloud cover (>80%), not a symmetric observable ground-truth mask.
        </p>
      </div>
    `;

    container.appendChild(wrap);

    // Event listeners
    wrap.querySelectorAll("[data-bday]").forEach((btn) => {
      btn.addEventListener("click", () => {
        wrap.querySelectorAll("[data-bday]").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        selectedDay = btn.getAttribute("data-bday");
        renderData();
      });
    });

    wrap.querySelectorAll("[data-bmetric]").forEach((btn) => {
      btn.addEventListener("click", () => {
        wrap.querySelectorAll("[data-bmetric]").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        selectedMetric = btn.getAttribute("data-bmetric");
        renderData();
      });
    });

    const reloadBtn = document.getElementById("rp-biome-reload");
    if (reloadBtn) reloadBtn.addEventListener("click", fetchData);

    fetchData();
  }

  async function fetchData() {
    const tbody = document.getElementById("rp-biome-table-body");
    if (tbody) tbody.innerHTML = `<tr><td colspan="6" style="padding:12px;text-align:center;color:var(--muted,#888);">Loading biome error benchmark…</td></tr>`;

    try {
      const data = await getJSON("/replay/biome-errors", { objective: "iou" });
      if (data && data.status === "success") {
        currentData = data;
        renderData();
      } else {
        throw new Error(data.detail || "Unable to load biome error matrix");
      }
    } catch (err) {
      if (tbody) tbody.innerHTML = `<tr><td colspan="6" style="padding:12px;text-align:center;color:#dc2626;">Error: ${esc(err.message)}</td></tr>`;
    }
  }

  function renderData() {
    if (!currentData || !currentData.biomes) return;

    const bms = currentData.cross_biome_benchmarks || {};
    const bmBox = document.getElementById("rp-biome-benchmarks");
    if (bmBox) {
      bmBox.innerHTML = `
        <div style="padding:8px;border-radius:6px;background:rgba(128,128,128,0.06);border:1px solid rgba(128,128,128,0.15);text-align:center;">
          <div class="small muted" style="font-size:0.75rem;">Mean Day 1 Strict</div>
          <div style="font-size:1.1rem;font-weight:700;color:var(--text,#222);">${fmtPct(bms.mean_day_1_strict_iou_pct)}</div>
        </div>
        <div style="padding:8px;border-radius:6px;background:rgba(128,128,128,0.06);border:1px solid rgba(128,128,128,0.15);text-align:center;">
          <div class="small muted" style="font-size:0.75rem;">Mean Day 3 Strict</div>
          <div style="font-size:1.1rem;font-weight:700;color:var(--text,#222);">${fmtPct(bms.mean_day_3_strict_iou_pct)}</div>
        </div>
        <div style="padding:8px;border-radius:6px;background:rgba(128,128,128,0.06);border:1px solid rgba(128,128,128,0.15);text-align:center;">
          <div class="small muted" style="font-size:0.75rem;">Mean Day 3 Within-5km</div>
          <div style="font-size:1.1rem;font-weight:700;color:#15803d;">${fmtPct(bms.mean_day_3_tolerant_within_5km_pct)}</div>
        </div>
        <div style="padding:8px;border-radius:6px;background:rgba(128,128,128,0.06);border:1px solid rgba(128,128,128,0.15);text-align:center;">
          <div class="small muted" style="font-size:0.75rem;">Mean Recall</div>
          <div style="font-size:1.1rem;font-weight:700;color:var(--primary,#2563eb);">${fmtPct(bms.mean_cumulative_recall_pct)}</div>
        </div>
      `;
    }

    const tbody = document.getElementById("rp-biome-table-body");
    const cards = document.getElementById("rp-biome-cards");
    if (!tbody || !cards) return;

    let rowsHtml = "";
    let cardsHtml = "";

    const lang = document.documentElement.lang === "pl" ? "pl" : "en";

    currentData.biomes.forEach((b) => {
      const sum = b.cumulative_summary;
      let scoreVal, precVal, recVal, cloudVal, currentFp;

      if (selectedDay === "all") {
        scoreVal = selectedMetric === "strict" ? sum.cumulative_strict_iou_pct : sum.day_3_tolerant_5km_pct;
        precVal = sum.cumulative_strict_precision_pct;
        recVal = sum.cumulative_strict_recall_pct;
        cloudVal = sum.unverifiable_false_alarm_fraction_pct;
        currentFp = sum.total_false_alarms;
      } else {
        const dNum = parseInt(selectedDay, 10);
        const dObj = (b.days || []).find((d) => d.day === dNum);
        if (dObj) {
          scoreVal = selectedMetric === "strict" ? dObj.strict.iou_pct : dObj.tolerant_5km.within_5km_pct;
          precVal = selectedMetric === "strict" ? dObj.strict.precision_pct : dObj.tolerant_5km.precision_pct;
          recVal = selectedMetric === "strict" ? dObj.strict.recall_pct : dObj.tolerant_5km.recall_pct;
          cloudVal = dObj.observability.unverifiable_false_alarm_share_pct;
          currentFp = selectedMetric === "strict" ? dObj.strict.false_alarms : dObj.tolerant_5km.false_alarms;
        } else {
          scoreVal = 0; precVal = 0; recVal = 0; cloudVal = 0; currentFp = 0;
        }
      }

      const scoreColor = getBadgeColor(scoreVal);
      const biomeTitle = lang === "pl" ? b.biome_name_pl : b.biome_name;
      const incTitle = lang === "pl" ? b.incident_name_pl : b.incident_name;
      const modeLabel = lang === "pl" ? b.failure_mode_label_pl : b.failure_mode_label;
      const insight = lang === "pl" ? b.diagnostic_insight_pl : b.diagnostic_insight;
      const fuelRegime = lang === "pl" ? b.fuel_regime_pl : b.fuel_regime;

      const modelMethodBadge = b.model_method === "ml"
        ? `<span style="display:inline-block;font-size:0.68rem;padding:1px 5px;border-radius:3px;background:rgba(217,119,6,0.15);color:#b45309;font-weight:600;" title="Direct XGBoost ML-only (smoldering peatland)">ML-only</span>`
        : `<span style="display:inline-block;font-size:0.68rem;padding:1px 5px;border-radius:3px;background:rgba(37,99,235,0.12);color:var(--primary,#2563eb);font-weight:600;" title="Coupled ML + Rothermel surface fire physics">ML+Physics</span>`;

      let cloudDisplay = "—";
      if (currentFp === 0) {
        cloudDisplay = `<span style="color:var(--muted,#888);font-size:0.75rem;">— (No FP)</span>`;
      } else if (cloudVal != null && isFinite(cloudVal)) {
        if (cloudVal > 0) {
          cloudDisplay = `<span style="font-weight:600;color:${cloudVal > 20 ? "#d97706" : "inherit"};">${fmtPct(cloudVal)}</span>`;
        } else {
          cloudDisplay = `<span style="color:var(--muted,#888);">0.0%</span>`;
        }
      }

      rowsHtml += `
        <tr style="border-bottom:1px solid rgba(128,128,128,0.1);">
          <td style="padding:8px 8px;">
            <div style="font-weight:600;color:var(--text,#222);">${esc(biomeTitle)}</div>
            <div style="display:flex;align-items:center;gap:6px;margin-top:2px;">
              <span class="small muted" style="font-size:0.75rem;">${esc(incTitle)}</span>
              ${modelMethodBadge}
            </div>
          </td>
          <td style="padding:8px;text-align:center;">
            <span style="display:inline-block;padding:2px 8px;border-radius:4px;font-weight:700;background:${scoreColor}22;color:${scoreColor};">
              ${fmtPct(scoreVal)}
            </span>
          </td>
          <td style="padding:8px;text-align:center;">${fmtPct(precVal)}</td>
          <td style="padding:8px;text-align:center;font-weight:600;">${fmtPct(recVal)}</td>
          <td style="padding:8px;text-align:center;">
            ${cloudDisplay}
          </td>
          <td style="padding:8px;font-size:0.8rem;">
            <span style="padding:2px 6px;border-radius:4px;background:rgba(128,128,128,0.08);color:var(--text,#333);">
              ${esc(modeLabel)}
            </span>
          </td>
        </tr>
      `;

      cardsHtml += `
        <div style="border:1px solid rgba(128,128,128,0.15);border-radius:6px;padding:10px 12px;background:rgba(128,128,128,0.03);">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:4px;">
            <div style="font-weight:700;font-size:0.95rem;color:var(--text,#111);">${esc(biomeTitle)} — <span style="font-weight:normal;color:var(--muted,#666);">${esc(incTitle)}</span></div>
            <span style="font-size:0.75rem;padding:2px 6px;border-radius:4px;background:${scoreColor}20;color:${scoreColor};font-weight:600;">
              ${selectedMetric === "strict" ? "Strict" : "Within 5km"}: ${fmtPct(scoreVal)}
            </span>
          </div>
          <div class="small muted" style="margin-bottom:6px;display:flex;flex-wrap:wrap;gap:8px;align-items:center;">
            <span><strong>Fuel & regime:</strong> ${esc(fuelRegime)}</span>
            <span>|</span>
            <span><strong>Model method:</strong> ${b.model_method === "ml" ? "Direct XGBoost (ML-only)" : "Coupled ML + Rothermel physics"}</span>
          </div>
          <div class="small" style="line-height:1.4;color:var(--text,#333);margin-bottom:6px;">
            <strong>Diagnosis:</strong> ${esc(insight)}
          </div>
          <div style="display:flex;flex-wrap:wrap;gap:12px;font-size:0.75rem;color:var(--muted,#666);padding-top:4px;border-top:1px dashed rgba(128,128,128,0.15);">
            <span>Pred cells: <strong>${sum.total_predicted_cells}</strong></span>
            <span>Observed cells: <strong>${sum.total_observed_cells}</strong></span>
            <span>Hits: <strong style="color:#15803d;">${sum.total_hits}</strong></span>
            <span>Misses (FN): <strong style="color:#dc2626;">${sum.total_misses}</strong></span>
            <span>False alarms (FP): <strong style="color:#d97706;">${sum.total_false_alarms}</strong></span>
            ${sum.total_unverifiable_cells > 0 ? `<span style="color:#2563eb;">Unverified FP (ERA5 cloud > 80%): <strong>${sum.total_unverifiable_cells}</strong> (${fmtPct(sum.unverifiable_false_alarm_fraction_pct)})</span>` : ""}
          </div>
        </div>
      `;
    });

    tbody.innerHTML = rowsHtml;
    cards.innerHTML = cardsHtml;
  }

  function tryInit() {
    // Attach inside #replay element
    const replayContainer = document.getElementById("replay");
    if (!replayContainer) return false;

    // Look for details container inside #replay
    const targetDetails = document.getElementById("rp-method-details") || replayContainer.querySelector(".methodology");
    if (targetDetails && targetDetails.parentNode) {
      buildUI(targetDetails.parentNode);
      return true;
    }

    // Fallback: append directly to replay
    buildUI(replayContainer);
    return true;
  }

  // Poll until DOM is ready
  let tries = 0;
  const iv = setInterval(() => {
    tries++;
    if (tryInit() || tries > 40) clearInterval(iv);
  }, 350);

  // Hook tab switch
  const tabReplay = document.getElementById("tab-replay");
  if (tabReplay) {
    tabReplay.addEventListener("click", () => {
      setTimeout(tryInit, 200);
    });
  }
})();
