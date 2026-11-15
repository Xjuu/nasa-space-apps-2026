/* FireSpot: "Guide" step-by-step guided tour (English by default, Polish with the PL | EN switch). */
(function () {
  "use strict";
  var EF = function () { return window.EquiFire || {}; };

  function lang() {
    var l = (document.documentElement.getAttribute("lang") || "en").toLowerCase();
    return l.indexOf("en") === 0 ? "en" : "pl";
  }

  // mode: workspace to open before showing the step; sel: element to highlight (optional).
  var STEPS = [
    { sel: null, mode: "live",
      pl: ["Witaj w FireSpot", "To narzędzie pokazuje pożary wykryte przez satelity MODIS i VIIRS, prognozuje ich rozprzestrzenianie i porównuje bieżącą aktywność z wieloletnią normą. Przejdziemy krok po kroku przez wszystkie ekrany i funkcje (około 4 minut). Strzałki ← → przesuwają dalej, Esc zamyka."],
      en: ["Welcome to FireSpot", "This tool shows fires detected by the MODIS and VIIRS satellites, forecasts how they may spread, and compares current activity with the multi-year norm. We will go through every screen and feature step by step (about 4 minutes). Arrow keys ← → move on, Esc closes."] },
    { sel: "#headline-card", mode: "live",
      pl: ["Główny wynik projektu", "Po zwykłym sklejeniu archiwów MODIS i VIIRS liczba wykryć skacze w 2012 r. ×5,24, a pozorny trend wynosi +13,8 %/rok. Po harmonizacji skok to ×1,16, a trend −0,8 %/rok (nieistotny). Liczby czytane są na żywo z danych. Przycisk prowadzi do dowodu w Historii, a × zamyka kartę."],
      en: ["The main result", "Splicing the MODIS and VIIRS archives makes detections jump ×5.24 in 2012 and the apparent trend +13.8 %/yr. After harmonization the step is ×1.16 and the trend −0.8 %/yr (not significant). The numbers are read live from the data. The button leads to the proof in History; × closes the card."] },
    { sel: ".mode-tabs", mode: "live",
      pl: ["Pięć obszarów roboczych", "Monitor – bieżące detekcje. Prognoza – gdzie pożar może się rozprzestrzenić. Historia – wieloletnie porównania i harmonizacja. Alerty – obszary ponad normą. Powtórka – sprawdzenie prognozy na zakończonych pożarach."],
      en: ["Five workspaces", "Monitor – current detections. Forecast – where a fire may spread. History – multi-year comparisons and harmonization. Alerts – regions above their norm. Replay – check a forecast against fires that have ended."] },
    { sel: "#workspace-header .lang-switch", mode: "live",
      pl: ["Narzędzia w nagłówku", "Przełącznik języka (angielski jest domyślny, polski jedną opcją dalej), motyw ciemny/jasny, ustawienia dostępności (rozmiar tekstu, kontrast, mniej animacji), link do kodu na GitHubie i ten przewodnik. Wybory są zapamiętywane."],
      en: ["Header tools", "Language switch (English is the default, Polish is one click away), dark/light theme, accessibility settings (text size, contrast, less motion), the GitHub link and this guide. Your choices are remembered."] },
    { sel: "#area-section", mode: "live",
      pl: ["Monitor: wybierz obszar", "Narysuj prostokąt na mapie, użyj środka mapy albo wpisz współrzędne. Obszar może mieć do 25 000 km². „Quick locations” ustawiają znane regiony jednym kliknięciem."],
      en: ["Monitor: choose an area", "Draw a rectangle on the map, use the map centre, or type coordinates. The area can be up to 25,000 km². “Quick locations” set well-known regions with one click."] },
    { sel: "#map-draw-area", mode: "live",
      pl: ["Rysowanie z podglądem rozmiaru", "Przeciągnij prostokąt: na żywo widzisz „X km² / 25 000 km²”. Gdy obszar jest za duży, prostokąt robi się czerwony i pojawia się napis „too large”, więc od razu wiesz, kiedy przesadziłeś."],
      en: ["Drawing with a live size", "Drag a rectangle: you see “X km² / 25,000 km²” update as you draw. When the area is too big the rectangle turns red and says “too large”, so you know at once when you went too far."] },
    { sel: ".preset-chip", mode: "live",
      pl: ["Szybkie lokalizacje i klasa miesiąca", "Przy Amazonii i północnej Australii widać plakietkę: Quiet, Typical, Busy albo Exceptional. To pozycja ostatniego kompletnego miesiąca wśród tego samego miesiąca w innych latach. Dla regionów bez danych nie pokazujemy nic."],
      en: ["Quick locations and the month class", "Amazon and northern Australia show a badge: Quiet, Typical, Busy or Exceptional. It is the rank of the latest complete month among the same month in other years. Regions without data show nothing."] },
    { sel: "#btn-show-fires", mode: "live",
      pl: ["Wczytaj detekcje", "Przycisk pobiera pożary z ostatnich 24 godzin. Rozmiar punktu to moc promieniowania (FRP). Kliknij punkt, aby zobaczyć satelitę, czas i pewność wykrycia."],
      en: ["Load detections", "The button loads fires from the last 24 hours. Point size shows radiative power (FRP). Click a point for the satellite, time and confidence."] },
    { sel: ".map-legend", mode: "live",
      pl: ["Kolor = wiek detekcji", "Żółty: do 3 godzin temu, pomarańczowy: 3–8 h, czerwony: 8–16 h, fioletowy: powyżej 16 h. Te same kolory są na mapie, na liście detekcji i na globusie."],
      en: ["Colour = detection age", "Yellow: up to 3 hours ago, orange: 3–8 h, red: 8–16 h, purple: over 16 h. The same colours appear on the map, in the detection list and on the globe."] },
    { sel: ".af-wrap", mode: "live",
      pl: ["Filtr wieku i podgląd przyspieszony", "Suwak pokazuje tylko detekcje z ostatnich N godzin (1–24). „Time-lapse” puszcza detekcje od najstarszych do najnowszych, więc widać, jak ostatnia doba się rozwijała. Pod suwakiem jest licznik „X z Y detekcji”."],
      en: ["Age filter and time-lapse", "The slider shows only detections from the last N hours (1–24). “Time-lapse” plays detections oldest first, so you can watch the last day unfold. A counter below says “X of Y detections shown”."] },
    { sel: "#live-results", mode: "live",
      pl: ["Lista detekcji", "Najnowsze wykrycia z kolorową kropką wieku, mocą FRP, temperaturą i satelitą. Kliknięcie wpisu przybliża mapę do punktu. „Show global detections” dodaje 250 najsilniejszych detekcji na świecie."],
      en: ["Detection list", "The newest detections with a colour dot for age, FRP, temperature and satellite. Clicking an entry zooms the map to the point. “Show global detections” adds the 250 strongest detections worldwide."] },
    { sel: "#layers-btn", mode: "live",
      pl: ["Warstwy, mapa i globus", "Przełącz mapę płaską i globus 3D. W menu Warstwy włączysz orbity satelitów (Terra, Aqua, Suomi NPP, NOAA-20, NOAA-21), radar opadów i strzałki wiatru. W ciemnym motywie mapa jest gotową ciemną mapą, bez filtrów, więc przesuwa się płynnie."],
      en: ["Layers, map and globe", "Switch between the flat map and the 3D globe. The Layers menu turns on satellite orbits (Terra, Aqua, Suomi NPP, NOAA-20, NOAA-21), rain radar and wind arrows. In the dark theme the map is a ready-made dark basemap with no filters, so it pans smoothly."] },
    { sel: ".cd-bar", mode: "live",
      pl: ["Mniej miejsca w panelu", "Każda karta ma strzałkę zwijania. „Collapse all” zwija wszystko, „Expand all” rozwija, „Compact / Roomy” zmienia gęstość. Stan jest zapamiętywany."],
      en: ["Less space in the panel", "Every card has a collapse arrow. “Collapse all” folds everything, “Expand all” opens it, “Compact / Roomy” switches density. The state is remembered."] },
    { sel: "#panel-toggle", mode: "live",
      pl: ["Ukryj cały panel", "Strzałka na krawędzi panelu chowa go całkowicie, a mapa dostaje pełną szerokość. Kliknij ją ponownie, aby przywrócić panel (komputer)."],
      en: ["Hide the whole panel", "The arrow on the panel edge hides it completely and the map gets the full width. Click it again to bring the panel back (desktop)."] },
    { sel: "#hotspot-note", mode: "live", open: "#hotspot-note",
      pl: ["Jak czytać punkty", "Jeden punkt to jeden odczyt ciepła z satelity. Duży pożar zapala wiele pikseli, chmury i dym mogą ukryć ogień, a małe pożary mogą nie zostać wykryte. Tu też wypisane są ograniczenia FireSpot."],
      en: ["How to read the dots", "One dot is one satellite reading of heat. A large fire lights many pixels, cloud and smoke can hide a fire, and small fires may be missed. FireSpot's limits are listed here too."] },
    { sel: "#date-controls", mode: "date",
      pl: ["Prognoza: wybierz pożar", "Kliknij pożar na mapie, ustaw horyzont (godziny), start i model paliwa. Gdy VIIRS nie widzi ognia, używamy MODIS jako zastępczego źródła zapłonu i oznaczamy to w wyniku."],
      en: ["Forecast: pick a fire", "Click a fire on the map and set the horizon (hours), start time and fuel model. When VIIRS sees no fire, MODIS is used as the ignition source and this is stated in the result."] },
    { sel: "#btn-forecast", mode: "date",
      pl: ["Uruchom i odtwarzaj", "Prognoza łączy model ML (dzienne komórki ~5,5 km) z symulacją fizyczną (obwód godzina po godzinie z wiatrem, wilgotnością i paliwem). Odtwarzacz ma start/pauzę, kroki, suwak czasu i prędkość. Analiza trwa zwykle kilkadziesiąt sekund, a pierwsze zapytanie dla nowego miejsca do 3 minut."],
      en: ["Run and play", "The forecast combines an ML model (daily ~5.5 km cells) with a physics simulation (hour-by-hour perimeter with wind, humidity and fuel). The player has play/pause, steps, a time slider and speed. A run usually takes tens of seconds; the first request for a new place can take up to 3 minutes."] },
    { sel: "#fx-sec-nextobs", mode: "date", open: "#fx-sec-nextobs",
      pl: ["Do następnego przelotu", "Kiedy satelita znów zobaczy ten pożar, z którego satelity, i czy chmury go zasłonią. Odliczanie pomaga ocenić, ile godzin pozostaje bez nowej obserwacji."],
      en: ["Until the next pass", "When a satellite will see this fire again, from which satellite, and whether cloud may hide it. The countdown shows how many hours remain without a new observation."] },
    { sel: "#fx-sec-blind", mode: "date", open: "#fx-sec-blind",
      pl: ["Ślepe strefy", "Odróżnia „brak pożaru” od „zbyt słaba obserwacja” (chmury, kąt skanu). Mapa pokazuje komórki, w których brak detekcji nic nie dowodzi."],
      en: ["Blind spots", "Separates “no fire detected” from “insufficient observation” (cloud, scan angle). The map shows cells where the absence of a detection proves nothing."] },
    { sel: "#fx-sec-scen", mode: "date", open: "#fx-sec-scen",
      pl: ["Scenariusze pogodowe", "Silniejszy wiatr, deszcz, większa wilgotność: porównanie z podstawową prognozą. To scenariusze hipotetyczne, nie prognoza pogody."],
      en: ["Weather scenarios", "Stronger wind, rain, higher humidity: compared with the base forecast. These are hypothetical scenarios, not a weather forecast."] },
    { sel: "#fx-sec-expo", mode: "date", open: "#fx-sec-expo",
      pl: ["Ekspozycja w czasie", "Szacunek obiektów z OpenStreetMap (osady, drogi, linie energetyczne, szpitale, szkoły), które znajdą się w symulowanym obszarze, wraz z czasem dojścia ognia. To ekspozycja, nie straty, i bez wartości pieniężnych."],
      en: ["Exposure over time", "An estimate of OpenStreetMap assets (settlements, roads, power lines, hospitals, schools) inside the simulated area, with the time the fire reaches them. It is exposure, not damage, and carries no monetary value."] },
    { sel: "#fx-sec-brief", mode: "date", open: "#fx-sec-brief",
      pl: ["Raport zdarzenia (PDF)", "Jedno kliknięcie tworzy raport PDF z gotowej prognozy: mapa, oś czasu, ekspozycja, następny przelot. Generowanie trwa zwykle do kilkunastu sekund, a przy nowym obszarze do kilku minut. Każdy widok ma też własny przycisk PDF."],
      en: ["Incident brief (PDF)", "One click builds a PDF report from the finished forecast: map, timeline, exposure, next pass. Generation usually takes some seconds, and a few minutes for a new area. Every view also has its own PDF button."] },
    { sel: "#fx-sec-why-forecast", mode: "date", open: "#fx-sec-why-forecast",
      pl: ["Dlaczego ta prognoza?", "Regułowe, deterministyczne wyjaśnienie: jakie dane weszły, jaki model, jak wybrano komórki. Obok „Tymczasowy link” tworzy krótkotrwały adres do tego stanu analizy, który możesz komuś wysłać."],
      en: ["Why this forecast?", "A rule-based, deterministic explanation: which inputs went in, which model, how cells were selected. Next to it, “Temporary analysis link” makes a short-lived address for this analysis state that you can send to someone."] },
    { sel: "#fx-sec-lab", mode: "date", open: "#fx-sec-lab",
      pl: ["Laboratorium", "Moduły eksperymentalne lub słabiej zwalidowane: trasa drona (GPX/KML), licznik CO₂, Direct Broadcast, geometria Słońce–satelita, Monte Carlo, nowcasting, pasy przeciwpożarowe, ranking priorytetów, powiadomienia Telegram i dane dodatkowe. Część ma słabe wyniki i jest opisana uczciwie w README."],
      en: ["Laboratory", "Experimental or less validated modules: drone route (GPX/KML), CO₂ ticker, Direct Broadcast, sun–satellite geometry, Monte Carlo, nowcasting, firebreak sandbox, priority ranking, Telegram alerts and extra data. Some have weak results and are described honestly in the README."] },
    { sel: "#history", mode: "history",
      pl: ["Historia", "Wybierz obszar i miarę. Kalendarz pożarów pokazuje tygodnie × lata, a wykres tygodniowy porównuje ten rok ze średnią innych lat. Linie 2012 i 2018 to zmiany satelitów, nie zmiany pożarów."],
      en: ["History", "Pick an area and a measure. The burning calendar shows weeks × years and the weekly chart compares this year with the average of other years. The 2012 and 2018 lines are satellite changes, not fire changes."] },
    { sel: "#hz-compare", mode: "history", open: "#hz-section", wait: true,
      pl: ["Przed i po harmonizacji", "Suwak działa jak kurtyna: po lewej surowe sklejenie MODIS i VIIRS ze skokiem w 2012 r., po prawej zapis zharmonizowany. Pod wykresem jest skok 2011→2012 dla obu wersji."],
      en: ["Before and after harmonization", "The slider works like a curtain: on the left the raw MODIS + VIIRS splice with the 2012 jump, on the right the harmonized record. Below the chart is the 2011→2012 step for both versions."] },
    { sel: "#cg-card", mode: "history", open: "#hz-section", wait: true,
      pl: ["Siatka rok × miesiąc", "Każdy kwadrat to miesiąc. Kreskowanie oznacza miesiące odtworzone z MODIS (przed lutym 2012), pasek na górze to miesiąc intensywny lub wyjątkowy. Kliknij kwadrat, aby zobaczyć jego pozycję w rankingu tego samego miesiąca w innych latach."],
      en: ["Year-by-month grid", "Each square is a month. Hatching marks months reconstructed from MODIS (before Feb 2012); a bar on top marks a busy or exceptional month. Click a square to see its rank among the same month in other years."] },
    { sel: "#alerts", mode: "alerts",
      pl: ["Alerty", "Aktywność z ostatnich 7 dni porównana z tym samym tygodniem w latach 2008–2025. Poziomy: obserwuj (z ≥ 2), ostrzeżenie (z ≥ 3), krytyczny (z ≥ 5), z flagą „poza sezonem”. Klasyfikator odróżnia rutynowe wypalanie od anomalii. Eksport CAP 1.2 i GeoJSON."],
      en: ["Alerts", "The last 7 days compared with the same week in 2008–2025. Levels: watch (z ≥ 2), warning (z ≥ 3), critical (z ≥ 5), with an “out of season” flag. A classifier separates routine burning from anomalies. Export as CAP 1.2 and GeoJSON."] },
    { sel: "#replay", mode: "replay",
      pl: ["Powtórka", "Wybierz zakończony pożar. Najpierw zobaczysz prognozę z danych dostępnych w chwili startu, dopiero po „Reveal what happened” rzeczywistość: trafienia, chybienia i fałszywe alarmy (ściśle lub z tolerancją 5 km). To nasz test bez wycieku danych."],
      en: ["Replay", "Pick a fire that has ended. You first see the forecast made from the data available at the start; only after “Reveal what happened” do you see reality: hits, misses and false alarms (strict or within 5 km). This is our leak-free test."] },
    { sel: null, mode: "live",
      pl: ["Jak to czytać", "To narzędzie eksperymentalne, a prawdopodobieństwa nie są skalibrowane. W testach na nieznanych pożarach 3-dniowa prognoza trafia w granicach 5 km w około 70–75% przypadków (dzień 1: około 57–59%, cel 70% nie został osiągnięty). Brak detekcji nie oznacza braku pożaru, bo satelity nie widzą przez chmury i dym. Nie używaj do decyzji o ewakuacji."],
      en: ["How to read it", "This tool is experimental and probabilities are not calibrated. On unseen fires the 3-day forecast lands within 5 km about 70–75 % of the time (day 1: about 57–59 %; the 70 % target was not met). No detection does not mean no fire: satellites cannot see through cloud and smoke. Do not use it for evacuation decisions."] },
  ];

  var UI = {
    pl: { btn: "Zapoznaj się", btnTitle: "Przewodnik krok po kroku", prev: "Wstecz", next: "Dalej", done: "Zakończ", close: "Zamknij przewodnik", step: "Krok" },
    en: { btn: "Guide", btnTitle: "Step-by-step guide", prev: "Back", next: "Next", done: "Finish", close: "Close guide", step: "Step" }
  };

  var root, spot, card, btn, idx = 0, startMode = "live", open = false;

  function t() { return UI[lang()]; }

  function build() {
    root = document.createElement("div");
    root.className = "tour-root"; root.hidden = true; root.setAttribute("data-i18n-skip", "");
    root.innerHTML =
      '<div class="tour-dim"></div><div class="tour-spot" aria-hidden="true"></div>' +
      '<div class="tour-card" role="dialog" aria-modal="true" aria-labelledby="tour-title" tabindex="-1">' +
      '<button type="button" class="tour-x" data-act="close">×</button>' +
      '<div class="tour-count"></div><h3 id="tour-title"></h3><p class="tour-text"></p>' +
      '<div class="tour-bar"><button type="button" class="tour-btn" data-act="prev"></button>' +
      '<button type="button" class="tour-btn tour-primary" data-act="next"></button></div></div>';
    document.body.appendChild(root);
    spot = root.querySelector(".tour-spot"); card = root.querySelector(".tour-card");
    root.addEventListener("click", function (e) {
      var a = e.target && e.target.getAttribute && e.target.getAttribute("data-act");
      if (a === "next") go(idx + 1); else if (a === "prev") go(idx - 1); else if (a === "close") stop();
      else if (e.target.classList.contains("tour-dim")) stop();
    });
  }

  function addButton() {
    btn = document.createElement("button");
    btn.type = "button"; btn.className = "tour-launch"; btn.setAttribute("data-i18n-skip", "");
    btn.addEventListener("click", start);
    var theme = document.getElementById("theme-toggle");
    if (theme && theme.parentNode) theme.parentNode.insertBefore(btn, theme);
    else { btn.classList.add("tour-launch-float"); document.body.appendChild(btn); }
    labelButton();
  }

  function labelButton() {
    if (!btn) return;
    var u = t();
    btn.innerHTML = '<span class="tour-q" aria-hidden="true">?</span><span class="tour-lbl">' + u.btn + "</span>";
    btn.setAttribute("aria-label", u.btnTitle); btn.title = u.btnTitle;
  }

  function target(step) {
    if (!step.sel) return null;
    var el = document.querySelector(step.sel);
    if (!el) return null;
    var r = el.getBoundingClientRect();
    return (r.width > 0 && r.height > 0) ? el : null;
  }

  function place(el) {
    var pad = 6, vw = window.innerWidth, vh = window.innerHeight;
    if (el) {
      el.scrollIntoView({ block: "center", inline: "nearest", behavior: "auto" });
      var r = el.getBoundingClientRect();
      var top = Math.max(r.top - pad, 4), left = Math.max(r.left - pad, 4);
      var w = Math.min(r.width + pad * 2, vw - 8), h = Math.min(r.height + pad * 2, vh - 8);
      spot.style.cssText = "display:block;top:" + top + "px;left:" + left + "px;width:" + w + "px;height:" + h + "px";
      root.classList.remove("tour-center");
      card.style.cssText = "";
      var cw = card.offsetWidth, ch = card.offsetHeight, x, y;
      if (vw < 640) { x = (vw - cw) / 2; y = (top + h + ch + 12 < vh) ? top + h + 10 : Math.max(8, top - ch - 10); }
      else if (left + w + cw + 16 < vw) { x = left + w + 12; y = Math.min(Math.max(top, 8), vh - ch - 8); }
      else if (left - cw - 12 > 0) { x = left - cw - 12; y = Math.min(Math.max(top, 8), vh - ch - 8); }
      else { x = Math.min(Math.max(left, 8), vw - cw - 8); y = (top + h + ch + 12 < vh) ? top + h + 10 : Math.max(8, top - ch - 10); }
      card.style.left = Math.max(8, x) + "px"; card.style.top = Math.max(8, y) + "px";
    } else {
      spot.style.display = "none"; root.classList.add("tour-center"); card.style.cssText = "";
    }
  }

  function render() {
    var s = STEPS[idx], u = t(), txt = s[lang()];
    if (s.mode && EF().switchMode && EF().state && EF().state.mode !== s.mode) {
      try { EF().switchMode(s.mode); } catch (e) { /* keep going */ }
    }
    if (s.open) { try { document.querySelectorAll(s.open).forEach(function (d) { if (d.tagName === "DETAILS") d.open = true; }); } catch (e) { /* keep going */ } }
    card.querySelector(".tour-count").textContent = u.step + " " + (idx + 1) + " / " + STEPS.length;
    card.querySelector("#tour-title").textContent = txt[0];
    card.querySelector(".tour-text").textContent = txt[1];
    var prev = card.querySelector('[data-act="prev"]'), next = card.querySelector('[data-act="next"]');
    prev.textContent = u.prev; prev.disabled = idx === 0;
    next.textContent = idx === STEPS.length - 1 ? u.done : u.next;
    card.querySelector(".tour-x").setAttribute("aria-label", u.close);
    var tries = 0, my = idx;
    (function show() {
      if (!open || my !== idx) return;
      var el = target(s);
      if (!el && s.sel && s.wait && tries++ < 20) { setTimeout(show, 300); return; }     // lazily rendered targets (charts that load data)
      place(el); next.focus({ preventScroll: true });
      if (s.wait) setTimeout(function () { if (open && my === idx) place(target(s)); }, 900);     // content may still grow after first paint
    })();
  }

  function go(i) {
    if (i >= STEPS.length) { stop(); return; }
    idx = Math.max(0, i); render();
  }

  function onKey(e) {
    if (!open) return;
    if (e.key === "Escape") { e.preventDefault(); stop(); }
    else if (e.key === "ArrowRight") { e.preventDefault(); go(idx + 1); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); go(idx - 1); }
    else if (e.key === "Tab") {
      var f = card.querySelectorAll("button:not([disabled])");
      if (!f.length) return;
      var first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  }

  function start() {
    if (!root) build();
    startMode = (EF().state && EF().state.mode) || "live";
    idx = 0; open = true; root.hidden = false;
    document.body.classList.add("tour-open");
    render();
  }

  function stop() {
    if (!open) return;
    open = false; root.hidden = true; document.body.classList.remove("tour-open");
    try { if (EF().switchMode && EF().state && EF().state.mode !== startMode) EF().switchMode(startMode); } catch (e) { /* ignore */ }
    if (btn) btn.focus({ preventScroll: true });
  }

  function init() {
    addButton();
    document.addEventListener("keydown", onKey, true);
    document.addEventListener("equifire:langchange", function () { labelButton(); if (open) render(); });
    window.addEventListener("resize", function () { if (open) place(target(STEPS[idx])); });
    new MutationObserver(function () { if (btn && !btn.isConnected) { addButton(); } })
      .observe(document.body, { childList: true, subtree: false });
    window.EquiTour = { start: start, stop: stop };
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { setTimeout(init, 0); });
  else setTimeout(init, 0);
})();
