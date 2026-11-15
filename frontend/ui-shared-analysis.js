(function () {
  "use strict";
  const EF = window.EquiFire, FX = window.EquiFireFX;
  if (!EF || !FX) return;
  const tr = (pl, en) => document.documentElement.lang === "pl" ? pl : en;
  const sec = FX.section({ id: "shared-analysis", title: "Temporary analysis link", icon: "share-2", order: 57 });
  let latest = null;
  sec.body.innerHTML =
    `<p class="fx-note">${tr("Utwórz publiczny link ważny 24 godziny. Udostępniany jest tylko krótki, zatwierdzony podgląd tekstowy — bez mapy, geometrii ani sekretów. Dane nie są umieszczane w adresie URL.", "Create a public link valid for 24 hours. Only a small, approved text summary is shared — no map, geometry or secrets. Analysis data is not placed in the URL.")}</p>` +
    `<div class="fx-actions"><button id="fx-share-create" type="button" class="tool-btn" disabled>${tr("Utwórz link", "Create link")}</button></div>` +
    `<p id="fx-share-status" class="fx-note" role="status" aria-live="polite"></p><div id="fx-share-result"></div>`;
  const create = sec.body.querySelector("#fx-share-create");
  const status = sec.body.querySelector("#fx-share-status");
  const result = sec.body.querySelector("#fx-share-result");

  function onForecast(data, query) {
    latest = data && query ? { data, query } : null;
    create.disabled = !latest;
  }
  EF.on("load", onForecast);
  EF.on("reset", () => onForecast(null, null));

  function makeSnapshot() {
    const { data, query } = latest;
    const guidance = data.guidance || {};
    const frames = Array.isArray(data.frames) ? data.frames : [];
    const last = frames[frames.length - 1] || {};
    const ros = frames.filter((frame) => frame.max_ros_m_min != null)
      .map((frame) => Number(frame.max_ros_m_min)).filter(Number.isFinite);
    return {
      version: 1,
      created_utc: new Date().toISOString(),
      mode: "Combined ML + physics forecast summary",
      location: { lat: Number(query.lat), lon: Number(query.lon) },
      query: {
        start_utc: query.start || guidance.query && guidance.query.date || query.date || undefined,
        days: guidance.query && guidance.query.days || (Number.isFinite(Number(query.hours)) ? Math.max(1, Math.ceil(Number(query.hours) / 24)) : undefined),
        fuel: data.fuel && data.fuel.class || query.fuel || undefined,
        ignition_source: guidance.history && guidance.history.ignition_source || query.ignition || undefined,
        selection_objective: guidance.method && guidance.method.selection_objective || undefined,
      },
      model: {
        blender: guidance.model && guidance.model.blender || undefined,
        fit_year: guidance.model && guidance.model.fit_year || undefined,
      },
      history: {
        reduced: guidance.history && guidance.history.reduced,
        as_of_utc: guidance.history && guidance.history.as_of_utc || undefined,
        ignition_source: guidance.history && guidance.history.ignition_source || undefined,
        ignition_shifted_days: guidance.history && guidance.history.ignition_shifted_days || undefined,
      },
      summary: {
        burned_km2: Number.isFinite(Number(last.burned_km2)) ? Number(last.burned_km2) : undefined,
        max_ros_m_min: ros.length ? Math.max(...ros) : undefined,
        frames: frames.length,
      },
      warnings: (Array.isArray(data.warnings) ? data.warnings : []).slice(0, 8).map((text) => String(text).slice(0, 500)),
    };
  }

  create.addEventListener("click", async () => {
    if (!latest || create.disabled) return;
    create.disabled = true;
    result.replaceChildren();
    status.textContent = tr("Tworzenie linku…", "Creating link…");
    try {
      const share = await FX.fetchJSON("/share", null, { method: "POST", body: makeSnapshot() });
      const url = new URL(location.pathname, location.origin);
      url.searchParams.set("shared", share.id);
      const link = document.createElement("a");
      link.href = url.href;
      link.textContent = url.href;
      link.className = "btn-download";
      link.rel = "noopener";
      result.append(link);
      status.textContent = tr(`Link wygasa: ${new Date(share.expires_at).toLocaleString()}.`, `Link expires: ${new Date(share.expires_at).toLocaleString()}.`);
    } catch (error) {
      status.textContent = error.message;
    } finally {
      create.disabled = !latest;
    }
  });

  async function loadShared(id) {
    sec.el.open = true;
    create.disabled = true;
    status.textContent = tr("Wczytywanie udostępnionego podsumowania…", "Loading shared summary…");
    try {
      const snapshot = await FX.fetchJSON(`/share/${encodeURIComponent(id)}`);
      const rows = [
        [tr("Utworzono (UTC)", "Created (UTC)"), snapshot.created_utc],
        [tr("Tryb", "Mode"), snapshot.mode],
        [tr("Lokalizacja", "Location"), `${snapshot.location.lat}, ${snapshot.location.lon}`],
        [tr("Paliwo", "Fuel"), snapshot.query.fuel],
        [tr("Pochodzenie zapłonu", "Ignition provenance"), snapshot.query.ignition_source],
        [tr("Cel selekcji", "Selection objective"), snapshot.query.selection_objective],
        [tr("Model", "Model"), snapshot.model.blender],
        [tr("Rok dopasowania", "Fit year"), snapshot.model.fit_year],
        [tr("Powierzchnia spalona w ostatniej klatce (km²)", "Burned area at final frame (km²)"), snapshot.summary.burned_km2],
        [tr("Maks. ROS (m/min)", "Max ROS (m/min)"), snapshot.summary.max_ros_m_min],
        [tr("Liczba klatek", "Frame count"), snapshot.summary.frames],
        [tr("Pochodzenie historii", "History provenance"), snapshot.history.ignition_source],
        [tr("Historia do (UTC)", "History through (UTC)"), snapshot.history.as_of_utc],
        [tr("Dane zredukowane", "Reduced history"), snapshot.history.reduced],
      ].filter(([, value]) => value != null);
      result.innerHTML = `<h3>${tr("Udostępnione podsumowanie (tylko do odczytu)", "Shared summary (read-only)")}</h3><dl>${rows.map(([label, value]) => `<dt>${EF.esc(label)}</dt><dd>${EF.esc(value)}</dd>`).join("")}</dl>` +
        (snapshot.warnings.length ? `<h3>${tr("Uwagi", "Warnings")}</h3><ul>${snapshot.warnings.map((warning) => `<li>${EF.esc(warning)}</li>`).join("")}</ul>` : "");
      status.textContent = tr("Ten link zawiera wyłącznie zapisane podsumowanie, nie aktualizuje się.", "This link contains a saved summary only; it does not update.");
    } catch (error) {
      result.replaceChildren();
      status.textContent = error.message;
    }
  }

  const sharedId = new URLSearchParams(location.search).get("shared");
  if (sharedId) loadShared(sharedId);
})();
