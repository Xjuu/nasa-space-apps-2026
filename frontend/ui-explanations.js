/* Rule-based explanation of the combined forecast; descriptive, not causal attribution. */
(function () {
  "use strict";
  const EF = window.EquiFire, FX = window.EquiFireFX;
  if (!EF || !FX) return;
  const { esc } = EF;
  const sec = FX.section({ id: "why-forecast", title: "Why this forecast?", icon: "info", order: 58 });
  const tr = (pl, en) => document.documentElement.lang === "pl" ? pl : en;
  let latest = null;

  function row(title, detail) {
    return `<li><strong>${esc(title)}</strong> ${esc(detail)}</li>`;
  }

  function render(data) {
    latest = data || null;
    if (!latest || !latest.guidance) {
      sec.body.innerHTML = `<p class="fx-note" role="status">${esc(tr(
        "Uruchom prognozę ML + fizyka, aby zobaczyć jej źródła i reguły.",
        "Run an ML + physics forecast to see its inputs and selection rules."))}</p>`;
      return;
    }

    const g = latest.guidance, hist = g.history || {}, model = g.model || {}, method = g.method || {};
    const q = g.query || {}, perDay = g.per_day || [];
    const frames = (latest.frames || []).filter((f) => f && f.weather);
    const first = frames[0] || {}, last = frames[frames.length - 1] || {};
    const weather = first.weather || {};
    const ignition = latest.ignition || {};
    const chosen = (g.features || []).filter((f) => f.properties && f.properties.selected);
    const top = chosen.slice().sort((a, b) =>
      (b.properties.combined_probability || 0) - (a.properties.combined_probability || 0)).slice(0, 3);
    const fmt = (n, digits) => Number.isFinite(Number(n)) ? Number(n).toFixed(digits) : "n/a";
    const ignitionSource = hist.ignition_source || ignition.mode || "not reported";
    const reduced = hist.reduced === true;
    const weatherSource = (latest.weather && latest.weather.sources || [])
      .map((s) => s.name || s.api || s.model).filter(Boolean).join(", ");
    const mode = reduced === true ? tr("live feed (reduced history)", "live feed (reduced history)") :
      reduced === false ? tr("archive history", "archive history") : tr("history mode not reported", "history mode not reported");
    const physicsMeta = latest.weather || {};
    const bullets = [
      row(tr("Start", "Ignition"), `${ignitionSource}${hist.ignition_shifted_days ? `; shifted by ${hist.ignition_shifted_days} day(s)` : ""}.`),
      row(tr("History", "History"), `${mode}${hist.as_of_utc ? `; cutoff ${hist.as_of_utc}` : ""}.`),
      row(tr("Daily model", "Daily model"), `${model.blender || "combined model"}${model.fit_year ? `; fitted ${model.fit_year}` : ""}; ${method.selection_objective || "configured"} selection objective. ${g.features ? `${chosen.length} cells passed the selection rule.` : ""}`),
      row(tr("Hourly physics", "Hourly physics"), `${physicsMeta.weather_source || weatherSource || "weather source not reported"}; ${latest.fuel && latest.fuel.class || q.fuel || "fuel not reported"}; ${last.burned_km2 != null ? `${fmt(last.burned_km2, 2)} km² simulated by the final frame` : "final simulated area unavailable"}.`),
    ];
    if (weather.wind_kmh != null || weather.rh != null) {
      bullets.push(row(tr("Initial weather input", "Initial weather input"),
        `${weather.wind_kmh != null ? `${fmt(weather.wind_kmh, 1)} km/h wind` : ""}${weather.wind_dir_deg != null ? `, ${fmt(weather.wind_dir_deg, 0)}°` : ""}${weather.rh != null ? `, ${fmt(weather.rh, 0)}% RH` : ""}.`));
    }

    const ruleRows = perDay.slice(0, 3).map((day) => row(
      `${tr("Dzień", "Day")} ${day.day}`,
      `${day.selected_cells} ${tr("komórek wybranych przez ustaloną regułę", "cells selected by the fixed rule")} (${Array.isArray(day.rule) ? day.rule.join(", ") : "rule details unavailable"}).`
    ));
    const topRows = top.map((feature) => {
      const p = feature.properties;
      return row(`${tr("Dzień", "Day")} ${p.day}`,
        `${tr("combined model score", "combined model score")} ${fmt(p.combined_probability, 3)} (uncalibrated); ML ${fmt(p.ml_probability, 3)}, physics burned fraction ${fmt(p.physics_burned_fraction, 3)}.`);
    });
    const warnings = (latest.warnings || []).slice(0, 5);
    const caveats = [
      tr("To deterministyczne podsumowanie zapisanych pól i reguł, nie wyjaśnienie przyczynowe ani wynik SHAP/atrybucja cech.",
        "This is a deterministic summary of stored fields and rules, not a causal explanation or SHAP/feature attribution."),
      tr("Wyniki komórek nie są skalibrowanym prawdopodobieństwem pożaru. Scenariusze fizyczne nie są przedziałem ufności.",
        "Cell scores are not calibrated fire probabilities. Physical scenarios are not confidence intervals."),
      ...warnings,
    ];

    sec.body.innerHTML = `<p class="fx-note">${esc(tr(
      "Wyjaśnienie oparte na regułach. Pokazuje pochodzenie danych, ustawienia modelu i wartości wejściowe; nie zmienia prognozy.",
      "Rule-based explanation. It shows data provenance, model settings and input values; it does not change the forecast."))}</p>` +
      `<h3>${esc(tr("Dane i przebieg", "Inputs and process"))}</h3><ul>${bullets.join("")}</ul>` +
      `<h3>${esc(tr("Reguły wyboru komórek", "Cell-selection rules"))}</h3><ul>${ruleRows.length ? ruleRows.join("") : `<li>${esc(tr("Brak szczegółów reguł dla tej odpowiedzi.", "No selection-rule details in this response."))}</li>`}</ul>` +
      `<h3>${esc(tr("Najwyższe wybrane wyniki", "Highest selected scores"))}</h3><ul>${topRows.length ? topRows.join("") : `<li>${esc(tr("Brak wybranych komórek.", "No selected cells."))}</li>`}</ul>` +
      `<h3>${esc(tr("Ograniczenia", "Limitations"))}</h3><ul>${caveats.map((text) => `<li>${esc(text)}</li>`).join("")}</ul>`;
  }

  EF.on("load", (data) => render(data));
  EF.on("reset", () => render(null));
  EF.on("point", () => render(null));
  document.addEventListener("equifire:langchange", () => render(latest));
  render(null);
})();
