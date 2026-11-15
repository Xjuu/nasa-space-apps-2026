/* EquiFire: "? Zapoznaj się" step-by-step guided tour (Polish by default, English with the PL | EN switch). */
(function () {
  "use strict";
  var EF = function () { return window.EquiFire || {}; };

  function lang() {
    var l = (document.documentElement.getAttribute("lang") || "pl").toLowerCase();
    return l.indexOf("en") === 0 ? "en" : "pl";
  }

  // mode: workspace to open before showing the step; sel: element to highlight (optional).
  var STEPS = [
    { sel: null, mode: "live",
      pl: ["Witaj w FireSpot", "To narzędzie pokazuje pożary wykryte przez satelity MODIS i VIIRS, prognozuje ich rozprzestrzenianie i porównuje bieżącą aktywność z wieloletnią normą. Przejdziemy krok po kroku przez wszystkie ekrany (około 2 minut). Strzałki ← → przechodzą dalej, Esc zamyka."],
      en: ["Welcome to FireSpot", "This tool shows fires detected by the MODIS and VIIRS satellites, forecasts how they may spread, and compares current activity with the multi-year norm. We will go through every screen step by step (about 2 minutes). Arrow keys ← → move on, Esc closes."] },
    { sel: ".mode-tabs", mode: "live",
      pl: ["Cztery obszary robocze i powtórka", "Monitor – bieżące detekcje. Prognoza – gdzie pożar może się rozprzestrzenić. Historia – wieloletnie porównania. Alerty – obszary ponad normą. Powtórka – sprawdzenie prognozy na zakończonych pożarach."],
      en: ["Four workspaces and a replay", "Monitor – current detections. Forecast – where a fire may spread. History – multi-year comparisons. Alerts – regions above their norm. Replay – check a forecast against fires that have ended."] },
    { sel: "#area-section", mode: "live",
      pl: ["Monitor: wybierz obszar", "Narysuj prostokąt na mapie, użyj środka mapy albo wpisz współrzędne i rozmiar. Obszar może mieć do 25 000 km²."],
      en: ["Monitor: choose an area", "Draw a rectangle on the map, use the map centre, or type coordinates and a size. The area can be up to 25,000 km²."] },
    { sel: "#btn-show-fires", mode: "live",
      pl: ["Wczytaj detekcje", "Przycisk pobiera pożary z ostatnich 24 godzin. Kolor punktu oznacza wiek obserwacji, a rozmiar moc promieniowania (FRP). Kliknij punkt, żeby zobaczyć satelitę, czas i pewność wykrycia."],
      en: ["Load detections", "The button loads fires from the last 24 hours. Point colour shows the age of the observation and size shows radiative power (FRP). Click a point for the satellite, time and confidence."] },
    { sel: "#layers-btn", mode: "live",
      pl: ["Warstwy, mapa i globus", "Przełącz mapę płaską i globus 3D. W menu Warstwy włączysz orbity satelitów (Terra, Aqua, Suomi NPP, NOAA-20, NOAA-21), radar opadów i strzałki wiatru. Położenie jest szacowane, gdy źródłem jest środek piksela satelity – oznaczamy to etykietą „Szacowane położenie”."],
      en: ["Layers, map and globe", "Switch between the flat map and the 3D globe. The Layers menu turns on satellite orbits (Terra, Aqua, Suomi NPP, NOAA-20, NOAA-21), rain radar and wind arrows. A location is an estimate when it comes from a satellite pixel centre – this is marked “Estimated location”."] },
    { sel: "#date-controls", mode: "date",
      pl: ["Prognoza: wybierz pożar", "Kliknij pożar na mapie, ustaw horyzont (godziny), start i model paliwa. Gdy VIIRS nie widzi ognia, używamy MODIS jako zastępczego źródła zapłonu i oznaczamy to w wyniku."],
      en: ["Forecast: pick a fire", "Click a fire on the map and set the horizon (hours), start time and fuel model. When VIIRS sees no fire, MODIS is used as the ignition source and this is stated in the result."] },
    { sel: "#btn-forecast", mode: "date",
      pl: ["Uruchom i odtwarzaj", "Prognoza łączy model ML (dzienne komórki ~5,5 km) z symulacją fizyczną (obwód godzina po godzinie z prawdziwym wiatrem, wilgotnością i paliwem). Odtwarzacz: Start/Pauza, krok wstecz i w przód, suwak czasu i prędkość. Włącz „Pokaż obserwowane pożary”, żeby porównać z rzeczywistością."],
      en: ["Run and play", "The forecast combines an ML model (daily ~5.5 km cells) with a physics simulation (hour-by-hour perimeter with real wind, humidity and fuel). Player: play/pause, step back and forward, time slider and speed. Turn on “Show observed fires” to compare with reality."] },
    { sel: "#fx-root", mode: "date",
      pl: ["Dodatki prognozy", "Do następnego przelotu: kiedy satelita znów zobaczy pożar i czy chmury go zasłonią. Martwe strefy: odróżnia „brak pożaru” od „zbyt słaba obserwacja”. Scenariusze pogodowe: silniejszy wiatr, deszcz (hipotetyczne, nie prognoza). Ekspozycja: szacowane obiekty na drodze ognia. Karta zdarzenia do wydruku."],
      en: ["Forecast extras", "Until the next pass: when a satellite will see the fire again and whether cloud may hide it. Blind spots: separates “no fire detected” from “insufficient observation”. Weather scenarios: stronger wind, rain (hypothetical, not a forecast). Exposure: estimated assets in the fire's path. A printable incident brief."] },
    { sel: "#history", mode: "history",
      pl: ["Historia", "Wybierz obszar i miarę. Kalendarz pożarów pokazuje tygodnie × lata, a wykres tygodniowy porównuje ten rok ze średnią innych lat. Linie 2012 i 2018 to zmiany satelitów, nie zmiany pożarów. Sekcja „25-letni zharmonizowany zapis” pokazuje rekord 2000–2025 bez sztucznego skoku po 2012."],
      en: ["History", "Pick an area and a measure. The burning calendar shows weeks × years and the weekly chart compares this year with the average of other years. The 2012 and 2018 lines are satellite changes, not fire changes. The “25-year harmonized record” section shows 2000–2025 without the artificial jump after 2012."] },
    { sel: "#alerts", mode: "alerts",
      pl: ["Alerty", "Aktywność z ostatnich 7 dni porównana z tym samym tygodniem w latach 2008–2025. Poziomy: obserwuj (z ≥ 2), ostrzeżenie (z ≥ 3), krytyczny (z ≥ 5), z flagą „poza sezonem”. Eksport CAP 1.2 i GeoJSON."],
      en: ["Alerts", "The last 7 days compared with the same week in 2008–2025. Levels: watch (z ≥ 2), warning (z ≥ 3), critical (z ≥ 5), with an “out of season” flag. Export as CAP 1.2 and GeoJSON."] },
    { sel: "#replay", mode: "replay",
      pl: ["Powtórka", "Wybierz zakończony pożar. Najpierw zobaczysz prognozę z danych dostępnych w chwili startu, dopiero po „Pokaż, co się stało” – rzeczywistość: trafienia, chybienia i fałszywe alarmy (ściśle lub z tolerancją 5 km)."],
      en: ["Replay", "Pick a fire that has ended. You first see the forecast made from the data available at the start; only after “Reveal what happened” do you see reality: hits, misses and false alarms (strict or within 5 km)."] },
    { sel: "#theme-toggle", mode: "live",
      pl: ["Motyw i język", "Przełącznik motywu (ciemny/jasny) i język PL | EN znajdują się w nagłówku. Wybór jest zapamiętywany."],
      en: ["Theme and language", "The theme switch (dark/light) and the PL | EN language switch are in the header. Your choice is remembered."] },
    { sel: null, mode: "live",
      pl: ["Jak to czytać", "To narzędzie eksperymentalne, a prawdopodobieństwa nie są skalibrowane. W testach na nieznanych pożarach 3-dniowa prognoza trafia w granicach 5 km w około 70–75% przypadków (dzień 1: około 57–59%). Brak detekcji nie oznacza braku pożaru – satelity nie widzą przez chmury i dym."],
      en: ["How to read it", "This tool is experimental and probabilities are not calibrated. On unseen fires the 3-day forecast lands within 5 km about 70–75% of the time (day 1: about 57–59%). No detection does not mean no fire – satellites cannot see through cloud and smoke."] }
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
    card.querySelector(".tour-count").textContent = u.step + " " + (idx + 1) + " / " + STEPS.length;
    card.querySelector("#tour-title").textContent = txt[0];
    card.querySelector(".tour-text").textContent = txt[1];
    var prev = card.querySelector('[data-act="prev"]'), next = card.querySelector('[data-act="next"]');
    prev.textContent = u.prev; prev.disabled = idx === 0;
    next.textContent = idx === STEPS.length - 1 ? u.done : u.next;
    card.querySelector(".tour-x").setAttribute("aria-label", u.close);
    setTimeout(function () { if (open) { place(target(s)); next.focus({ preventScroll: true }); } }, 220);
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
