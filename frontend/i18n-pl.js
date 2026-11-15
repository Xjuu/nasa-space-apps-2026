/* FireSpot Polish dictionary for the runtime translation layer (i18n.js).
   exact:    "English source string" -> "Polish"   (whitespace-insensitive)
   patterns: [ "English with {placeholders}", "Polish with {placeholders}" ]; use {x|t} in the Polish side to
             translate the captured value too. Proper names, units (km², MW, UTC, FRP, MODIS, VIIRS) are kept.
   Anything missing here simply stays English. */
(function () {
  "use strict";
  var E = {}, P = [];
  function e(o) { for (var k in o) E[k] = o[k]; }
  function p(en, pl) { P.push([en, pl]); }
  function pv(en, pl) { p(en.replace("{s}", "s"), pl); p(en.replace("{s}", ""), pl); }

  /* ================================================================ header, shell, map chrome (index.html) */
  e({
    "FireSpot — Wildfire monitoring": "FireSpot — monitoring pożarów",
    "Monitor MODIS and VIIRS fire detections, predict fire spread and compare historical activity.":
      "Monitoruj detekcje pożarów z MODIS i VIIRS, prognozuj rozprzestrzenianie się ognia i porównuj aktywność historyczną.",
    "Skip to analysis": "Przejdź do analizy",
    "FireSpot monitoring home": "FireSpot: strona główna monitoringu",
    "Wildfire monitoring": "Monitoring pożarów",
    "Workspace": "Obszar roboczy",
    "Monitor": "Monitoring",
    "Forecast": "Prognoza",
    "History": "Historia",
    "Alerts": "Alerty",
    "Replay": "Odtworzenie",
    "Switch theme": "Zmień motyw",
    "Switch to light theme": "Przełącz na jasny motyw",
    "Switch to dark theme": "Przełącz na ciemny motyw",
    "Map view": "Widok mapy",
    "Map": "Mapa",
    "Globe view": "Widok globusa",
    "Globe": "Globus",
    "Map layers": "Warstwy mapy",
    "Layers": "Warstwy",
    "Basemap": "Mapa podkładowa",
    "Streets (OpenStreetMap)": "Ulice (OpenStreetMap)",
    "Satellite imagery (Esri)": "Zdjęcia satelitarne (Esri)",
    "Topographic (Esri)": "Mapa topograficzna (Esri)",
    "Overlays": "Nakładki",
    "Fire detections": "Detekcje pożarów",
    "Precipitation radar": "Radar opadów",
    "Wind (10 m, now)": "Wiatr (10 m, teraz)",
    "Fire satellites (MODIS / VIIRS)": "Satelity pożarowe (MODIS / VIIRS)",
    "Orbit tracks": "Ścieżki orbit",
    "Radar: RainViewer. Wind: Open-Meteo. Both keyless; weather is drawn on the map view.":
      "Radar: RainViewer. Wiatr: Open-Meteo. Oba bez klucza API; pogoda jest rysowana w widoku mapy.",
    "Maximum inspection area: 25,000 km²": "Maksymalny obszar analizy: 25 000 km²",
    "Area: — / 25,000 km²": "Obszar: — / 25 000 km²",
    "Observation map": "Mapa obserwacji",
    "Forecast map": "Mapa prognozy",
    "Historical area": "Obszar historyczny",
    "Regional activity": "Aktywność regionalna",
    "Replay map": "Mapa odtworzenia",
    "Last 24 hours · MODIS & VIIRS": "Ostatnie 24 godziny · MODIS i VIIRS",
    "3D globe with fire detections and satellite positions": "Globus 3D z detekcjami pożarów i pozycjami satelitów",
    "Fire observation map": "Mapa obserwacji pożarów",
    "Fire satellites": "Satelity pożarowe",
    "Loading positions…": "Wczytywanie pozycji…",
    "Positions from orbital elements, updated every second. Lines show about one orbit (past 50 min, next 70 min).":
      "Pozycje wyliczone z elementów orbitalnych, odświeżane co sekundę. Linie pokazują mniej więcej jedną orbitę (ostatnie 50 min, następne 70 min).",
    "Map legend": "Legenda mapy",
    "Satellite fire detections": "Satelitarne detekcje pożarów",
    "≤ 3 h": "≤ 3 godz.",
    "3–12 h": "3–12 godz.",
    "> 12 h": "> 12 godz.",
    "Burning at start": "Płonie na starcie",
    "Spread timing · early → late": "Czas rozprzestrzeniania · wcześnie → późno",
    "Daily ML + physics risk": "Dzienne ryzyko ML + fizyka",
    "Observed later": "Zaobserwowane później",
    "Area": "Obszar",
    "Wind (blows toward)": "Wiatr (wieje w kierunku)",
    "Radar": "Radar",
    "Quick locations": "Szybkie lokalizacje",
    "Amazon": "Amazonia",
    "Pantanal": "Pantanal",
    "California": "Kalifornia",
    "Australia": "Australia",
    "Spread prediction playback": "Odtwarzanie prognozy rozprzestrzeniania",
    "ML + physics": "ML + fizyka",
    "Skip to start": "Przejdź na początek",
    "Step back": "Krok wstecz",
    "Play": "Odtwórz",
    "Pause": "Pauza",
    "Step forward": "Krok naprzód",
    "Skip to end": "Przejdź na koniec",
    "Prediction time": "Czas prognozy",
    "Playback speed": "Szybkość odtwarzania",
    "Select a fire and run a prediction to use the timeline.": "Wybierz pożar i uruchom prognozę, aby użyć osi czasu.",
    "Colours show spread timing, not confidence. Perimeter: hourly physics; risk cells: daily ML + physics.":
      "Kolory pokazują czas rozprzestrzeniania, nie pewność prognozy. Obrys: fizyka godzinowa; komórki ryzyka: dzienne ML + fizyka.",
    "Area analysis": "Analiza obszaru",
    "Area overview": "Przegląd obszaru",
    "Connecting…": "Łączenie…",
    "Latest satellite observations": "Najnowsze obserwacje satelitarne",
  });

  /* ---- Forecast tab (index.html) */
  e({
    "Spread prediction": "Prognoza rozprzestrzeniania",
    "Choose a fire on the map, set the forecast window, then run ML + physics. Inspect each step in the timeline.":
      "Wybierz pożar na mapie, ustaw okno prognozy, a następnie uruchom ML + fizyka. Każdy krok sprawdzisz na osi czasu.",
    "Ignition": "Źródło zapłonu",
    "Nearest live fire": "Najbliższy aktywny pożar",
    "Archive fire on date": "Pożar z archiwum w wybranym dniu",
    "Horizon": "Horyzont",
    "3 hours": "3 godziny", "6 hours": "6 godzin", "12 hours": "12 godzin", "24 hours": "24 godziny",
    "48 hours": "48 godzin", "72 hours": "72 godziny",
    "Time step": "Krok czasowy",
    "Auto": "Automatycznie",
    "10 min": "10 min", "15 min": "15 min", "30 min": "30 min", "60 min": "60 min",
    "Start (UTC)": "Start (UTC)",
    "Fuel": "Paliwo",
    "Auto (fuel map, fallback if unavailable)": "Automatycznie (mapa paliw, zapasowo wartość domyślna)",
    "Grass": "Trawa", "Savanna": "Sawanna", "Shrub": "Krzewy", "Forest": "Las",
    "Experimental forecast. Confidence is not calibrated for individual fires.":
      "Prognoza eksperymentalna. Pewność nie jest skalibrowana dla pojedynczych pożarów.",
    "Model limits and validation": "Ograniczenia modelu i walidacja",
    "Experimental simulation. An earlier physics version over-predicted grass, savanna and shrub fires and under-predicted forest fires in a 10-fire test. That test does not validate the new landscape model. Daily ML forecasts have roughly 70–75% day-3 overlap with one-cell tolerance, but only about 25–31% exact-cell overlap.":
      "Symulacja eksperymentalna. Wcześniejsza wersja fizyki zawyżała wyniki dla pożarów traw, sawann i krzewów, a zaniżała dla pożarów lasów w teście na 10 pożarach. Ten test nie waliduje nowego modelu krajobrazu. Dzienne prognozy ML mają około 70–75% pokrycia w 3. dniu przy tolerancji jednej komórki, ale tylko około 25–31% pokrycia dokładnie w tej samej komórce.",
    "Observation date": "Data obserwacji",
    "Show observed fires": "Pokaż zaobserwowane pożary",
    "Show observed later (historical replay)": "Pokaż zaobserwowane później (odtworzenie historyczne)",
    "Selected": "Wybrano",
    "Run prediction": "Uruchom prognozę",
    "Predictions are estimates, not confirmed perimeters. Verify on the ground.":
      "Prognozy są szacunkami, nie potwierdzonymi obrysami pożaru. Zweryfikuj w terenie.",
  });

  /* ---- Monitor tab (index.html) */
  e({
    "Define your area": "Zdefiniuj obszar",
    "Max 25,000 km²": "Maks. 25 000 km²",
    "Draw on the map, use the map centre, or enter coordinates.": "Narysuj na mapie, użyj środka mapy albo wpisz współrzędne.",
    "Draw area": "Narysuj obszar",
    "Use map centre": "Użyj środka mapy",
    "Center an area on the current map view": "Wyśrodkuj obszar na bieżącym widoku mapy",
    "Latitude": "Szerokość geograficzna",
    "Longitude": "Długość geograficzna",
    "Set area": "Ustaw obszar",
    "Set monitoring area at these coordinates": "Ustaw obszar monitoringu w tych współrzędnych",
    "Area coordinates": "Współrzędne obszaru",
    "Bounds": "Granice",
    "Dimensions": "Wymiary",
    "Load detections": "Wczytaj detekcje",
    "Fire detections ": "Detekcje pożarów",
    "0 locations": "0 lokalizacji",
    "Show global detections": "Pokaż detekcje globalne",
    "Loading global satellite detections…": "Wczytywanie globalnych detekcji satelitarnych…",
    "Selected area": "Wybrany obszar",
    "Detection locations": "Lokalizacje detekcji",
    "Peak fire power": "Maksymalna moc pożaru",
    "Last observed": "Ostatnia obserwacja",
    "Estimated location": "Szacowana lokalizacja",
    "Each detection is the centre of the satellite pixel that saw heat: about 375 m across for VIIRS and 1 km for MODIS at nadir, larger toward the swath edge. The fire can be anywhere inside that pixel.":
      "Każda detekcja to środek piksela satelity, który zarejestrował ciepło: około 375 m dla VIIRS i 1 km dla MODIS w nadirze, a większy ku krawędzi pasa skanowania. Ogień może być w dowolnym miejscu wewnątrz piksela.",
    "Points are pixel centres, not exact fire positions.": "Punkty to środki pikseli, a nie dokładne pozycje pożarów.",
    "Select an area, then load satellite detections.": "Wybierz obszar, a następnie wczytaj detekcje satelitarne.",
    "Selected location": "Wybrana lokalizacja",
    "ML + physics simulation": "Symulacja ML + fizyka",
    "Simulation (JSON)": "Symulacja (JSON)",
    "Go to timeline": "Przejdź do osi czasu",
    "ML spread forecast": "Prognoza rozprzestrzeniania ML",
    "Combined daily guidance": "Łączne wskazówki dzienne",
    "Colour": "Kolor",
    "Day": "Dzień",
    "Date": "Data",
    "Observed": "Zaobserwowano",
    "Overlap": "Pokrycie",
    "Overlap between forecast and observed cells": "Pokrycie między komórkami prognozowanymi a zaobserwowanymi",
    "CAP alert (XML)": "Alert CAP (XML)",
    "Forecast GeoJSON": "Prognoza GeoJSON",
    "Activity against historical baseline": "Aktywność na tle historycznej normy",
    "Next satellite passes": "Najbliższe przeloty satelitów",
  });

  /* ---- History tab (index.html) */
  e({
    "Area of interest": "Obszar zainteresowania",
    "Widen the panel for bigger charts": "Poszerz panel, aby powiększyć wykresy",
    "Wide": "Szeroki",
    "Draw an area on the map, click a spot, or enter coordinates (up to 40° × 40°).":
      "Narysuj obszar na mapie, kliknij punkt albo wpisz współrzędne (do 40° × 40°).",
    "Draw area of interest": "Narysuj obszar zainteresowania",
    "Rectangle": "Prostokąt",
    "Polygon": "Wielokąt",
    "Use the visible map area": "Użyj widocznego obszaru mapy",
    "Click on the map to add points.": "Kliknij na mapie, aby dodać punkty.",
    "Finish": "Zakończ",
    "Cancel": "Anuluj",
    "Size": "Rozmiar",
    "Go": "Idź",
    "or bbox: min lon, min lat, max lon, max lat": "lub bbox: min dł., min szer., maks. dł., maks. szer.",
    "No area selected.": "Nie wybrano obszaru.",
    "Compare year": "Rok do porównania",
    "Measure": "Miara",
    "VIIRS burning cells (2012+)": "Płonące komórki VIIRS (od 2012)",
    "MODIS burning cells (2008+)": "Płonące komórki MODIS (od 2008)",
    "Any sensor, burning cells": "Dowolny sensor, płonące komórki",
    "VIIRS detections": "Detekcje VIIRS",
    "MODIS detections": "Detekcje MODIS",
    "Harmonized FRP (MW)": "Zharmonizowane FRP (MW)",
    "fires detected": "wykrytych pożarów",
    "vs multi-year average": "względem średniej wieloletniej",
    "z-score": "wynik z (z-score)",
    "status": "status",
    "Burning calendar": "Kalendarz pożarów",
    "Calendar colour": "Kolor kalendarza",
    "Counts": "Liczby",
    "Anomaly": "Anomalia",
    "Hover or tap a cell for details; tap a row to compare that year.":
      "Najedź na komórkę lub dotknij jej, aby zobaczyć szczegóły; dotknij wiersza, aby porównać ten rok.",
    "This year vs average, by week": "Ten rok na tle średniej, tygodniami",
    "selected year": "wybrany rok",
    "mean of other years": "średnia z pozostałych lat",
    "± 1 std": "± 1 odch. std.",
    "anomaly (z ≥ 2)": "anomalia (z ≥ 2)",
    "unusually low": "nietypowo niski",
    "Fires per year by sensor": "Pożary w roku według sensora",
    "MODIS (Terra, Aqua)": "MODIS (Terra, Aqua)",
    "VIIRS (S-NPP, NOAA-20/21)": "VIIRS (S-NPP, NOAA-20/21)",
    "* partial year": "* rok niepełny",
    "Data sources and methodology": "Źródła danych i metodyka",
    "Burning cells = 0.05° (~5 km) grid cells with at least one detection that day. Baseline: the same ISO week in every other year with full sensor coverage; z = (x − mean) / max(std, √mean). VIIRS (375 m) sees 2–3× more small fires than MODIS (1 km), so the sensors are shown separately; MODIS gives the longest consistent record, and FRP is harmonized (MODIS converted to VIIRS-equivalent).":
      "Płonące komórki = komórki siatki 0,05° (~5 km) z co najmniej jedną detekcją w danym dniu. Norma: ten sam tydzień ISO we wszystkich pozostałych latach z pełnym pokryciem sensora; z = (x − średnia) / max(odch. std., √średnia). VIIRS (375 m) widzi 2–3 razy więcej małych pożarów niż MODIS (1 km), dlatego sensory pokazano osobno; MODIS daje najdłuższy spójny rekord, a FRP jest zharmonizowane (MODIS przeliczony na ekwiwalent VIIRS).",
    "Data (JSON)": "Dane (JSON)",
    "Calendar (CSV)": "Kalendarz (CSV)",
  });

  /* ---- Alerts tab (index.html) */
  e({
    "Live alerts": "Alerty na żywo",
    "Burning 5 km cells in the last 7 days (NASA FIRMS NRT, VIIRS NOAA-20) compared with the same calendar week in 2008-2025 for the same grid region. Only areas far above their own norm are listed.":
      "Płonące komórki 5 km w ostatnich 7 dniach (NASA FIRMS NRT, VIIRS NOAA-20) w porównaniu z tym samym tygodniem kalendarzowym w latach 2008-2025 dla tego samego regionu siatki. Na liście są tylko obszary znacznie powyżej własnej normy.",
    "Region": "Region",
    "Show": "Pokaż",
    "watch and above": "obserwacja i wyżej",
    "warning and above": "ostrzeżenie i wyżej",
    "critical only": "tylko krytyczne",
    "Refresh alerts": "Odśwież alerty",
    "Alert levels": "Poziomy alertów",
    "watch z≥2": "obserwacja z≥2",
    "warning z≥3": "ostrzeżenie z≥3",
    "critical z≥5": "krytyczny z≥5",
    "no baseline": "brak normy",
    "Loading…": "Wczytywanie…",
    "CAP alerts (XML)": "Alerty CAP (XML)",
    "GeoJSON": "GeoJSON",
    "How activity alerts are calculated": "Jak liczone są alerty aktywności",
    "z = (cells − mean) / max(std, 0.3·mean, √mean+1). Baseline: 5 fully archived countries (Brazil, Australia, India, USA, Canada); 2008-2011 reconstructed from MODIS. Elsewhere: no baseline.":
      "z = (komórki − średnia) / max(odch. std., 0,3·średnia, √średnia+1). Norma: 5 w pełni zarchiwizowanych krajów (Brazylia, Australia, Indie, USA, Kanada); lata 2008-2011 zrekonstruowane z MODIS. W pozostałych miejscach: brak normy.",
    "Connection details": "Szczegóły połączenia",
    "Backend API": "API serwera",
    "NASA FIRMS · MODIS & VIIRS · RainViewer · Open-Meteo · OpenStreetMap contributors · Esri":
      "NASA FIRMS · MODIS i VIIRS · RainViewer · Open-Meteo · autorzy OpenStreetMap · Esri",
  });


  /* ================================================================ app.js: monitor, map, popups */
  e({
    "Estimated location": "Szacowana lokalizacja",
    "MODIS detection: the point is the centre of a ~1 km pixel (up to ~2 x 5 km near the swath edge). The fire can be anywhere inside that footprint.":
      "Detekcja MODIS: punkt to środek piksela ~1 km (do ~2 x 5 km przy krawędzi pasa skanowania). Ogień może być w dowolnym miejscu wewnątrz tego śladu.",
    "VIIRS detection: the point is the centre of a ~375 m pixel (up to ~0.8 km near the swath edge). The fire can be anywhere inside that footprint.":
      "Detekcja VIIRS: punkt to środek piksela ~375 m (do ~0,8 km przy krawędzi pasa skanowania). Ogień może być w dowolnym miejscu wewnątrz tego śladu.",
    "Cancel drawing": "Anuluj rysowanie",
    "Drag to draw an area, or tap to centre a box. Press Escape to cancel.":
      "Przeciągnij, aby narysować obszar, albo dotknij, aby wyśrodkować ramkę. Naciśnij Esc, aby anulować.",
    "Zoom in": "Przybliż", "Zoom out": "Oddal",
    "A JavaScript library for interactive maps": "Biblioteka JavaScript do map interaktywnych",
    "Fire spread prediction": "Prognoza rozprzestrzeniania pożaru",
    "Pick a fire, choose a horizon, play the prediction": "Wybierz pożar, ustaw horyzont i odtwórz prognozę",
    "Click a fire or a point to select it": "Kliknij pożar lub punkt, aby go wybrać",
    "Historical activity": "Aktywność historyczna",
    "Compare fire activity across years": "Porównaj aktywność pożarową w kolejnych latach",
    "Selected historical area": "Wybrany obszar historyczny",
    "Activity alerts": "Alerty aktywności",
    "Regions above their seasonal baseline": "Regiony powyżej sezonowej normy",
    "Recent activity against historical baseline": "Ostatnia aktywność na tle historycznej normy",
    "Historical replay": "Odtworzenie historyczne",
    "Issued forecast first, then what actually happened": "Najpierw wydana prognoza, potem to, co faktycznie się wydarzyło",
    "Replay of a past forecast": "Odtworzenie dawnej prognozy",
    "The observation service is unavailable. Please try loading the data again.":
      "Usługa obserwacji jest niedostępna. Spróbuj wczytać dane ponownie.",
    "Zoom out to explore.": "Oddal widok, aby przeglądać.",
    "Not an exhaustive global count.": "To nie jest pełna liczba globalna.",
    "Global detections unavailable.": "Detekcje globalne niedostępne.",
    "Area detections load separately.": "Detekcje obszaru wczytują się osobno.",
    "Use Load detections to retry.": "Użyj „Wczytaj detekcje”, aby spróbować ponownie.",
    "Loading satellite observations…": "Wczytywanie obserwacji satelitarnych…",
    "Zoom in: live view covers at most 40 x 40 degrees.": "Przybliż: widok na żywo obejmuje najwyżej 40 x 40 stopni.",
    "Fetching live fires (FIRMS near-real-time, last 24 h)...": "Pobieranie aktywnych pożarów (FIRMS, niemal w czasie rzeczywistym, ostatnie 24 godz.)...",
    "No satellite fire detections in this area during the last 24 hours.": "Brak satelitarnych detekcji pożarów w tym obszarze w ciągu ostatnich 24 godzin.",
    "No detections returned. You can draw another area or select a quick location.": "Nie zwrócono detekcji. Możesz narysować inny obszar albo wybrać szybką lokalizację.",
    "Just now": "Przed chwilą", "Today": "Dziś", "Last 24h": "Ostatnie 24 godz.", "last 24 hours": "ostatnie 24 godziny",
    "Last 24 h": "Ostatnie 24 godz.",
    "Before 20 Jan 2012 only MODIS (1 km) exists; cells are reconstructed to VIIRS-equivalent.": "Przed 20 sty 2012 istnieje tylko MODIS (1 km); komórki są zrekonstruowane do ekwiwalentu VIIRS.",
    "Archive cells are 0.05° (~5 km) grid squares; the marker is the cell centre, not the fire position.": "Komórki archiwalne to kwadraty siatki 0,05° (~5 km); znacznik to środek komórki, a nie pozycja pożaru.",
    "Before 20 Jan 2012 the record is MODIS-only, reconstructed to VIIRS-equivalent.": "Przed 20 sty 2012 zapis pochodzi wyłącznie z MODIS, zrekonstruowanego do ekwiwalentu VIIRS.",
    "Burning cell (archive)": "Płonąca komórka (archiwum)",
    "Satellite fire detection": "Satelitarna detekcja pożaru",
    "Live · last 24 h": "Na żywo · ostatnie 24 godz.",
    "Cell centre": "Środek komórki", "Pixel centre": "Środek piksela",
    "Fire power": "Moc pożaru", "Last seen": "Ostatnio widziany", "Temperature": "Temperatura",
    "Satellite": "Satelita", "Detection confidence": "Pewność detekcji", "Detections": "Detekcje",
    "Predict spread": "Prognozuj rozprzestrzenianie",
    "Burning cell": "Płonąca komórka", "Fire detection": "Detekcja pożaru",
    "(estimated location)": "(szacowana lokalizacja)",
    "Comparing historical activity…": "Porównywanie aktywności historycznej…",
    "Loading passes…": "Wczytywanie przelotów…",
    "No historical baseline for this point and date.": "Brak historycznej normy dla tego punktu i daty.",
    "Pass predictions unavailable.": "Prognozy przelotów niedostępne.",
    "No passes found for next 24h.": "Nie znaleziono przelotów w ciągu najbliższych 24 godz.",
    "Before 20 Jan 2012 only MODIS (1 km) data exist; counts are reconstructed to VIIRS-equivalent and are less certain.":
      "Przed 20 sty 2012 istnieją tylko dane MODIS (1 km); liczby są zrekonstruowane do ekwiwalentu VIIRS i mniej pewne.",
    "MODIS-era data, reconstructed.": "Dane z ery MODIS, zrekonstruowane.",
    "Reconstructed data": "Dane zrekonstruowane",
    "ML forecast": "Prognoza ML",
    "Day {#n} predicted cell (~5 km)": "Dzień {#n}: prognozowana komórka (~5 km)",
    "Experimental ML + physics blend. Each selected cell has an ML probability, a physics burned fraction and a combined model score. Local forecast accuracy is not known.":
      "Eksperymentalne połączenie ML + fizyka. Każda wybrana komórka ma prawdopodobieństwo ML, frakcję spalenia z fizyki i łączny wynik modelu. Lokalna trafność prognozy nie jest znana.",
    "ML + physics forecast": "Prognoza ML + fizyka",
    "Model note": "Uwaga o modelu",
    "Area selection cancelled.": "Anulowano wybór obszaru.",
    "VIIRS burning cell-days": "Komórko-dni z pożarem (VIIRS)",
    "MODIS burning cell-days": "Komórko-dni z pożarem (MODIS)",
    "burning cell-days (any sensor)": "komórko-dni z pożarem (dowolny sensor)",
    "MW harmonized FRP": "MW zharmonizowanego FRP",
    "The area is empty: max lon/lat must be greater than min lon/lat.": "Obszar jest pusty: maks. dł./szer. geogr. musi być większa niż min.",
    "Click (or tap) one corner, then the opposite corner.": "Kliknij (lub dotknij) jeden narożnik, a potem narożnik przeciwległy.",
    "Click to add points; click the first point, double-click or Finish to close.": "Klikaj, aby dodawać punkty; kliknij pierwszy punkt, kliknij dwukrotnie lub użyj „Zakończ”, aby zamknąć.",
    "A polygon needs at least 3 points.": "Wielokąt wymaga co najmniej 3 punktów.",
    "Latitude must be -90..90 and longitude -180..180.": "Szerokość geograficzna musi mieścić się w zakresie -90..90, a długość -180..180.",
    "Enter 4 numbers: min lon, min lat, max lon, max lat.": "Wpisz 4 liczby: min dł., min szer., maks. dł., maks. szer.",
    "Loading fire history for this area...": "Wczytywanie historii pożarów dla tego obszaru...",
    "Archive is still loading on the server; retrying in 10 s...": "Archiwum nadal wczytuje się na serwerze; ponowna próba za 10 s...",
    "No data for this measure.": "Brak danych dla tej miary.",
    "quieter": "spokojniej", "more fire (z)": "więcej pożarów (z)",
    "Ready": "Gotowe",
    "Out of season": "Poza sezonem",
    "No NRT detections.": "Brak detekcji NRT.",
    "No region is above its norm at this level.": "Żaden region nie przekracza swojej normy na tym poziomie.",
    "Loading NRT feed…": "Wczytywanie kanału NRT…",
    "Radar mosaic": "Mozaika radarowa",
    "Loading wind…": "Wczytywanie wiatru…",
    "Radar unavailable right now (RainViewer).": "Radar chwilowo niedostępny (RainViewer).",
    "Harmonized fire power per year (VIIRS-equivalent)": "Zharmonizowana moc pożarów w roku (ekwiwalent VIIRS)",
    "Burning cell-days per year, both sensors combined": "Komórko-dni z pożarem w roku, oba sensory łącznie",
    "Fire detections per year by sensor": "Detekcje pożarów w roku według sensora",
    "Burning cell-days per year by sensor": "Komórko-dni z pożarem w roku według sensora",
    "Harmonized FRP": "Zharmonizowane FRP",
    "Burning cell-days, any sensor": "Komórko-dni z pożarem, dowolny sensor",
    "Burning cell-days": "Komórko-dni z pożarem",
    "partial year": "rok niepełny",
    "MODIS (Terra/Aqua)": "MODIS (Terra/Aqua)",
    "Satellite ignition": "Zapłon wg satelity",
    "Mapped": "Zmapowane",
  });

  p("Area: {#a} km² / 25,000 km²", "Obszar: {#a} km² / 25 000 km²");
  p("Area {#a} km² exceeds the 25,000 km² maximum", "Obszar {#a} km² przekracza maksimum 25 000 km²");
  p("Selected area is {#a} km², which exceeds the maximum limit of 25,000 km². Please shrink the box.", "Wybrany obszar ma {#a} km², co przekracza limit 25 000 km². Zmniejsz ramkę.");
  p("Area is {#a} km². Maximum allowed is 25,000 km².", "Obszar ma {#a} km². Maksymalnie dozwolone: 25 000 km².");
  p("Drawing: {#a} km² (over 25,000 km²)", "Rysowanie: {#a} km² (powyżej 25 000 km²)");
  p("Drawing: {#a} km²", "Rysowanie: {#a} km²");
  p("{#a}, {#b} to {#c}, {#d}", "{#a}, {#b} do {#c}, {#d}");
  p("{#n} highest-power global detections (last 24 h), plus detections in your area.", "{#n} {n|pl:najsilniejsza detekcja globalna,najsilniejsze detekcje globalne,najsilniejszych detekcji globalnych} (ostatnie 24 godz.), plus detekcje w Twoim obszarze.");
  p("Global detections unavailable; previous observations remain visible.", "Detekcje globalne niedostępne; poprzednie obserwacje pozostają widoczne.");
  p("{#n} live detections (last 24 h) in this area. Click one to select it as the ignition.", "{#n} {n|pl:detekcja na żywo,detekcje na żywo,detekcji na żywo} (ostatnie 24 godz.) w tym obszarze. Kliknij jedną, aby wybrać ją jako zapłon.");
  p("{#n} live fire detections in the visible map, last 24 h (list shows the newest 150).", "{#n} {n|pl:detekcja aktywnego pożaru,detekcje aktywnych pożarów,detekcji aktywnych pożarów} na widocznej mapie, ostatnie 24 godz. (lista pokazuje 150 najnowszych).");
  p("{#n}h ago", "{#n} h temu");
  p("{#n} h ago", "{#n} h temu");
  p("{d} UTC ({#n} h ago)", "{d} UTC ({#n} h temu)");
  p("({#n} h ago)", "({#n} h temu)");
  p("{#n} detection locations", "{#n} {n|pl:lokalizacja detekcji,lokalizacje detekcji,lokalizacji detekcji}");
  p("{#n} locations", "{#n} {n|pl:lokalizacja,lokalizacje,lokalizacji}");
  p("{#n} burning 5 km cells observed on {d} in this area.", "Dnia {d} na tym obszarze zaobserwowano {#n} {n|pl:płonącą komórkę 5 km,płonące komórki 5 km,płonących komórek 5 km}.");
  p("Archive {d}", "Archiwum {d}");
  p("Seen {t} UTC", "Widziano {t} UTC");
  p("FRP {#a} MW", "FRP {#a} MW");
  p("Last seen", "Ostatnio widziany");
  p("routine {a} • anomaly {b}", "rutynowe {a} • anomalia {b}");
  p("this week: {#a} burning cell-days vs usual {#b}", "w tym tygodniu: {#a} komórko-dni z pożarem wobec zwykle {#b}");
  p("in season in {a} of other years", "w sezonie w {a} pozostałych lat");
  p("On {d}, 0.25° region", "Dnia {d}, region 0,25°");
  p("z-score: {#a}", "wynik z (z-score): {#a}");
  p("observed: {#a} vs baseline: {#b}", "zaobserwowano: {#a}, norma: {#b}");
  p("{#h}h {#m}m", "{#h} godz. {#m} min");
  p("{#m} min", "{#m} min");
  p("{t} (in {m})", "{t} (za {m|t})");
  p("{#n} predicted cells", "{#n} prognozowanych komórek");
  p("Day {#n}", "Dzień {#n}");
  pv("{#n} cells (~5 km) predicted to newly burn over {#d} day{s}, starting from {#b} burning cells on {date}.", "Prognozuje się, że przez {#d} {d|pl:dzień,dni,dni} nowo spłonie {#n} {n|pl:komórka,komórki,komórek} (~5 km), licząc od {#b} {b|pl:płonącej komórki,płonących komórek,płonących komórek} dnia {date}.");
  p("The fire centre was snapped to the nearest burning 0.05° cell (centre {a}, {b}), {#k} km from the point you selected. All cells are ~5 km squares, so positions are approximate.", "Środek pożaru przyciągnięto do najbliższej płonącej komórki 0,05° (środek {a}, {b}), {#k} km od wybranego punktu. Wszystkie komórki to kwadraty ~5 km, więc pozycje są przybliżone.");
  p("Fire centre snapped {#k} km to the nearest burning 5 km cell.", "Środek pożaru przyciągnięto o {#k} km do najbliższej płonącej komórki 5 km.");
  p("Before 20 Jan 2012 the starting fire comes from MODIS (1 km) detections reconstructed to VIIRS-equivalent cells.", "Przed 20 sty 2012 pożar początkowy pochodzi z detekcji MODIS (1 km) zrekonstruowanych do komórek ekwiwalentnych VIIRS.");
  p("Starting cells reconstructed from MODIS-era data.", "Komórki początkowe zrekonstruowane z danych z ery MODIS.");
  p("Selected area is {a} km²", "Wybrany obszar ma {a} km²");
  p("{a} per ISO week", "{a} na tydzień ISO");
  p("Polygon in {a}", "Wielokąt w {a}");
  p("Area too large ({#a}° × {#b}°): at most {#c}° × {#d}°.", "Obszar zbyt duży ({#a}° × {#b}°): maksymalnie {#c}° × {#d}°.");
  pv("{#n} point{s}. Click the first point, double-click or Finish to close.", "Punkty: {#n}. Kliknij pierwszy punkt, kliknij dwukrotnie lub użyj „Zakończ”, aby zamknąć.");
  p("{#n} point. Click the first point, double-click or Finish to close.", "Punkty: {#n}. Kliknij pierwszy punkt, kliknij dwukrotnie lub użyj „Zakończ”, aby zamknąć.");
  p("{#n} points. Click the first point, double-click or Finish to close.", "Punkty: {#n}. Kliknij pierwszy punkt, kliknij dwukrotnie lub użyj „Zakończ”, aby zamknąć.");
  p("{u} in {#y}{per}", "{u|t} w {#y}{per|t}");
  p("({#n} weeks)", "({#n} tyg.)");
  p("vs {#n}-year average ({#a})", "względem średniej z {#n} {n|pl:roku,lat,lat} ({#a})");
  pv("{l}; {#n} anomalous week{s}", "{l|t}; {#n} {n|pl:tydzień anomalny,tygodnie anomalne,tygodni anomalnych}");
  p("{#y}: {#t} {u} in total. Tap to compare this year.", "{#y}: łącznie {#t} {u|t}. Dotknij, aby porównać ten rok.");
  p("{#y}, week {#w} ({r}): {#v} {u}", "{#y}, tydzień {#w} ({r}): {#v} {u|t}");
  p("; other years average {#a}", "; średnia z pozostałych lat {#a}");
  p(", z {a}", ", z {a}");
  p(" (anomaly)", " (anomalia)");
  p("anomalous week {#y}", "tydzień anomalny {#y}");
  p("blank = no {s} data", "puste = brak danych {s}");
  p("blank = no data", "puste = brak danych");
  p("{#y} vs other years, by week", "{#y} na tle pozostałych lat, tygodniami");
  p("{#n}× normal for this week", "{#n}× normy dla tego tygodnia");
  p("{l} · z = {#z}", "{l|t} · z = {#z}");
  p("{#n} burning cells in 7 days vs {#a} ± {#b}", "{#n} płonących komórek w 7 dni wobec {#a} ± {#b}");
  p("({#n} yrs, {#r} reconstructed)", "({#n} lat, {#r} zrekonstruowanych)");
  p("Top FRP {#a} MW at {#b}, {#c}", "Najwyższe FRP {#a} MW w {#b}, {#c}");
  p("Latest {t}", "Najnowsze: {t}");
  p("Baseline includes {#n} years reconstructed from MODIS (2008-2011).", "Norma zawiera {#n} {n|pl:rok,lata,lat} zrekonstruowanych z MODIS (2008-2011).");
  p("Strongest detection in this region; the coordinates are the centre of the VIIRS pixel (~375 m) that saw it, not an exact fire position.", "Najsilniejsza detekcja w tym regionie; współrzędne to środek piksela VIIRS (~375 m), który ją zarejestrował, a nie dokładna pozycja pożaru.");
  p("{#n} alert{s}", "{#n} {n|pl:alert,alerty,alertów}");
  p("{#n} alert", "{#n} {n|pl:alert,alerty,alertów}");
  p("{#n} alerts", "{#n} {n|pl:alert,alerty,alertów}");
  p("{f} {t} (stale)", "{f} {t} (nieaktualne)");
  p("Window {a} to {b} · week {#w} · feeds: {f}", "Okno {a} do {b} · tydzień {#w} · kanały: {f}");
  p("Inspect {l} activity near {#a}, {#b}", "Sprawdź aktywność ({l|t}) w pobliżu {#a}, {#b}");
  p("{#t} · {#o} vs {#m}", "{#t} · {#o} wobec {#m}");
  p("{t} · out of season", "{t|t} · poza sezonem");
  p("{#a}× normal", "{#a}× normy");
  p("{#a}× normal · {#o} vs {#m}", "{#a}× normy · {#o} wobec {#m}");
  p("{#a}× normal · {#o} vs {#m} · out of season", "{#a}× normy · {#o} wobec {#m} · poza sezonem");
  p("watch", "obserwacja"); p("warning", "ostrzeżenie"); p("critical", "krytyczny"); p("none", "brak");
  p("WATCH", "OBSERWACJA"); p("WARNING", "OSTRZEŻENIE"); p("CRITICAL", "KRYTYCZNY");
  p("Out of season (burned in {#n} years)", "Poza sezonem (pożary w {#n} {n|pl:roku,latach,latach})");
  p("Satellite positions unavailable: {e}", "Pozycje satelitów niedostępne: {e}");
  p("{n} ({i}), swath {#k} km", "{n} ({i}), pas skanowania {#k} km");
  p("{n} · {i} · swath {#k} km", "{n} · {i} · pas skanowania {#k} km");
  p("{n} orbit", "Orbita: {n}");
  p("Show {n}", "Pokaż: {n}");
  p("Radar mosaic {t} UTC (past 10 min frame). Radar covers land areas with ground radar only.", "Mozaika radarowa {t} UTC (klatka sprzed 10 min). Radar obejmuje tylko obszary lądowe z radarami naziemnymi.");
  p("Wind unavailable (Open-Meteo): {e}", "Wiatr niedostępny (Open-Meteo): {e}");
  p("Open-Meteo 10 m wind, {t}. Labels give speed and the direction it comes from.", "Wiatr 10 m z Open-Meteo, {t}. Etykiety podają prędkość i kierunek, z którego wieje.");
  p("{#v} km/h {c}", "{#v} km/h {c}");
  p("Radar © RainViewer", "Radar © RainViewer");

  /* ================================================================ app.js: ML + physics simulation, player */
  e({
    "The combined ML + physics service is unavailable. Retry when the service is ready.": "Połączona usługa ML + fizyka jest niedostępna. Spróbuj ponownie, gdy będzie gotowa.",
    "Ready. Press Run prediction.": "Gotowe. Naciśnij „Uruchom prognozę”.",
    "Ready. Select a fire on the map to start.": "Gotowe. Wybierz pożar na mapie, aby zacząć.",
    "Checking the simulation service…": "Sprawdzanie usługi symulacji…",
    "Run ML + physics": "Uruchom ML + fizyka",
    "The simulation starts from nearby satellite detections and groups them into separate fires. Locations are pixel centres (VIIRS ~375 m, MODIS ~1 km), so the true fire edge can be several hundred metres away.":
      "Symulacja startuje z pobliskich detekcji satelitarnych i grupuje je w oddzielne pożary. Lokalizacje to środki pikseli (VIIRS ~375 m, MODIS ~1 km), więc faktyczna krawędź pożaru może leżeć kilkaset metrów dalej.",
    "The simulation ignites exactly where you clicked. Without a detection there, the start location is an assumption you chose.":
      "Symulacja rozpoczyna zapłon dokładnie w miejscu kliknięcia. Bez detekcji w tym miejscu punkt startu jest założeniem wybranym przez Ciebie.",
    "The simulation starts from archive burning cells for the chosen date. Cells are 0.05° (~5 km) squares, so the start shape is coarse.":
      "Symulacja startuje z archiwalnych płonących komórek z wybranej daty. Komórki to kwadraty 0,05° (~5 km), więc kształt początkowy jest zgrubny.",
    "Before 20 Jan 2012 these cells are reconstructed from MODIS (1 km).": "Przed 20 sty 2012 te komórki są zrekonstruowane z MODIS (1 km).",
    "Location selected. Run ML + physics to load the timeline.": "Wybrano lokalizację. Uruchom ML + fizyka, aby wczytać oś czasu.",
    "The combined service is unavailable. Please retry once it is ready.": "Usługa łączona jest niedostępna. Spróbuj ponownie, gdy będzie gotowa.",
    "Running ML + physics…": "Uruchamianie ML + fizyka…",
    "Calculating combined daily guidance and hourly spread… This usually takes about 50 seconds, but can take up to 3 minutes in harder cases.": "Obliczanie łącznych wskazówek dziennych i godzinowego rozprzestrzeniania… Zwykle trwa to około 50 sekund, ale w trudniejszych przypadkach może potrwać nawet do 3 minut.",
    "Calculating combined daily guidance and hourly spread…": "Obliczanie łącznych wskazówek dziennych i godzinowego rozprzestrzeniania…",
    "Prediction unavailable. Adjust the location or retry.": "Prognoza niedostępna. Zmień lokalizację lub spróbuj ponownie.",
    "Both ML + physics guidance and simulation frames are required.": "Wymagane są zarówno wskazówki ML + fizyka, jak i klatki symulacji.",
    "Confidence not calibrated": "Pewność nieskalibrowana",
    "No validated confidence percentage is available for this individual fire. Colours show spread timing, not confidence.":
      "Dla tego pożaru nie ma zwalidowanego procentu pewności. Kolory pokazują czas rozprzestrzeniania, nie pewność.",
    "Live predictions use reduced seven-day history and have not been validated as a live operational forecast.":
      "Prognozy na żywo korzystają ze skróconej, siedmiodniowej historii i nie zostały zwalidowane jako operacyjna prognoza na żywo.",
    "Weather is an illustrative fallback scenario rather than a retrieved forecast.": "Pogoda to ilustracyjny scenariusz zastępczy, a nie pobrana prognoza.",
    "Rothermel surface spread with hourly forecast weather": "Rozprzestrzenianie powierzchniowe wg Rothermela z godzinową prognozą pogody",
    "Physics simulation": "Symulacja fizyczna",
    "Assumed ignition": "Zapłon założony",
    "Ignition estimate": "Szacunek zapłonu",
    "Ignition note.": "Uwaga o zapłonie.",
    "Ignition location is approximate.": "Lokalizacja zapłonu jest przybliżona.",
    "Estimate": "Szacunek",
    "Fallback": "Wartość zastępcza",
    "Fuel class assumed, no fuel map used.": "Przyjęto klasę paliwa, nie użyto mapy paliw.",
    "Fuel assumption": "Założenie dotyczące paliwa",
    "Weather updates each simulation hour.": "Pogoda aktualizuje się w każdej godzinie symulacji.",
    "Historical weather replay; observed future weather.": "Odtworzenie historycznej pogody; zaobserwowana pogoda z przyszłości.",
    "Weather unavailable: illustrative fallback scenario.": "Pogoda niedostępna: ilustracyjny scenariusz zastępczy.",
    "Weather source": "Źródło pogody",
    "Weather service unavailable; fallback weather used.": "Usługa pogodowa niedostępna; użyto pogody zastępczej.",
    "Weather fallback in use.": "Używana jest pogoda zastępcza.",
    "Weather coverage or source is reduced.": "Pokrycie lub źródło pogody jest ograniczone.",
    "Weather estimate": "Szacunek pogody",
    "Landscape layers loaded.": "Wczytano warstwy krajobrazu.",
    "Mapped fuel, terrain and vegetation conditions used where available.": "Tam, gdzie dostępne, użyto zmapowanych warunków paliwa, terenu i roślinności.",
    "Landscape unavailable; assumed fuel and fallback terrain used.": "Krajobraz niedostępny; użyto założonego paliwa i terenu zastępczego.",
    "Landscape": "Krajobraz",
    "The fire reached the edge of the simulation grid, so the final area is a lower bound.": "Pożar dotarł do krawędzi siatki symulacji, więc końcowa powierzchnia jest dolnym ograniczeniem.",
    "Fire reached the simulation domain edge.": "Pożar dotarł do krawędzi obszaru symulacji.",
    "Truncated": "Obcięte",
    "Dashed outlines show the low and high envelopes for the current frame.": "Linie przerywane pokazują dolną i górną obwiednię dla bieżącej klatki.",
    "No prediction loaded": "Nie wczytano prognozy",
    "Timeline": "Oś czasu",
    "ML + physics · hourly perimeter and daily risk guidance": "ML + fizyka · godzinowy obrys i dzienne wskazówki ryzyka",
    "Settings changed. Run ML + physics to update the timeline.": "Zmieniono ustawienia. Uruchom ML + fizyka, aby zaktualizować oś czasu.",
    "Experimental": "Eksperymentalne",
    "Select a fire on the map first.": "Najpierw wybierz pożar na mapie.",
    "Estimated location ": "Szacowana lokalizacja",
    "wind —": "wiatr —",
    "Pick a time on the timeline": "Wybierz czas na osi czasu",
    "nearby live detections": "pobliskie detekcje na żywo",
    "selected point": "wybrany punkt",
    "archive fire cells": "archiwalne komórki pożaru",
    "km² simulated": "km² (symulacja)",
    "horizon": "horyzont",
    "max spread rate (m/min)": "maks. tempo rozprzestrzeniania (m/min)",
    "km² simulated at end": "km² (symulacja) na końcu",
  });
  p("Running ML + physics for {#h} h in {#s}-minute steps…", "Uruchamianie ML + fizyka dla {#h} godz. w krokach {#s}-minutowych…");
  p("Done: {#n} frames.", "Gotowe: {#n} {n|pl:klatka,klatki,klatek}.");
  p("Combined prediction unavailable: {e}", "Prognoza łączona niedostępna: {e}");
  p("Combined prediction failed: {e}", "Prognoza łączona nie powiodła się: {e}");
  p("This run includes {#n} fire groups; displayed areas are totals across those groups.", "Ten przebieg obejmuje {#n} {n|pl:grupę pożarów,grupy pożarów,grup pożarów}; wyświetlane powierzchnie to sumy dla tych grup.");
  p("Area sensitivity: {#a}–{#b} km² across weather scenarios. This is not a statistical confidence interval.", "Wrażliwość powierzchni: {#a}–{#b} km² w scenariuszach pogodowych. To nie jest statystyczny przedział ufności.");
  p("Day {#d} combined model score: {#s} (uncalibrated) · daily guidance", "Dzień {#d}: łączny wynik modelu {#s} (nieskalibrowany) · wskazówki dzienne");
  p("{#n} frames ready. Drag the slider or press Play.", "Gotowych klatek: {#n}. Przeciągnij suwak lub naciśnij „Odtwórz”.");
  p("{#n} frames over {#h} h ({#s}-minute steps), ignition: {i}.", "{#n} {n|pl:klatka,klatki,klatek} w ciągu {#h} godz. (kroki {#s}-minutowe), zapłon: {i|t}.");
  p(". {#n} frames over {#h} h ({#s}-minute steps), ignition: {i}.", ". {#n} {n|pl:klatka,klatki,klatek} w ciągu {#h} godz. (kroki {#s}-minutowe), zapłon: {i|t}.");
  p("Areas total {#n} nearby fire groups.", "Powierzchnie sumują {#n} {n|pl:pobliską grupę pożarów,pobliskie grupy pożarów,pobliskich grup pożarów}.");
  p("Ignition used: {#a}, {#b} ({s}).", "Użyty zapłon: {#a}, {#b} ({s}).");
  p("Ignition used: {#a}, {#b}.", "Użyty zapłon: {#a}, {#b}.");
  p("Ignition snapped {#k} km from your point.", "Zapłon przyciągnięto o {#k} km od Twojego punktu.");
  p("Ignition from {#n} detections ({#c} grid cells).", "Zapłon z {#n} {n|pl:detekcji,detekcji,detekcji} ({#c} komórek siatki).");
  p("Ignition from {#n} detection ({#c} grid cells).", "Zapłon z {#n} detekcji ({#c} komórek siatki).");
  p("Requested \"{a}\", used \"{b}\" instead.", "Zażądano „{a}”, użyto zamiast tego „{b}”.");
  p("Fuel: {c}{m} (assumed).", "Paliwo: {c|t}{m} (założone).");
  p("Fuel: {c}{m}.", "Paliwo: {c|t}{m}.");
  p("Fuel: {c}", "Paliwo: {c|t}");
  p("Hourly forecast weather · {s}", "Godzinowa prognoza pogody · {s}");
  p("Ignition (estimated) {#a}, {#b}", "Zapłon (szacowany) {#a}, {#b}");
  p("Risk guidance: day {#d}, valid through {t}.", "Wskazówki ryzyka: dzień {#d}, ważne do {t}.");
  p("Frame {#i} of {#n}, {t}", "Klatka {#i} z {#n}, {t}");
  p("T+{#h} h · {t}", "T+{#h} godz. · {t}");
  p("{#a} km² simulated", "{#a} km² (symulacja)");
  p("{#a} km² simulated across {#n} fire groups", "{#a} km² (symulacja) w {#n} {n|pl:grupie pożarów,grupach pożarów,grupach pożarów}");
  p("{#v} km/h from {c}", "{#v} km/h z kierunku {c}");
  p("{#v} km/h from {c}, gusts {g}", "{#v} km/h z kierunku {c}, porywy {g}");
  p("{#v}% RH", "{#v}% wilg. wzgl.");
  p("{#v}% RH · FM {f}", "{#v}% wilg. wzgl. · FM {f}");
  p("ROS {#v} m/min", "ROS {#v} m/min");
  p("ROS {#v} m/min · {f}", "ROS {#v} m/min · {f|t}");
  p("max spread rate (m/min)", "maks. tempo rozprzestrzeniania (m/min)");
  p("{#a} km² simulated", "{#a} km² (symulacja)");

  /* ================================================================ ui-replay.js */
  e({
    "Hit": "Trafienie", "Near hit (within 5 km)": "Prawie trafienie (w promieniu 5 km)", "Miss": "Chybienie",
    "False alarm": "Fałszywy alarm", "Unverifiable (cloud)": "Nieweryfikowalne (chmury)",
    "Replay legend": "Legenda odtworzenia",
    "Replay a past forecast": "Odtwórz dawną prognozę",
    "The forecast is shown as it would have been issued, using only observations up to the cutoff time. Reveal afterwards compares it with what satellites detected.":
      "Prognoza jest pokazana tak, jak zostałaby wydana, z użyciem wyłącznie obserwacji do chwili odcięcia. Odkrycie wyników porównuje ją następnie z tym, co wykryły satelity.",
    "Incident": "Zdarzenie",
    "Loading incidents…": "Wczytywanie zdarzeń…",
    "Incidents unavailable": "Zdarzenia niedostępne",
    "Custom location and date…": "Własna lokalizacja i data…",
    "Date (UTC)": "Data (UTC)",
    "Custom replays are computed on demand and can take a minute. Dates before 2024 can only be replayed with ML alone.":
      "Własne odtworzenia są liczone na żądanie i mogą trwać minutę. Daty sprzed 2024 można odtworzyć tylko samym ML.",
    "Run replay": "Uruchom odtworzenie",
    "Forecast method": "Metoda prognozy",
    "ML only": "Tylko ML",
    "Forecast day": "Dzień prognozy",
    "Day 1": "Dzień 1", "Day 2": "Dzień 2", "Day 3": "Dzień 3",
    "Reveal what happened": "Pokaż, co się wydarzyło",
    "Hide what happened": "Ukryj, co się wydarzyło",
    "Matching rule": "Reguła dopasowania",
    "Strict (same cell)": "Ścisła (ta sama komórka)",
    "Within 5 km": "W promieniu 5 km",
    "strict (same cell)": "ścisłe (ta sama komórka)",
    "within-5 km": "w promieniu 5 km",
    "strict": "ścisłe",
    "Score against the ring baseline": "Wynik względem linii bazowej „pierścień”",
    "Ring baseline: a comparator that marks every cell within about 5 km of the burning area as burned. It is not a forecast model. Within 5 km is a spatial overlap score, not classification accuracy.":
      "Linia bazowa „pierścień”: punkt odniesienia, który oznacza jako spaloną każdą komórkę w promieniu około 5 km od płonącego obszaru. To nie jest model prognostyczny. „W promieniu 5 km” to wynik nakładania przestrzennego, a nie trafność klasyfikacji.",
    "One event is not a benchmark.": "Jedno zdarzenie to nie jest benchmark.",
    "Curated incidents are illustrative. Probabilities are uncalibrated; the walk-forward benchmark in the model card is the statistically meaningful measure.":
      "Wybrane zdarzenia mają charakter ilustracyjny. Prawdopodobieństwa są nieskalibrowane; istotną statystycznie miarą jest benchmark walk-forward z karty modelu.",
    "Method and information cutoff": "Metoda i moment odcięcia informacji",
    "No-leakage audit": "Audyt braku wycieku danych",
    "Limitations of this replay": "Ograniczenia tego odtworzenia",
    "Enter latitude, longitude and date.": "Wpisz szerokość, długość geograficzną i datę.",
    "Not saved. Computing: usually about 50 seconds, up to 3 minutes in harder cases…": "Brak zapisu. Liczenie: zwykle około 50 sekund, w trudniejszych przypadkach do 3 minut…",
    "Loading saved replay…": "Wczytywanie zapisanego odtworzenia…",
    "No saved replay for this request. Computing it now; this can take up to a minute…": "Brak zapisanego odtworzenia dla tego żądania. Liczę je teraz; może to potrwać do minuty…",
    "Flagged as likely to burn": "Oznaczona jako prawdopodobnie spłonie",
    "Not flagged": "Nieoznaczona",
    "Burning at forecast time": "Płonie w chwili prognozy",
    "Score low to high (uncalibrated)": "Wynik od niskiego do wysokiego (nieskalibrowany)",
    "Dark outline: flagged cell": "Ciemny kontur: komórka oznaczona",
    "Hits": "Trafienia", "Misses": "Chybienia", "False alarms": "Fałszywe alarmy",
    "Precision": "Precyzja", "Recall": "Czułość",
    "Forecast IoU": "IoU prognozy", "Forecast within 5 km": "Prognoza ≤ 5 km",
    "Ring IoU": "IoU pierścienia", "Ring within 5 km": "Pierścień ≤ 5 km",
    "Strict: a hit is a newly burning cell predicted in the same 0.05 degree cell (about 5.5 km). Counts are cumulative to each day.":
      "Ścisłe: trafienie to nowo płonąca komórka przewidziana w tej samej komórce 0,05 stopnia (około 5,5 km). Liczby są skumulowane do każdego dnia.",
    "Within 5 km: hits count observed cells with a flagged cell within one grid cell; false alarms are flagged cells with no observed fire within one grid cell.":
      "W promieniu 5 km: trafienia to zaobserwowane komórki, w odległości jednej komórki siatki od których jest komórka oznaczona; fałszywe alarmy to komórki oznaczone bez zaobserwowanego pożaru w odległości jednej komórki siatki.",
    "Cloud and overpass proxy not available for this replay: absence of a detection is not proof of no fire.":
      "Dla tego odtworzenia brak przybliżenia zachmurzenia i przelotów: brak detekcji nie dowodzi braku pożaru.",
    "Cloud caveat": "Zastrzeżenie dot. chmur",
    "Truth used in the forecast:": "Dane prawdziwe użyte w prognozie:",
    "Reveal computed after the forecast:": "Odkrycie wyliczone po prognozie:",
    "Fire observations:": "Obserwacje pożarów:", "Models:": "Modele:", "Weather:": "Pogoda:",
    "no": "nie", "yes": "tak",
    "ML only (no ML + physics blender exists for this date)": "Tylko ML (dla tej daty nie istnieje blender ML + fizyka)",
    "ML + physics, experimental blend": "ML + fizyka, eksperymentalne połączenie",
    "Probabilities are uncalibrated. The shaded colours are relative scores, not chances.": "Prawdopodobieństwa są nieskalibrowane. Odcienie kolorów to wyniki względne, a nie szanse.",
    "Why this incident was chosen": "Dlaczego wybrano to zdarzenie",
    "not scored (no archive data for this day)": "nie oceniono (brak danych archiwalnych dla tego dnia)",
    "not scored": "nie oceniono",
    "misses": "chybienia", "false alarms": "fałszywe alarmy",
    "Information cutoff:": "Moment odcięcia informacji:",
  });
  p("Forecast for {a} (cumulative from the cutoff)", "Prognoza na {a|t} (skumulowana od chwili odcięcia)");
  p("Issued forecast, day {#d}{s}. Black cells were already burning at the cutoff. Nothing after the cutoff is shown until you reveal.", "Wydana prognoza, dzień {#d}{s}. Czarne komórki już płonęły w chwili odcięcia. Nic po chwili odcięcia nie jest pokazywane, dopóki nie odkryjesz wyników.");
  p("{c} score {#s} (uncalibrated)", "{c|t} wynik {#s} (nieskalibrowany)");
  p("Combined", "Łączny");
  p("ML", "ML");
  p("First observed on day {#d}, cumulative to day {#e}", "Pierwszy raz zaobserwowano w dniu {#d}, skumulowane do dnia {#e}");
  p("First observed on day n/a, cumulative to day {#e}", "Pierwszy raz zaobserwowano: brak danych, skumulowane do dnia {#e}");
  p("Blender: {b} (fit on {y}).", "Blender: {b} (dopasowany na {y}).");
  p(", weather {w} (fallback weather used)", ", pogoda {w} (użyto pogody zastępczej)");
  p(", weather {w}", ", pogoda {w}");
  p("Day {#d}: forecast strict IoU {a} is {c} the ring baseline ({b}); within-5 km score {x} is {d} the ring baseline ({y}).", "Dzień {#d}: ścisłe IoU prognozy {a} jest {c|t} linii bazowej „pierścień” ({b}); wynik w promieniu 5 km {x} jest {d|t} linii bazowej „pierścień” ({y}).");
  p("above", "powyżej"); p("below", "poniżej"); p("equal to", "równe");
  p("hits, day {#d}", "trafienia, dzień {#d}");
  p("Day {#d}: modelled cloud cover averaged {a}% at the daytime pass and {b}% at the night pass. {c}% of the window was treated as obscured (cumulative unverifiable fraction {d}).", "Dzień {#d}: modelowane zachmurzenie wyniosło średnio {a}% w przelocie dziennym i {b}% w przelocie nocnym. {c}% okna uznano za zasłonięte (skumulowany udział nieweryfikowalny {d}).");
  p("{#n} flagged cell could not be verified (grey hatch) and is not counted as false alarms in the adjusted scores.", "{#n} oznaczonej komórki nie dało się zweryfikować (szary deseń) i nie wlicza się jej do fałszywych alarmów w wynikach skorygowanych.");
  p("{#n} flagged cells could not be verified (grey hatch) and are not counted as false alarms in the adjusted scores.", "{#n} {n|pl:oznaczonej komórki,oznaczone komórki,oznaczonych komórek} nie dało się zweryfikować (szary deseń) i nie wlicza się ich do fałszywych alarmów w wynikach skorygowanych.");
  p("Adjusted ({m}): precision {a}, IoU {b}.", "Skorygowane ({m|t}): precyzja {a}, IoU {b}.");
  p("Threshold for obscured: {#t}% cloud.", "Próg uznania za zasłonięte: {#t}% zachmurzenia.");
  p("Per day, {m} matching", "Dla każdego dnia, dopasowanie {m|t}");
  p("{b}, forecast issued {d}.", "{b}, prognoza wydana {d}.");
  p("Custom location", "Własna lokalizacja");
  p("Replay unavailable: {e}", "Odtworzenie niedostępne: {e}");

  /* ================================================================ ui-harmonize.js: footer, About, 25-year record */
  e({
    "Fire detections:": "Detekcje pożarów:",
    "Weather data by Open-Meteo.com": "Dane pogodowe: Open-Meteo.com",
    "OpenStreetMap contributors": "autorzy OpenStreetMap",
    "Esri imagery only when selected in Layers": "Zdjęcia Esri tylko po wybraniu w Warstwach",
    "About this tool": "O tym narzędziu",
    "FireSpot is a research prototype, not validated for operational decisions": "FireSpot to prototyp badawczy, niezwalidowany do decyzji operacyjnych",
    "(evacuation, dispatch, insurance pricing, safety of life). Everything below is a retrospective benchmark on satellite detections, copied from the model card (":
      "(ewakuacja, dysponowanie sił, wycena ubezpieczeń, bezpieczeństwo życia). Wszystko poniżej to retrospektywny benchmark na detekcjach satelitarnych, przepisany z karty modelu (",
    "in the repository).": "w repozytorium).",
    "Headline numbers": "Najważniejsze liczby",
    "Spread forecast (ML, walk-forward, 100 sampled fires per test year 2023-2025): within-5 km score on day 1 is 52-59% and on day 3 69-76%. Exact-cell (strict) overlap is 18-23% on day 1 and 25-31% on day 3. The project target of more than 70% on day 1 is not met.":
      "Prognoza rozprzestrzeniania (ML, walk-forward, 100 wylosowanych pożarów na rok testowy 2023-2025): wynik w promieniu 5 km wynosi 52-59% w dniu 1 i 69-76% w dniu 3. Pokrycie dokładnie w tej samej komórce (ścisłe) wynosi 18-23% w dniu 1 i 25-31% w dniu 3. Cel projektu, ponad 70% w dniu 1, nie został osiągnięty.",
    "ML + physics blend: no measured gain over ML alone (2024, day 1 within 5 km: 58.68% vs 58.89%).":
      "Połączenie ML + fizyka: brak zmierzonej poprawy względem samego ML (2024, dzień 1, w promieniu 5 km: 58,68% wobec 58,89%).",
    "Physics simulator: validated on 20 fires only; it over-predicts area, typically 3-6 times with calibration.":
      "Symulator fizyczny: zwalidowany tylko na 20 pożarach; zawyża powierzchnię, zwykle 3-6 razy przy kalibracji.",
    "1 km alerts (test 2023-2025): precision 63-66%, recall only 9-15%.": "Alerty 1 km (test 2023-2025): precyzja 63-66%, czułość (recall) zaledwie 9-15%.",
    "MODIS-era reconstruction before 2012 (held-out 2021-2025): precision 0.47, recall 0.35, IoU 0.25. It recovers totals better than individual cells.":
      "Rekonstrukcja z ery MODIS sprzed 2012 (zbiór wyłączony 2021-2025): precyzja 0,47, czułość 0,35, IoU 0,25. Lepiej odtwarza sumy niż pojedyncze komórki.",
    "Routine-burning vs anomaly classifier: 10 of 12 independent events (a small, hand-picked sample).":
      "Klasyfikator rutynowe wypalanie a anomalia: 10 z 12 niezależnych zdarzeń (mała próba dobrana ręcznie).",
    "Claims we do not make": "Czego nie twierdzimy",
    "\"70% accurate\" day-1 forecasts, calibrated probabilities or confidence intervals.": "Że prognozy na dzień 1 są „trafne w 70%”, ani że mamy skalibrowane prawdopodobieństwa czy przedziały ufności.",
    "That the physics simulation predicts area burned, or that ML + physics beats ML alone.": "Że symulacja fizyczna przewiduje spaloną powierzchnię, ani że ML + fizyka przewyższa sam ML.",
    "Forecast skill with real weather forecasts: backtests use observed reanalysis weather.": "Skuteczności prognoz z prawdziwymi prognozami pogody: testy wsteczne używają zaobserwowanej pogody z reanalizy.",
    "That reconstructed 2000-2011 data equals real VIIRS data.": "Że zrekonstruowane dane z lat 2000-2011 są równoważne prawdziwym danym VIIRS.",
    "That the live anomaly alerts are validated (no lead-time or false-alarm study), or that 1 km alerts cover a whole fire.":
      "Że alerty anomalii na żywo są zwalidowane (brak badania czasu wyprzedzenia i fałszywych alarmów), ani że alerty 1 km obejmują cały pożar.",
    "That a missing detection means no fire: satellites miss fires under cloud and smoke.": "Że brak detekcji oznacza brak pożaru: satelity nie widzą pożarów pod chmurami i dymem.",
    "Worldwide baselines: fully processed for Brazil, Australia, India, the United States and Canada only.": "Norm światowych: w pełni przetworzone są tylko Brazylia, Australia, Indie, Stany Zjednoczone i Kanada.",
    "Operational readiness, or endorsement by NASA, ESA or NOAA.": "Gotowości operacyjnej ani poparcia ze strony NASA, ESA czy NOAA.",
    "Five countries (Brazil, Australia, India, United States, Canada)": "Pięć krajów (Brazylia, Australia, Indie, Stany Zjednoczone, Kanada)",
    "Brazil": "Brazylia", "India": "Indie", "United States": "Stany Zjednoczone", "Canada": "Kanada",
    "Fire detections ": "Detekcje pożarów",
    "Burning 0.05 deg cell-days": "Komórko-dni z pożarem (komórki 0,05°)",
    "Fire radiative power, sum": "Moc radiacyjna pożaru (FRP), suma",
    "25-year harmonized record": "25-letni zharmonizowany zapis",
    "MODIS (2000-) and VIIRS (2012-) count fires differently, so a plain splice has an artificial jump in 2012. Before 2012-01-20 the harmonized series is reconstructed from MODIS as a VIIRS equivalent. Region-wide data, not the area you drew.":
      "MODIS (od 2000) i VIIRS (od 2012) liczą pożary inaczej, więc zwykłe zszycie ma sztuczny skok w 2012. Przed 2012-01-20 zharmonizowana seria jest zrekonstruowana z MODIS jako ekwiwalent VIIRS. Dane dla całego regionu, a nie dla narysowanego obszaru.",
    "Resolution": "Rozdzielczość",
    "Yearly": "Rocznie", "Monthly": "Miesięcznie",
    "Raw record, by sensor": "Surowy zapis, według sensora",
    "Harmonized record": "Zharmonizowany zapis",
    "Hover, tap or use the arrow keys on a chart to read values.": "Najedź, dotknij lub użyj klawiszy strzałek na wykresie, aby odczytać wartości.",
    "Data table and notes": "Tabela danych i uwagi",
    "Loading harmonized series…": "Wczytywanie zharmonizowanej serii…",
    "Terra-only era": "Era tylko Terra",
    "Hollow marker = partial year. Dotted vertical line = 2012 sensor change.": "Pusty znacznik = rok niepełny. Pionowa linia kropkowana = zmiana sensora w 2012.",
    "2012 sensor change": "zmiana sensora 2012",
    "Monthly values: switch to yearly resolution to see the data table.": "Wartości miesięczne: przełącz na rozdzielczość roczną, aby zobaczyć tabelę danych.",
    "Year": "Rok",
    "What the chart says": "Co mówi wykres",
    "Caveat.": "Zastrzeżenie.",
    "Harmonization reduces the 2012 step; it does not remove it for every measure. For burning cell-days across the five countries the plain harmonized series still shows a step of about x1.5 in 2012, while the overlap-calibrated variant is about x1.12. Fire detections are the cleanest measure. The pre-2012 part is a statistical reconstruction (shaded), not an observation, and it recovers totals better than individual cells.":
      "Harmonizacja zmniejsza skok z 2012, ale nie usuwa go dla każdej miary. Dla komórko-dni z pożarem w pięciu krajach zwykła zharmonizowana seria nadal pokazuje skok około x1,5 w 2012, a wariant skalibrowany na nakładaniu około x1,12. Detekcje pożarów to najczystsza miara. Część sprzed 2012 to statystyczna rekonstrukcja (zacieniona), a nie obserwacja, i lepiej odtwarza sumy niż pojedyncze komórki.",
    "High year-to-year correlation is not a cell-by-cell recovery.": "Wysoka korelacja rok do roku nie oznacza odtworzenia komórka po komórce.",
  });
  p("Harmonized record unavailable: {e}", "Zharmonizowany zapis niedostępny: {e}");
  p("Raw {l} by sensor, {r}. MODIS, VIIRS S-NPP and VIIRS NOAA-20 shown separately; a step appears in 2012 when VIIRS starts.", "Surowe {l|t} według sensora, {r|t}. MODIS, VIIRS S-NPP i VIIRS NOAA-20 pokazano osobno; skok pojawia się w 2012, gdy startuje VIIRS.");
  p("Harmonized {l}, {r}. Years before 2012 are reconstructed from MODIS as a VIIRS equivalent.", "Zharmonizowane {l|t}, {r|t}. Lata sprzed 2012 są zrekonstruowane z MODIS jako ekwiwalent VIIRS.");
  p("Reconstructed from MODIS (through {d})", "Zrekonstruowane z MODIS (do {d})");
  p("Yearly values ({u})", "Wartości roczne ({u|t})");
  p("{y} · {u}. {rest}", "{y} · {u|t}. {rest}");
  p("Raw splice: 2012 is {x} the 2011 level.", "Surowe zszycie: 2012 to {x} poziomu z 2011.");
  p("Harmonized: 2012 over 2011 is {x}", "Zharmonizowane: 2012 względem 2011 to {x}");
  p("(overlap-calibrated variant {x})", "(wariant skalibrowany na nakładaniu {x})");
  p("; mean 2012-2014 over 2009-2011 is {x}", "; średnia 2012-2014 względem 2009-2011 to {x}");
  p("Trend 2003-2025:", "Trend 2003-2025:");
  p("Reconstruction check (held-out 2021-2025):", "Kontrola rekonstrukcji (zbiór wyłączony 2021-2025):");
  p("reconstructed over real VIIRS median {x} (x1.00 would be an exact match); annual correlation {c}.", "mediana zrekonstruowane / prawdziwe VIIRS: {x} (x1,00 oznaczałoby dokładne dopasowanie); korelacja roczna {c}.");
  p("Raw splice:", "Surowe zszycie:"); p("Harmonized:", "Zharmonizowane:");
  p("Calibrated variant: {v}.", "Wariant skalibrowany: {v}.");
  p("Terra", "Terra");

  /* ================================================================ ui-history-fix.js: sensor-honesty notes */
  e({
    "MODIS only (Terra + Aqua)": "Tylko MODIS (Terra + Aqua)",
    "MODIS + S-NPP VIIRS (VIIRS from 20 Jan 2012)": "MODIS + S-NPP VIIRS (VIIRS od 20 sty 2012)",
    "MODIS + two VIIRS satellites (NOAA-20 from 2018)": "MODIS + dwa satelity VIIRS (NOAA-20 od 2018)",
    "MODIS only": "Tylko MODIS", "NOAA-20 added": "dodano NOAA-20", "NOAA-20 added (Apr)": "dodano NOAA-20 (kwi)",
    "+ S-NPP VIIRS": "+ S-NPP VIIRS", "+ NOAA-20": "+ NOAA-20", "+VIIRS": "+VIIRS",
    "Yearly chart series": "Seria wykresu rocznego",
    "This measure": "Ta miara",
    "By sensor (raw)": "Według sensora (surowe)",
    "Sensor eras": "Ery sensorów",
    "(bars and labels at the right edge, dashed lines mark the changes):": "(paski i etykiety przy prawej krawędzi, linie przerywane oznaczają zmiany):",
    "harmonized FRP (MW)": "zharmonizowane FRP (MW)",
    "burning cell-days, MODIS or VIIRS": "komórko-dni z pożarem, MODIS lub VIIRS",
    "MODIS-only years": "lata tylko z MODIS",
    "shaded: MODIS only": "zacienione: tylko MODIS",
    "dashed lines: VIIRS 2012, NOAA-20 2018": "linie przerywane: VIIRS 2012, NOAA-20 2018",
    "· * partial year": "· * rok niepełny",
    "Harmonized FRP, VIIRS-equivalent (MW)": "Zharmonizowane FRP, ekwiwalent VIIRS (MW)",
    "VIIRS burning cells (raw)": "Płonące komórki VIIRS (surowe)",
    "MODIS burning cells (raw)": "Płonące komórki MODIS (surowe)",
    "Any-sensor burning cells (raw, mixed)": "Płonące komórki, dowolny sensor (surowe, mieszane)",
    "VIIRS detections (raw)": "Detekcje VIIRS (surowe)",
    "MODIS detections (raw)": "Detekcje MODIS (surowe)",
    "Harmonized fire power (VIIRS-equivalent).": "Zharmonizowana moc pożarów (ekwiwalent VIIRS).",
    "MODIS fire power is converted to the VIIRS scale before summing, so MW is comparable between sensors. Counts are not: each sensor counts differently.":
      "Moc pożarów z MODIS jest przeliczana na skalę VIIRS przed sumowaniem, więc MW są porównywalne między sensorami. Liczby nie są: każdy sensor liczy inaczej.",
    "Raw count, two sensors mixed.": "Surowa liczba, dwa sensory zmieszane.",
    "Raw MODIS count (Terra + Aqua, 1 km).": "Surowa liczba MODIS (Terra + Aqua, 1 km).",
    "The same two instruments through the whole record, so it is the steadiest basis for trends across 2012, but it counts only a fraction of what VIIRS sees.":
      "Te same dwa instrumenty w całym zapisie, więc to najstabilniejsza podstawa do trendów w poprzek 2012, ale liczy tylko ułamek tego, co widzi VIIRS.",
    "Across Amazon, northern Australia, India, western USA and Canada, year by year, MODIS counted 13-56% of VIIRS cell-days and 8-31% of VIIRS detections.":
      "W Amazonii, północnej Australii, Indiach, zachodnich USA i Kanadzie, rok po roku, MODIS liczył 13-56% komórko-dni VIIRS i 8-31% detekcji VIIRS.",
    "Fire power converts well per detection (held-out 2021-2025 test: reconstructed total FRP within about 6% of real VIIRS), but a sum still only includes the cells each sensor detected, so some 2012 step remains. For a trend across 2012 compare against the MODIS-only measure, or read the harmonized record below.":
      "Moc pożaru dobrze się przelicza dla pojedynczej detekcji (test na zbiorze wyłączonym 2021-2025: zrekonstruowane łączne FRP w granicach około 6% od prawdziwego VIIRS), ale suma obejmuje tylko komórki wykryte przez każdy sensor, więc część skoku z 2012 pozostaje. Dla trendu w poprzek 2012 porównaj z miarą tylko z MODIS albo zajrzyj do zharmonizowanego zapisu poniżej.",
    "Baselines, z-scores and anomaly flags compare each year only with years of the same sensor era (before or from 2012).":
      "Normy, wyniki z i flagi anomalii porównują każdy rok tylko z latami z tej samej ery sensorów (przed 2012 lub od 2012).",
    "Baselines, z-scores and anomaly flags here compare against all years, including the MODIS-only years before 2012.":
      "Normy, wyniki z i flagi anomalii porównują tu ze wszystkimi latami, także z latami tylko z MODIS sprzed 2012.",
    "Mixes sensors: the 2012 and 2018 steps are instrument changes, not fire changes": "Miesza sensory: skoki w 2012 i 2018 to zmiany instrumentów, a nie zmiany w pożarach",
    "See the harmonized 25-year record": "Zobacz zharmonizowany 25-letni zapis",
    "Show MODIS-only burning cells (steady record)": "Pokaż płonące komórki tylko z MODIS (stabilny zapis)",
    "MODIS only ": "Tylko MODIS ",
  });
  p("MODIS only (before {d})", "Tylko MODIS (przed {d})");
  p("S-NPP VIIRS starts {d}", "Start S-NPP VIIRS: {d}");
  p("{#y}: {l}", "{#y}: {l|t}");
  p("It adds MODIS-only days before {d} to MODIS + VIIRS days after, so the record jumps in 2012 and again in 2018.", "Dodaje dni tylko z MODIS sprzed {d} do dni z MODIS + VIIRS po tej dacie, więc zapis skacze w 2012 i ponownie w 2018.");
  p("Raw VIIRS count (375 m, from {d}).", "Surowa liczba VIIRS (375 m, od {d}).");
  p("NOAA-20 joined S-NPP in {#y} with more overpasses, so a step in {#y2} is partly the instrument. Nothing before 2012.", "NOAA-20 dołączył do S-NPP w {#y} z większą liczbą przelotów, więc skok w {#y2} częściowo wynika z instrumentu. Brak danych sprzed 2012.");
  p("In this area, in {#n} overlap years ({#y}+), MODIS counted {a} of the burning cell-days VIIRS saw and {b} of its detections (medians).", "Na tym obszarze, w {#n} {n|pl:roku,latach,latach} nakładania ({#y}+), MODIS policzył {a} komórko-dni z pożarem widzianych przez VIIRS i {b} jego detekcji (mediany).");
  p("In this area, in {#n} overlap years ({#y}+), MODIS counted {a} of the burning cell-days VIIRS saw.", "Na tym obszarze, w {#n} {n|pl:roku,latach,latach} nakładania ({#y}+), MODIS policzył {a} komórko-dni z pożarem widzianych przez VIIRS.");
  p("Same-area step across 2012 (average 2013-15 over 2009-11): any-sensor cell-days {a}, harmonized FRP {b}, MODIS-only cell-days {c}. Part of any step is real year-to-year change.", "Skok na tym samym obszarze w poprzek 2012 (średnia 2013-15 względem 2009-11): komórko-dni dowolnego sensora {a}, zharmonizowane FRP {b}, komórko-dni tylko MODIS {c}. Część każdego skoku to rzeczywista zmiana rok do roku.");
  p("+ S-NPP VIIRS from {d}", "+ S-NPP VIIRS od {d}");
  p("+ NOAA-20 from {#y}", "+ NOAA-20 od {#y}");

  /* ================================================================ ui-forecast-extras / blindspots / scenarios / exposure */
  e({
    "Until next pass": "Do następnego przelotu",
    "The service is unavailable. Check the connection and try again.": "Usługa jest niedostępna. Sprawdź połączenie i spróbuj ponownie.",
    "Cancelled": "Anulowano", "Cancelled.": "Anulowano.",
    "Cancel": "Anuluj",
    "Hospital": "Szpital", "School": "Szkoła", "Fire station": "Remiza strażacka", "Power site": "Obiekt energetyczny",
    "Settlement": "Miejscowość", "Residential area": "Obszar mieszkalny", "Road": "Droga", "Railway": "Kolej", "Power line": "Linia energetyczna",
    "Buildings (~500 m cells)": "Budynki (komórki ~500 m)",
    "clear (model)": "bezchmurnie (model)", "partly cloudy": "częściowe zachmurzenie", "obscured (model)": "zasłonięte (model)", "cloud unknown": "zachmurzenie nieznane",
    "Next pass, blind spots and exposure": "Następny przelot, ślepe strefy i ekspozycja",
    "Observation gaps, scenarios and exposure": "Luki w obserwacji, scenariusze i ekspozycja",
    "Optional analysis for the selected fire. Estimates from satellite detections, hypothetical weather and OpenStreetMap. Scenario ranges are not confidence intervals.":
      "Opcjonalna analiza dla wybranego pożaru. Szacunki oparte na detekcjach satelitarnych, hipotetycznej pogodzie i OpenStreetMap. Zakresy scenariuszy nie są przedziałami ufności.",
    "Calibrate to this fire's recent growth (experimental)": "Skalibruj do niedawnego wzrostu tego pożaru (eksperymentalne)",
    "Calibration fits a spread multiplier to the fire's recent observed growth. Experimental: it did not improve results consistently in validation.":
      "Kalibracja dopasowuje mnożnik tempa rozprzestrzeniania do niedawno zaobserwowanego wzrostu pożaru. Eksperymentalne: w walidacji nie poprawiało wyników w sposób spójny.",
    "The combined ML + physics player currently applies its own fixed setting; this switch applies to scenario comparisons and to the player once the service accepts it.":
      "Odtwarzacz łączony ML + fizyka stosuje obecnie własne stałe ustawienie; ten przełącznik dotyczy porównań scenariuszy oraz odtwarzacza, gdy usługa zacznie go akceptować.",
    "Off by default: the physics runs on the issued weather and mapped landscape without fitting to recent growth.":
      "Domyślnie wyłączone: fizyka działa na wydanej pogodzie i zmapowanym krajobrazie, bez dopasowania do niedawnego wzrostu.",
    "scan angle above 45 degrees (pixels are large)": "kąt skanowania powyżej 45 stopni (piksele są duże)",
    "cloud cover 70% or more (model)": "zachmurzenie 70% lub więcej (model)",
    "no usable pass found": "nie znaleziono użytecznego przelotu",
    "Stem length: scan-angle tier (long = good, short = degraded)": "Długość trzonka: klasa kąta skanowania (długi = dobry, krótki = pogorszony)",
    "Faded: not usable": "Wyblakłe: nieużyteczne",
    "Ring: next usable pass": "Pierścień: następny użyteczny przelot",
    "Timeline legend": "Legenda osi czasu",
    "Time": "Czas", "Geometry": "Geometria", "Pixel": "Piksel", "Cloud (model)": "Chmury (model)", "Status": "Status",
    "usable": "użyteczny", "no detection": "brak detekcji", "fire detected": "wykryto pożar",
    "no detection yet (feed can lag)": "jeszcze brak detekcji (kanał może się opóźniać)",
    "Select a fire on the map to see when it can be observed again.": "Wybierz pożar na mapie, aby zobaczyć, kiedy można go ponownie zaobserwować.",
    "Retry": "Ponów",
    "Check when satellites next pass over this fire.": "Sprawdź, kiedy satelity następnie przelecą nad tym pożarem.",
    "Check next pass": "Sprawdź następny przelot",
    "Check again": "Sprawdź ponownie",
    "Observation age is the time since the last satellite detection near this point. FIRMS detections can lag the overpass by up to about 3 h.":
      "Wiek obserwacji to czas od ostatniej detekcji satelitarnej w pobliżu tego punktu. Detekcje FIRMS mogą opóźniać się względem przelotu nawet o około 3 godz.",
    "No usable pass (scan angle 45 degrees or less, not obscured by cloud, sensor delivering data) was found in the next 48 h. Observation stays uncertain.":
      "W ciągu najbliższych 48 godz. nie znaleziono użytecznego przelotu (kąt skanowania do 45 stopni, brak zasłonięcia chmurami, sensor dostarcza dane). Obserwacja pozostaje niepewna.",
    "Cloud is a model visibility indicator (Open-Meteo cloud cover), not proof that a sensor misses or sees a fire. Smoke, thin cirrus and fire size are not modelled.":
      "Chmury to modelowy wskaźnik widoczności (zachmurzenie z Open-Meteo), a nie dowód, że sensor pożar przegapi lub zobaczy. Dym, cienkie cirrusy i wielkość pożaru nie są modelowane.",
    "Only MODIS has detected this fire. The combined ML + physics run needs a VIIRS detection near the point on the same UTC day, so the simulation may be refused. Choose a fire with a VIIRS detection if the run is refused.":
      "Ten pożar wykrył tylko MODIS. Przebieg łączony ML + fizyka wymaga detekcji VIIRS w pobliżu punktu tego samego dnia UTC, więc symulacja może zostać odrzucona. Jeśli przebieg zostanie odrzucony, wybierz pożar z detekcją VIIRS.",
    "Archive ignition is selected. Switch Ignition to Nearest live fire to simulate until the next pass.": "Wybrano zapłon z archiwum. Przełącz „Źródło zapłonu” na „Najbliższy aktywny pożar”, aby symulować do następnego przelotu.",
    "No recent detection near this point, so there is no live fire to simulate.": "Brak niedawnej detekcji w pobliżu tego punktu, więc nie ma aktywnego pożaru do symulacji.",
    "All passes in the next 48 h": "Wszystkie przeloty w ciągu następnych 48 godz.",
    "Recent passes": "Ostatnie przeloty",
    "Looking up satellite passes…": "Wyszukiwanie przelotów satelitów…",
    "First lookups fetch cloud forecasts and can take up to a couple of minutes.": "Pierwsze wyszukiwania pobierają prognozy zachmurzenia i mogą potrwać kilka minut.",
    "Incident brief": "Raport zdarzenia",
    "A one-page printable brief with the simulated footprint, estimated exposure, data limits and sources. It re-runs the simulation and exposure on the server, so the first request can take one to two minutes. No loss or monetary figures are included.":
      "Jednostronicowy raport do wydruku z symulowanym zasięgiem, szacowaną ekspozycją, ograniczeniami danych i źródłami. Przelicza symulację i ekspozycję na serwerze, więc pierwsze żądanie może potrwać od jednej do dwóch minut. Raport nie zawiera strat ani kwot pieniężnych.",
    "Brief horizon": "Horyzont raportu",
    "Open brief (HTML)": "Otwórz raport (HTML)",
    "Download brief (PDF)": "Pobierz raport (PDF)",
    "Select a fire on the map first.": "Najpierw wybierz pożar na mapie.",
    /* blind spots */
    "No fire detected": "Nie wykryto pożaru",
    "Blind spots on the map": "Ślepe strefy na mapie",
    "Shows where satellites could not see well, so that \"no detection\" is not read as \"no fire\".": "Pokazuje, gdzie satelity nie widziały dobrze, aby „brak detekcji” nie był odczytywany jako „brak pożaru”.",
    "Show blind-spot overlay": "Pokaż nakładkę ślepych stref",
    "Update for this view": "Aktualizuj dla tego widoku",
    "Click the map to inspect a tile (turn off to select fires)": "Kliknij mapę, aby sprawdzić kafelek (wyłącz, aby wybierać pożary)",
    "The map has moved away from the loaded area. Use Update for this view.": "Mapa przesunęła się poza wczytany obszar. Użyj „Aktualizuj dla tego widoku”.",
    "Loading observation quality…": "Wczytywanie jakości obserwacji…",
    "Fire detected": "Wykryto pożar",
    "Recently burned, not seen now": "Niedawno spalone, teraz niewidoczne",
    "No fire detected (well observed)": "Nie wykryto pożaru (dobrze obserwowane)",
    "Insufficient observation (unknown)": "Niewystarczająca obserwacja (nieznane)",
    "Cell counts": "Liczby komórek",
    "The view is larger than 10 x 10 degrees, so the central 10 x 10 degree area was loaded. Zoom in to see the rest.": "Widok jest większy niż 10 x 10 stopni, więc wczytano centralny obszar 10 x 10 stopni. Przybliż, aby zobaczyć resztę.",
    "No fire detected (well observed).": "Nie wykryto pożaru (dobrze obserwowane).",
    "Insufficient observation (unknown, not \"no fire\").": "Niewystarczająca obserwacja (nieznane, a nie „brak pożaru”).",
    "Recently burned, not seen now.": "Niedawno spalone, teraz niewidoczne.",
    "Fire detected.": "Wykryto pożar.",
    "Tile details": "Szczegóły kafelka",
    "How this is computed": "Jak to jest liczone",
    "Overpass geometry is from orbital elements; cloud is an Open-Meteo model value. Experimental and uncalibrated.": "Geometria przelotów pochodzi z elementów orbitalnych; zachmurzenie to wartość modelowa z Open-Meteo. Eksperymentalne i nieskalibrowane.",
    "Insufficient observation": "Niewystarczająca obserwacja", "Good observation": "Dobra obserwacja",
    "No detection here means unknown, not \"no fire\".": "Brak detekcji tutaj oznacza „nieznane”, a nie „brak pożaru”.",
    "A usable overpass saw this tile recently. No detection is the closest the data gets to \"probably no active fire\", not a guarantee.": "Użyteczny przelot niedawno widział ten kafelek. Brak detekcji to najbliższe, co dają dane, stwierdzeniu „prawdopodobnie brak aktywnego pożaru”, a nie gwarancja.",
    "This point is outside the loaded observation tiles.": "Ten punkt leży poza wczytanymi kafelkami obserwacji.",
    "No fire cell here: no detection in this 0.05 degree cell during the window.": "Brak komórki z pożarem: w tej komórce 0,05 stopnia nie było detekcji w oknie czasowym.",
    "Outside loaded area": "Poza wczytanym obszarem",
    "Details in the panel under Blind spots.": "Szczegóły w panelu w sekcji Ślepe strefy.",
    /* scenarios */
    "Weather scenarios": "Scenariusze pogodowe",
    "Hypothetical weather — not the issued forecast": "Pogoda hipotetyczna — to nie jest wydana prognoza",
    "Compares the baseline with fixed edits to wind, rain and dryness to show how sensitive the simulated spread is. These are not probabilities or confidence intervals.":
      "Porównuje wariant bazowy ze stałymi zmianami wiatru, deszczu i suchości powietrza, aby pokazać, jak wrażliwe jest symulowane rozprzestrzenianie. To nie są prawdopodobieństwa ani przedziały ufności.",
    "Compare weather scenarios": "Porównaj scenariusze pogodowe",
    "Baseline (issued weather)": "Bazowy (wydana pogoda)",
    "Scenarios need live ignition. Set Ignition to Nearest live fire.": "Scenariusze wymagają zapłonu z danych na żywo. Ustaw „Źródło zapłonu” na „Najbliższy aktywny pożar”.",
    "Running weather scenarios…": "Uruchamianie scenariuszy pogodowych…",
    "Cancelled. No scenario results were kept.": "Anulowano. Nie zachowano wyników scenariuszy.",
    "hours from start": "godziny od startu",
    "burned area (km²)": "spalona powierzchnia (km²)",
    "The baseline simulation shows no spread beyond the initial footprint.": "Symulacja bazowa nie pokazuje rozprzestrzenienia poza początkowy zasięg.",
    "Baseline shows no spread beyond the initial footprint.": "Wariant bazowy nie pokazuje rozprzestrzenienia poza początkowy zasięg.",
    "Scenario (map)": "Scenariusz (mapa)", "Burned km²": "Spalone km²", "Growth km²": "Przyrost km²", "Extra vs baseline": "Dodatkowo względem bazowego",
    "Growth is burned area minus the initial footprint. Extra is shown only when the baseline grows. Ranges between scenarios show sensitivity to the weather edit, not confidence.":
      "Przyrost to spalona powierzchnia minus początkowy zasięg. „Dodatkowo” pokazywane jest tylko wtedy, gdy wariant bazowy rośnie. Zakresy między scenariuszami pokazują wrażliwość na zmianę pogody, a nie pewność.",
    "Burned area over time": "Spalona powierzchnia w czasie",
    "Lines overlap when a weather edit does not change the simulated spread.": "Linie nakładają się, gdy zmiana pogody nie zmienia symulowanego rozprzestrzeniania.",
    "Reach by compass direction": "Zasięg według kierunku świata",
    "No scenario spread beyond the initial footprint in any direction, so there is no directional reach to show.": "Żaden scenariusz nie rozprzestrzenia się poza początkowy zasięg w żadnym kierunku, więc nie ma zasięgu kierunkowego do pokazania.",
    "Scenario in the rose": "Scenariusz na róży",
    "Filled wedges: farthest new burn beyond the initial footprint in that sector for the chosen scenario. Dashed grey outline: baseline.": "Wypełnione kliny: najdalsze nowe spalenie poza początkowym zasięgiem w danym sektorze dla wybranego scenariusza. Szary kontur przerywany: wariant bazowy.",
    "Notes": "Uwagi",
    "Zoom to perimeters": "Przybliż do obrysów",
    "No change in reach vs baseline.": "Brak zmiany zasięgu względem bazowego.",
    /* exposure */
    "Exposure over time": "Ekspozycja w czasie",
    "ESTIMATED EXPOSURE: not damage, not loss": "SZACOWANA EKSPOZYCJA: to nie szkody ani straty",
    "Counts assets (hospitals, schools, roads, settlements and more, from OpenStreetMap) inside the simulated burned area as the timeline advances. It needs a finished ML + physics run in the player.":
      "Zlicza obiekty (szpitale, szkoły, drogi, miejscowości i inne, z OpenStreetMap) wewnątrz symulowanego obszaru spalenia w miarę postępu osi czasu. Wymaga zakończonego przebiegu ML + fizyka w odtwarzaczu.",
    "Estimate exposure for this run": "Oszacuj ekspozycję dla tego przebiegu",
    "Run a prediction first.": "Najpierw uruchom prognozę.",
    "Ready. The first request for an area can take about two minutes.": "Gotowe. Pierwsze żądanie dla obszaru może potrwać około dwóch minut.",
    "The simulation changed. Estimate exposure again for the new run.": "Symulacja się zmieniła. Oszacuj ekspozycję ponownie dla nowego przebiegu.",
    "Estimating exposure…": "Szacowanie ekspozycji…",
    "Looks up OpenStreetMap assets with Overpass. The first request for an area can take 70 to 120 seconds; later requests are cached. You can keep using the map.":
      "Wyszukuje obiekty OpenStreetMap przez Overpass. Pierwsze żądanie dla obszaru może potrwać od 70 do 120 sekund; kolejne są buforowane. Możesz dalej korzystać z mapy.",
    "Cancelled. The server may still finish the request and cache it.": "Anulowano. Serwer może nadal dokończyć żądanie i zapisać je w pamięci podręcznej.",
    "Ignition (ESTIMATE): satellite-based, location approximate": "Zapłon (SZACUNEK): na podstawie satelity, lokalizacja przybliżona",
    "Hospitals": "Szpitale", "Schools": "Szkoły", "Fire stations": "Remizy strażackie", "Power sites": "Obiekty energetyczne",
    "Settlements": "Miejscowości", "Residential areas": "Obszary mieszkalne", "Roads (km)": "Drogi (km)", "Railways (km)": "Koleje (km)",
    "Power lines (km)": "Linie energetyczne (km)", "Buildings (OSM)": "Budynki (OSM)",
    "Partial coverage.": "Pokrycie częściowe.",
    "Estimated exposure": "Szacowana ekspozycja",
    "Show assets on the map": "Pokaż obiekty na mapie",
    "Asset legend": "Legenda obiektów",
    "Bright symbols are reached by the simulated footprint at the current timeline step; faded ones are not yet reached. Cells of buildings are sized by the square root of the count.":
      "Jasne symbole są osiągnięte przez symulowany zasięg w bieżącym kroku osi czasu; wyblakłe nie zostały jeszcze osiągnięte. Komórki budynków mają rozmiar proporcjonalny do pierwiastka z liczby.",
    "Estimated arrival times": "Szacowane czasy dotarcia ognia",
    "Industrial heat sources such as steelworks, flares and power plants can appear as satellite fire detections. If a detection sits on an industrial site, confirm it before relying on the exposure estimate.":
      "Przemysłowe źródła ciepła, takie jak huty, pochodnie i elektrownie, mogą pojawiać się jako satelitarne detekcje pożarów. Jeśli detekcja leży na terenie przemysłowym, potwierdź ją, zanim zaufasz szacunkowi ekspozycji.",
    "Limits and sources": "Ograniczenia i źródła",
    "This tool does not estimate monetary or insured loss.": "To narzędzie nie szacuje strat pieniężnych ani ubezpieczeniowych.",
    "ESTIMATED EXPOSURE: an asset inside the simulated burned footprint. Not damage, not loss.": "SZACOWANA EKSPOZYCJA: obiekt wewnątrz symulowanego zasięgu spalenia. To nie szkoda ani strata.",
    "ESTIMATED POPULATION EXPOSURE (separate from the asset counts)": "SZACOWANA EKSPOZYCJA LUDNOŚCI (oddzielnie od liczby obiektów)",
    "ESTIMATED POPULATION EXPOSURE (separate from any loss figure)": "SZACOWANA EKSPOZYCJA LUDNOŚCI (oddzielnie od jakichkolwiek kwot strat)",
    "No building-based proxy either (building data unavailable).": "Brak również przybliżenia opartego na budynkach (brak danych o budynkach).",
    "No mapped assets are reached in this run (OpenStreetMap may be incomplete here).": "W tym przebiegu żaden zmapowany obiekt nie zostaje osiągnięty (OpenStreetMap może być tu niekompletny).",
    "Static estimates with no time-of-day model. Not a loss figure.": "Szacunki statyczne, bez modelu pory dnia. To nie jest kwota strat.",
    "People inside the simulated footprint, not casualties or evacuees.": "Osoby wewnątrz symulowanego zasięgu, a nie poszkodowani ani ewakuowani.",
    "Show fewer": "Pokaż mniej",
    "reached": "osiągnięte",
    "n/a": "brak danych",
    "unnamed": "bez nazwy",
    "Show more": "Pokaż więcej",
    "Estimated exposure ": "Szacowana ekspozycja",
    "Estimated location ": "Szacowana lokalizacja",
  });
  p("{#n} s", "{#n} s");
  p("Simulated until the next usable pass ({t})", "Symulowano do następnego użytecznego przelotu ({t})");
  p("Teal bar: window from the simulation start to the next usable pass ({s}, {t}). Frames after the bar are outside that window.", "Niebieskozielony pasek: okno od startu symulacji do następnego użytecznego przelotu ({s}, {t}). Klatki za paskiem leżą poza tym oknem.");
  p("{h} since last detection", "{h|t} od ostatniej detekcji");
  p("now {t}", "teraz {t}");
  p("until {t}", "do {t}");
  p("Last detection {h} ago, {s}", "Ostatnia detekcja {h|t} temu, {s}");
  p("Last detection {#n} h ago, {s}", "Ostatnia detekcja {#n} godz. temu, {s}");
  p("No detection within {#k} km in the last 7 days", "Brak detekcji w promieniu {#k} km w ostatnich 7 dniach");
  p("Latest usable pass without a detection yet: {s} at {t}. Its detections may still be pending in the feed.", "Ostatni użyteczny przelot bez detekcji: {s} o {t}. Jego detekcje mogą jeszcze oczekiwać w kanale.");
  p("Next usable pass: {s} ({i})", "Następny użyteczny przelot: {s} ({i})");
  p("at {t}, in {h}.", "o {t}, za {h|t}.");
  p("{q} geometry (scan {#a} deg), {px} km pixels, {c} sky in the model.", "geometria {q} (skanowanie {#a} stopni), piksele {px} km, niebo: {c} w modelu.");
  p("An earlier pass ({s}, {t}) is not counted as usable: {w}.", "Wcześniejszy przelot ({s}, {t}) nie jest liczony jako użyteczny: {w}.");
  p("Orbital elements are out of date for {s}. Pass times for those satellites may be less accurate.", "Elementy orbitalne są nieaktualne dla: {s}. Czasy przelotów tych satelitów mogą być mniej dokładne.");
  p("Detection feed delayed or unavailable: {s}. Observation age may be understated.", "Kanał detekcji opóźniony lub niedostępny: {s}. Wiek obserwacji może być zaniżony.");
  p("Detection feed delayed or unavailable: {s}. Fire cells may be incomplete.", "Kanał detekcji opóźniony lub niedostępny: {s}. Komórki z pożarem mogą być niekompletne.");
  p("Next-pass lookup failed: {e}", "Wyszukiwanie następnego przelotu nie powiodło się: {e}");
  p("The window is {#h} h; the combined service is limited to 72 h, so the run is capped at 72 h.", "Okno wynosi {#h} godz.; usługa łączona jest ograniczona do 72 godz., więc przebieg jest ucięty do 72 godz.");
  p("{b} The run starts at the top of the current hour and covers {#h} h, which is at least the time to that pass.", "{b} Przebieg zaczyna się od początku bieżącej godziny i obejmuje {#h} godz., czyli co najmniej czas do tego przelotu.");
  p("Simulate until next usable pass ({#h} h)", "Symuluj do następnego użytecznego przelotu ({#h} godz.)");
  p("Simulate until next usable pass", "Symuluj do następnego użytecznego przelotu");
  p("Current player run ({#h} h)", "Bieżący przebieg w odtwarzaczu ({#h} godz.)");
  p("Until next usable pass ({#h} h)", "Do następnego użytecznego przelotu ({#h} godz.)");
  p("Same as the player ({#h} h)", "Tak jak w odtwarzaczu ({#h} godz.)");
  p("{#h} hours", "{#h} {h|pl:godzina,godziny,godzin}");
  p("{l} ({#n})", "{l|t} ({#n})");
  p("Point {#a}, {#b}, start {s}, {#h} h.", "Punkt {#a}, {#b}, start {s}, {#h} godz.");
  p("{#h} h, calibration {c}. The first call for a new area downloads weather and landscape inputs and can take from a few seconds to about three minutes.", "{#h} godz., kalibracja: {c|t}. Pierwsze wywołanie dla nowego obszaru pobiera dane pogodowe i krajobrazowe i może potrwać od kilku sekund do około trzech minut.");
  p("Scenario comparison failed: {e}", "Porównanie scenariuszy nie powiodło się: {e}");
  p("Wind × {#x}", "Wiatr × {#x}");
  p("Wind rotated {x}°", "Wiatr obrócony o {x}°");
  p("Rain {#x} mm", "Deszcz {#x} mm");
  p("Drier air (RH −{#x} points)", "Suchsze powietrze (wilg. wzgl. −{#x} pkt)");
  p("Wetter air (RH +{#x} points)", "Wilgotniejsze powietrze (wilg. wzgl. +{#x} pkt)");
  p("outer ring {#k} km", "zewnętrzny pierścień {#k} km");
  p("{#h} h from {s}. Calibration {c}. Weather: {w}.", "{#h} godz. od {s}. Kalibracja: {c|t}. Pogoda: {w}.");
  p("off", "wyłączona"); p("applied", "zastosowana"); p("gridded", "siatkowa");
  p("Show {l} perimeter on the map", "Pokaż obrys: {l|t} na mapie");
  p("{l}: {#a} km² at +{#h} h", "{l|t}: {#a} km² w +{#h} godz.");
  p("Change in reach vs baseline: {r}.", "Zmiana zasięgu względem bazowego: {r}.");
  p("Centre of the burn shifts {#k} km{t}.", "Środek spalenia przesuwa się o {#k} km{t}.");
  p("Centre of the burn shifts {#k} km toward {d}.", "Środek spalenia przesuwa się o {#k} km w kierunku {d}.");
  p("Weather: mean wind {#v} km/h from {#d} deg, RH {#r}%, rain {#m} mm.", "Pogoda: średni wiatr {#v} km/h z kierunku {#d} stopni, wilg. wzgl. {#r}%, deszcz {#m} mm.");
  p("Run time {#s} s.", "Czas obliczeń {#s} s.");
  p("Run time {#s} s{x}.", "Czas obliczeń {#s} s{x}.");
  p("Exposure estimate failed: {e}", "Oszacowanie ekspozycji nie powiodło się: {e}");
  p("About {#n} buildings in a ~500 m cell (OSM, estimated exposure)", "Około {#n} budynków w komórce ~500 m (OSM, szacowana ekspozycja)");
  p("{a} (unnamed)", "{a|t} (bez nazwy)");
  p("{#k} km exposed", "{#k} km w zasięgu");
  p("{#n} areas", "{#n} {n|pl:obszar,obszary,obszarów}");
  p("scenario only: {s}", "tylko w scenariuszu: {s}");
  p("Estimated exposure at T+{#h} h · {t} (not damage, not loss)", "Szacowana ekspozycja w T+{#h} godz. · {t} (to nie szkody ani straty)");
  p("Estimated exposure at T+{#h} h (not damage, not loss)", "Szacowana ekspozycja w T+{#h} godz. (to nie szkody ani straty)");
  p("of {#n} mapped", "z {#n} zmapowanych");
  p("{a}–{b} at end across weather scenarios", "{a}–{b} na końcu w scenariuszach pogodowych");
  p("people inside the simulated footprint at this step, {s} ({v}).", "osób wewnątrz symulowanego zasięgu w tym kroku, {s} ({v}).");
  p("Gridded population (HRSL) unavailable here: {r}.", "Dane siatkowe o ludności (HRSL) niedostępne w tym miejscu: {r}.");
  p("The asset search area was capped at {#k} km, so assets farther out are not counted.", "Obszar wyszukiwania obiektów ograniczono do {#k} km, więc obiekty dalej nie są liczone.");
  p("Estimated exposure: {l}", "Szacowana ekspozycja: {l|t}");
  p("Counts are 0.05 degree (about 5 km) cells in the loaded area, window {#h} h, as of {t}.", "Liczby dotyczą komórek 0,05 stopnia (około 5 km) w wczytanym obszarze, okno {#h} godz., stan na {t}.");
  p("{#n} of the well-observed quiet cells burned earlier in the last {#h} h.", "{#n} dobrze obserwowanych cichych komórek paliło się wcześniej w ostatnich {#h} godz.");
  p("Tiles with insufficient observation, {#n} of {#m}:", "Kafelki z niewystarczającą obserwacją, {#n} z {#m}:");
  p("Click the map for the reasons behind a tile, or use these buttons. Tiles with insufficient observation, {#n} of {#m}:", "Kliknij mapę, aby poznać przyczyny dla kafelka, lub użyj tych przycisków. Kafelki z niewystarczającą obserwacją, {#n} z {#m}:");
  p("Blind-spot data unavailable: {e}", "Dane o ślepych strefach niedostępne: {e}");
  p("Area {b} (west, south, east, north).", "Obszar {b} (zachód, południe, wschód, północ).");
  p("{#a} of {#b} passes in the window were usable{r}", "{#a} z {#b} przelotów w oknie było użytecznych{r}");
  p("Passes considered ({#n})", "Uwzględnione przeloty ({#n})");
  p("{l}, 1 degree tile", "{l|t}, kafelek 1 stopnia");
  p("{l}, 0.05 degree cell", "{l|t}, komórka 0,05 stopnia");
  p("{#n} detections, {#a} MW, last {h} ago.", "{#n} {n|pl:detekcja,detekcje,detekcji}, {#a} MW, ostatnia {h|t} temu.");
  p("{#n} detection, {#a} MW, last {h} ago.", "{#n} detekcja, {#a} MW, ostatnia {h|t} temu.");
  p("Burned earlier ({#n} detections, last {h} ago) and quiet in a well-observed window.", "Paliło się wcześniej ({#n} {n|pl:detekcja,detekcje,detekcji}, ostatnia {h|t} temu), a w dobrze obserwowanym oknie jest cicho.");
  p("Burned earlier ({#n} detections, last {h} ago), poorly observed since: it may still be burning unseen.", "Paliło się wcześniej ({#n} {n|pl:detekcja,detekcje,detekcji}, ostatnia {h|t} temu), od tego czasu słabo obserwowane: może nadal płonąć niezauważone.");
  p("Burned earlier ({#n} detection, last {h} ago), poorly observed since: it may still be burning unseen.", "Paliło się wcześniej ({#n} detekcja, ostatnia {h|t} temu), od tego czasu słabo obserwowane: może nadal płonąć niezauważone.");
  p("{a} (OSM, estimated exposure)", "{a|t} (OSM, szacowana ekspozycja)");
  p("{#n} people inside the simulated footprint", "{#n} osób wewnątrz symulowanego zasięgu");
  p("{#n} h old", "{#n} h temu");

  /* ================================================================ texts sent by the backend (shown verbatim by the UI) */
  e({
    /* replay incidents (data/incidents.json) */
    "Flin Flon, Manitoba, Canada (boreal forest)": "Flin Flon, Manitoba, Kanada (las borealny)",
    "South Kalimantan, Indonesia (peatland, haze, ML-only)": "Borneo Południowe (Kalimantan), Indonezja (torfowiska, zmętnienie dymem, tylko ML)",
    "Pantanal wetland-savanna, Mato Grosso do Sul, Brazil/Bolivia border": "Pantanal, mokradła i sawanna, Mato Grosso do Sul, pogranicze Brazylii i Boliwii",
    "Punjab, India (agricultural stubble burning)": "Pendżab, Indie (wypalanie rżysk rolniczych)",
    "Roraima, northern Brazil (savanna / forest edge, cloudy)": "Roraima, północna Brazylia (sawanna / skraj lasu, zachmurzenie)",
    "Smokehouse Creek area, Texas Panhandle, USA (prairie grass)": "Okolice Smokehouse Creek, Texas Panhandle, USA (trawy preriowe)",
    "boreal forest": "las borealny",
    "peatland / tropical, smoke haze": "torfowiska / tropiki, zadymienie",
    "savanna / seasonally flooded grassland": "sawanna / sezonowo zalewane łąki",
    "agricultural / mixed": "rolnicze / mieszane",
    "savanna and forest edge, tropical cloud": "sawanna i skraj lasu, tropikalne zachmurzenie",
    "grassland / prairie": "łąki / prerie",
    "Late-May 2025 boreal wildfire complex on the Manitoba/Saskatchewan border that forced evacuations around Flin Flon. This is also the app's default demo event.":
      "Kompleks pożarów lasów borealnych z końca maja 2025 na granicy Manitoby i Saskatchewan, który zmusił do ewakuacji w okolicach Flin Flon. To także domyślne zdarzenie demonstracyjne aplikacji.",
    "Boreal crown-fire regime with persistent smoke and variable cloud; the project's primary demo, so judges can compare the replay with the live forecast view.":
      "Reżim pożarów koron drzew w lesie borealnym z uporczywym dymem i zmiennym zachmurzeniem; główne demo projektu, aby jury mogło porównać odtworzenie z widokiem prognozy na żywo.",
    "October 2023 El Nino-driven peat and scrub fires in Kalimantan under thick haze.": "Pożary torfowisk i zarośli w Kalimantanie w październiku 2023, spowodowane przez El Niño, pod gęstym zadymieniem.",
    "Pre-2024, so no ML+physics blender exists: demonstrates the ML-only replay path. Also the most cloud-obscured case (proxy marks about 57-79% of the area as having no clear overpass on days 1-2), so false alarms on those days are largely unverifiable.":
      "Sprzed 2024, więc nie istnieje blender ML + fizyka: pokazuje ścieżkę odtworzenia tylko z ML. To także przypadek najbardziej zasłonięty chmurami (przybliżenie oznacza około 57-79% obszaru jako pozbawionego czystego przelotu w dniach 1-2), więc fałszywe alarmy w tych dniach są w dużej mierze nieweryfikowalne.",
    "August 2024 dry-season fire activity in the Pantanal, part of the record 2024 South American fire season.": "Aktywność pożarowa w porze suchej w sierpniu 2024 w Pantanalu, część rekordowego sezonu pożarowego 2024 w Ameryce Południowej.",
    "Savanna/grassland dry-season spread with a long-lived multi-day fire front, in a different hemisphere and fuel type from the Canadian cases.":
      "Rozprzestrzenianie w porze suchej na sawannie i łąkach z długotrwałym, wielodniowym frontem ognia, na innej półkuli i w innym typie paliwa niż przypadki kanadyjskie.",
    "Post-harvest paddy-stubble burning season around the Punjab plains: hundreds of small, scattered, short-lived fires rather than one expanding front.":
      "Sezon wypalania rżysk ryżowych po żniwach na równinach Pendżabu: setki małych, rozproszonych, krótkotrwałych pożarów zamiast jednego rozszerzającego się frontu.",
    "Mixed/agricultural case where 'spread' is mostly new independent ignitions, a stress test for a spread model; heavy smoke and haze season.":
      "Przypadek mieszany/rolniczy, w którym „rozprzestrzenianie” to głównie nowe, niezależne zapłony, test wytrzymałości dla modelu rozprzestrzeniania; sezon silnego dymu i zadymienia.",
    "March 2024 fire season in Roraima during the El Nino drought, when fires burned around Boa Vista and along the savanna/forest border.":
      "Sezon pożarowy w Roraimie w marcu 2024 podczas suszy związanej z El Niño, gdy pożary płonęły wokół Boa Vista i wzdłuż granicy sawanny i lasu.",
    "Observation-limited case: the ERA5 cloud proxy marks a quarter to two-fifths of the area as having no clear overpass on days 1-2, so some 'false alarms' may simply be fire we could not see. The replay reports those separately as unverifiable.":
      "Przypadek ograniczony obserwacją: przybliżenie zachmurzenia z ERA5 oznacza od jednej czwartej do dwóch piątych obszaru jako pozbawione czystego przelotu w dniach 1-2, więc część „fałszywych alarmów” może być po prostu pożarem, którego nie mogliśmy zobaczyć. Odtworzenie raportuje je osobno jako nieweryfikowalne.",
    "Wind-driven grass fires that started on 26 Feb 2024 and spread very fast the next day under dry, windy conditions.":
      "Pożary traw napędzane wiatrem, które zaczęły się 26 lutego 2024 i bardzo szybko rozprzestrzeniły się następnego dnia w suchych, wietrznych warunkach.",
    "Grass fire with an explosive second day: tests whether the system anticipates fast, wind-driven growth when the init-day footprint is small (19 burning cells at init).":
      "Pożar traw z gwałtownym drugim dniem: sprawdza, czy system przewiduje szybki wzrost napędzany wiatrem, gdy początkowy zasięg jest mały (19 płonących komórek na starcie).",
    /* replay method, audit, limitations (backend/replay.py) */
    "forecast uses fire detections through the end of this UTC day and nothing later": "prognoza używa detekcji pożarów do końca tego dnia UTC i niczego późniejszego",
    "XGBoost direct multi-horizon ML (equifire.direct) blended with Rothermel/minimum-travel-time physics (backend.ensemble.predict); 'ml' rows score the same ML component alone.":
      "Bezpośredni wielohoryzontowy ML XGBoost (equifire.direct) połączony z fizyką Rothermela / minimalnego czasu przejścia (backend.ensemble.predict); wiersze „ml” oceniają ten sam składnik ML osobno.",
    "XGBoost direct multi-horizon ML only (equifire.direct, walk-forward model wf_<year>).": "Tylko bezpośredni wielohoryzontowy ML XGBoost (equifire.direct, model walk-forward wf_<rok>).",
    "Physics uses ERA5 reanalysis weather for the forecast period (perfect-weather replay): physics skill is optimistic vs a real-time forecast that only has weather forecasts. The ML component uses only day-of-init weather.":
      "Fizyka używa pogody z reanalizy ERA5 dla okresu prognozy (odtworzenie z idealną pogodą): skuteczność fizyki jest optymistyczna w porównaniu z prognozą w czasie rzeczywistym, która ma tylko prognozy pogody. Składnik ML używa wyłącznie pogody z dnia startu.",
    "Fire observations after the end of the init UTC day are removed from the forecaster's input (as-of index); forecast models were trained on years before the incident year (walk-forward folds).":
      "Obserwacje pożarów po końcu dnia UTC startu są usuwane z wejścia prognozy (indeks as-of); modele prognostyczne trenowano na latach sprzed roku zdarzenia (foldy walk-forward).",
    "'Truth' is satellite detection (VIIRS 375 m, 0.05 deg cells), not burned perimeter: absence of detection is not proof of no fire (clouds, smoke, overpass gaps, fire below detection threshold).":
      "„Prawda” to detekcja satelitarna (VIIRS 375 m, komórki 0,05°), a nie obrys spalonego obszaru: brak detekcji nie dowodzi braku pożaru (chmury, dym, luki w przelotach, pożar poniżej progu detekcji).",
    "The cloud/overpass caveat is a proxy from ERA5 modelled cloud cover at nominal local overpass times; actual overpass schedules and swath coverage are not reconstructed. Unverifiable cells are reported separately.":
      "Zastrzeżenie dot. chmur i przelotów jest przybliżeniem z modelowanego zachmurzenia ERA5 w nominalnych lokalnych godzinach przelotów; rzeczywiste harmonogramy przelotów i pokrycie pasa nie są odtwarzane. Komórki nieweryfikowalne raportowane są osobno.",
    "Scores come from single events, not a benchmark: the 100-fire/year walk-forward in forecast.py is the statistically meaningful measure. Curated incidents were chosen for diversity, so they are illustrative.":
      "Wyniki pochodzą z pojedynczych zdarzeń, a nie z benchmarku: istotną statystycznie miarą jest walk-forward na 100 pożarach rocznie w forecast.py. Wybrane zdarzenia dobrano pod kątem różnorodności, więc mają charakter ilustracyjny.",
    "Probabilities are uncalibrated and the physics burned fraction is not an ignition probability.": "Prawdopodobieństwa są nieskalibrowane, a frakcja spalenia z fizyki nie jest prawdopodobieństwem zapłonu.",
    "2023 and earlier incidents can only be replayed ML-only (no ML+physics blender exists for them).": "Zdarzenia z 2023 i wcześniejsze można odtworzyć tylko samym ML (dla nich nie istnieje blender ML + fizyka).",
    "physics uses ERA5 reanalysis for the forecast period (perfect-weather); ML uses init-day weather only": "fizyka używa reanalizy ERA5 dla okresu prognozy (idealna pogoda); ML używa wyłącznie pogody z dnia startu",
    "ML uses init-day weather only; no physics in this variant": "ML używa wyłącznie pogody z dnia startu; w tym wariancie brak fizyki",
    /* ensemble warnings */
    "Experimental blend: measured gains over ML alone are not established.": "Połączenie eksperymentalne: zmierzone korzyści względem samego ML nie zostały potwierdzone.",
    "Physics burned fraction is not an ignition probability; low/high are scenario envelopes.": "Frakcja spalenia z fizyki nie jest prawdopodobieństwem zapłonu; wartości niskie/wysokie to obwiednie scenariuszy.",
    "The hourly landscape simulation is a separate richer model, not the physics used to fit this blender.": "Godzinowa symulacja krajobrazowa to osobny, bogatszy model, a nie fizyka użyta do dopasowania tego blendera.",
    "Nothing detected near this point yet on the selected UTC day: the forecast starts from the latest detections of the previous UTC day, so it is up to 24 h behind the real fire.":
      "W wybranym dniu UTC nie wykryto jeszcze niczego w pobliżu tego punktu: prognoza startuje z najnowszych detekcji z poprzedniego dnia UTC, więc może być opóźniona nawet o 24 godz. względem prawdziwego pożaru.",
    "No VIIRS fire near this point: the ignition comes from MODIS (1 km pixels) used as VIIRS-equivalent. MODIS finds only about a quarter of the cells VIIRS would, so the starting footprint is probably too small. Estimate.":
      "Brak pożaru VIIRS w pobliżu tego punktu: zapłon pochodzi z MODIS (piksele 1 km) użytego jako ekwiwalent VIIRS. MODIS znajduje tylko około jednej czwartej komórek, które znalazłby VIIRS, więc zasięg początkowy jest prawdopodobnie zbyt mały. Szacunek.",
    "Reduced history: seven-day public feed; full 30/365-day history, sub-cell offsets and day/night flags are unavailable. This live mode has not been validated.":
      "Ograniczona historia: siedmiodniowy kanał publiczny; pełna historia 30/365 dni, przesunięcia podkomórkowe i flagi dzień/noc są niedostępne. Ten tryb na żywo nie został zwalidowany.",
    "Physics uses observed archive weather: historical replay, not a forecast-weather backtest.": "Fizyka używa zaobserwowanej pogody archiwalnej: to odtworzenie historyczne, a nie test wsteczny na prognozowanej pogodzie.",
    "No VIIRS or MODIS ignition detected near this point in the last 48 h": "Nie wykryto zapłonu VIIRS ani MODIS w pobliżu tego punktu w ciągu ostatnich 48 godz.",
    "No VIIRS ignition detected near this point on the selected UTC day": "Nie wykryto zapłonu VIIRS w pobliżu tego punktu w wybranym dniu UTC",
    "Insufficient detected ignition for this model": "Niewystarczający wykryty zapłon dla tego modelu",
    /* harmonized API */
    "Amazon arc of deforestation (Brazil, 14-5 S, 62-45 W)": "Łuk wylesiania Amazonii (Brazylia, 14-5 S, 62-45 W)",
    "Northern Australia savanna (19-11 S, 122-142 E)": "Sawanna północnej Australii (19-11 S, 122-142 E)",
    "Canadian boreal (50-60 N, 125-90 W)": "Kanadyjski las borealny (50-60 N, 125-90 W)",
    "Punjab-Haryana plains, Indian side (29-32.5 N, 73.8-77.5 E)": "Równiny Pendżabu i Hariany, strona indyjska (29-32,5 N, 73,8-77,5 E)",
    "MODIS raw (Terra + Aqua, 1 km)": "MODIS surowe (Terra + Aqua, 1 km)",
    "VIIRS S-NPP raw (375 m)": "VIIRS S-NPP surowe (375 m)",
    "VIIRS NOAA-20 raw (375 m)": "VIIRS NOAA-20 surowe (375 m)",
    "Raw splice (MODIS to 2011, VIIRS from 2012)": "Surowe zszycie (MODIS do 2011, VIIRS od 2012)",
    "MODIS reconstructed to VIIRS-equivalent (all years)": "MODIS zrekonstruowany do ekwiwalentu VIIRS (wszystkie lata)",
    "Harmonized (reconstructed before 2012-01-20, real S-NPP after)": "Zharmonizowane (zrekonstruowane przed 2012-01-20, prawdziwe S-NPP po)",
    "Harmonized + overlap calibration (reconstruction divided by its 2012-2020 ratio to real S-NPP)": "Zharmonizowane + kalibracja na nakładaniu (rekonstrukcja podzielona przez jej stosunek do prawdziwego S-NPP z lat 2012-2020)",
    "Harmonized": "Zharmonizowane", "Harmonized + overlap calibration": "Zharmonizowane + kalibracja na nakładaniu",
    "MODIS raw": "MODIS surowe", "VIIRS S-NPP raw": "VIIRS S-NPP surowe", "VIIRS NOAA-20 raw": "VIIRS NOAA-20 surowe",
    "VIIRS S-NPP starts (375 m)": "Start VIIRS S-NPP (375 m)", "Aqua starts (Terra only before)": "Start Aqua (wcześniej tylko Terra)",
    "harmonized = MODIS reconstructed to VIIRS S-NPP equivalent before 2012-01-20 + real S-NPP after; NOAA-20 is not added.":
      "zharmonizowane = MODIS zrekonstruowany do ekwiwalentu VIIRS S-NPP przed 2012-01-20 + prawdziwe S-NPP po tej dacie; NOAA-20 nie jest dodawany.",
    "Reconstruction recall is limited (about 35% of VIIRS-active cell-days are recovered, predicted/true cell-days about 0.7-1.2 depending on region); totals of detections and FRP are matched better than cell-day counts. See bridge for per-year ratios.":
      "Czułość rekonstrukcji jest ograniczona (odtwarzane jest około 35% komórko-dni aktywnych w VIIRS, stosunek przewidywanych do prawdziwych komórko-dni wynosi około 0,7-1,2 zależnie od regionu); sumy detekcji i FRP są odtwarzane lepiej niż liczby komórko-dni. Zob. „bridge” dla stosunków rok po roku.",
    "Only 5 countries (Brazil, Australia, India, United States, Canada) have a full 2000-2025 archive here.": "Tylko 5 krajów (Brazylia, Australia, Indie, Stany Zjednoczone, Kanada) ma tu pełne archiwum z lat 2000-2025.",
    "no significant trend, with or without a 2012 step": "brak istotnego trendu, z krokiem 2012 lub bez niego",
    "not significant": "nieistotny", "significant": "istotny",
    "ROUTINE": "RUTYNOWE", "ANOMALY": "ANOMALIA", "LOW": "NISKI",
    "within normal range": "w normalnym zakresie", "above normal": "powyżej normy", "unusually low": "nietypowo niski",
    "anomaly: above normal": "anomalia: powyżej normy", "routine: within normal range": "rutynowe: w normalnym zakresie",
    "no fire activity": "brak aktywności pożarowej", "not enough years for a baseline": "za mało lat do wyznaczenia normy",
    "z-score of the year total": "wynik z (z-score) sumy rocznej",
    "of the burning cell-days VIIRS saw and": "komórko-dni z pożarem widzianych przez VIIRS oraz",
    "of its detections (medians).": "jego detekcji (mediany).",
    ", harmonized FRP": ", zharmonizowane FRP",
    ", MODIS-only cell-days": ", komórko-dni tylko MODIS",
    ". Part of any step is real year-to-year change.": ". Część każdego skoku to rzeczywista zmiana rok do roku.",
    "· Esri imagery only when selected in Layers": "· zdjęcia Esri tylko po wybraniu w Warstwach",
    "contributors": "współtwórcy",
    "harmonized FRP": "zharmonizowane FRP",
    "Terra-only": "tylko Terra",
  });
  p("In this area, in {#n} overlap years ({#y}+), MODIS counted", "Na tym obszarze, w {#n} {n|pl:roku,latach,latach} nakładania ({#y}+), MODIS policzył");
  p("Same-area step across 2012 (average 2013-15 over 2009-11): any-sensor cell-days", "Skok na tym samym obszarze w poprzek 2012 (średnia 2013-15 względem 2009-11): komórko-dni dowolnego sensora");
  p("{#y} {l}: {#v}", "{#y} {l|t}: {#v}");
  p("{#y} {l}: {#v} (partial year)", "{#y} {l|t}: {#v} (rok niepełny)");
  p("Apparent trend {a} is largely an artifact of the 2012 step ({b}); with the step modelled it is {c} (not significant)", "Pozorny trend {a} jest w dużej mierze artefaktem skoku z 2012 ({b}); po uwzględnieniu skoku w modelu wynosi {c} (nieistotny)");
  p("Apparent trend {a} is largely an artifact of the 2012 step ({b}); with the step modelled it is {c}", "Pozorny trend {a} jest w dużej mierze artefaktem skoku z 2012 ({b}); po uwzględnieniu skoku w modelu wynosi {c}");
  p("apparent trend {a} is largely an artifact of the 2012 step ({b}); with the step modelled it is {c} (not significant)", "pozorny trend {a} jest w dużej mierze artefaktem skoku z 2012 ({b}); po uwzględnieniu skoku w modelu wynosi {c} (nieistotny)");
  p("apparent trend {a} is largely an artifact of the 2012 step ({b}); with the step modelled it is {c}", "pozorny trend {a} jest w dużej mierze artefaktem skoku z 2012 ({b}); po uwzględnieniu skoku w modelu wynosi {c}");
  p("no significant 2012 step ({a}); trend {b} ({s}), {c} with the step ({t})", "brak istotnego skoku w 2012 ({a}); trend {b} ({s|t}), {c} z uwzględnieniem skoku ({t|t})");
  p("2012 is {x} the 2011 level.", "2012 to {x} poziomu z 2011.");
  p("2012 over 2011 is {x} (overlap-calibrated variant {y}); mean 2012-2014 over 2009-2011 is {z} ({w}).", "2012 względem 2011 to {x} (wariant skalibrowany na nakładaniu {y}); średnia 2012-2014 względem 2009-2011 to {z} ({w}).");
  p("2012 over 2011 is {x}; mean 2012-2014 over 2009-2011 is {z}.", "2012 względem 2011 to {x}; średnia 2012-2014 względem 2009-2011 to {z}.");
  p("harmonized {a} ({b} with a 2012 step fitted): {v}.", "zharmonizowane {a} ({b} z dopasowanym skokiem 2012): {v|t}.");
  p("harmonized {a}: {v}.", "zharmonizowane {a}: {v|t}.");
  p("harmonized {a} ({b} with a 2012 step fitted).", "zharmonizowane {a} ({b} z dopasowanym skokiem 2012).");
  p("Calibrated variant: {v}.", "Wariant skalibrowany: {v|t}.");
  p("vs {#n}-year average ({#a})", "względem średniej z {#n} {n|pl:roku,lat,lat} ({#a})");
  p("{l} in {#y}", "{l|t} w {#y}");
  p("{#a} {l} in {#y}", "{l|t} w {#y}");
  p("within normal range; {#n} anomalous week", "w normalnym zakresie; {#n} tydzień anomalny");
  p("within normal range; {#n} anomalous weeks", "w normalnym zakresie; {#n} {n|pl:tydzień anomalny,tygodnie anomalne,tygodni anomalnych}");
  p("{l}; {#n} anomalous weeks", "{l|t}; {#n} {n|pl:tydzień anomalny,tygodnie anomalne,tygodni anomalnych}");
  p("{l}; {#n} anomalous week", "{l|t}; {#n} tydzień anomalny");
  p("{#o} vs {#m}", "{#o} wobec {#m}");
  p("{#n}× normal for this week", "{#n}× normy dla tego tygodnia");
  p("out of season", "poza sezonem");
  p("Information cutoff: {t}", "Moment odcięcia informacji: {t}");
  p("ML model: {m}, trained on years before {#y}. Blender: {b} (fit on {#f}).", "Model ML: {m}, trenowany na latach sprzed {#y}. Blender: {b} (dopasowany na {#f}).");
  p("ML model: {m}, trained on years before {#y}.", "Model ML: {m}, trenowany na latach sprzed {#y}.");
  p("Scoring: cumulative cells newly burning by day k inside the 41x41-cell window; strict = same 0.05 deg cell; tolerant = within 1 cell (~5.5 km), same as walk_forward._tol", "Ocena: skumulowane komórki nowo płonące do dnia k w oknie 41x41 komórek; ścisła = ta sama komórka 0,05°; tolerancyjna = w odległości 1 komórki (~5,5 km), tak jak w walk_forward._tol");
  p("Physics: fuel {f}, terrain {t}, weather {w}.", "Fizyka: paliwo {f|t}, teren {t|t}, pogoda {w}.");
  p("as-of index: all cell-days after {d} removed before forecasting (max day in forecaster input = {e})", "indeks as-of: wszystkie komórko-dni po {d} usunięto przed prognozowaniem (maks. dzień na wejściu prognozy = {e})");
  p("trained on years < {#y} (walk-forward fold)", "trenowano na latach < {#y} (fold walk-forward)");
  p("forest", "las"); p("flat (assumed)", "płaski (założony)"); p("grass", "trawa"); p("savanna", "sawanna"); p("shrub", "krzewy");

  /* ================================================================ small words coming from the backend / misc */
  e({
    "good": "dobra", "moderate": "umiarkowana", "degraded": "pogorszona",
    "clear": "bezchmurnie", "partly": "częściowo", "obscured": "zasłonięte", "unknown": "nieznane",
    "not usable": "nieużyteczny", "usable, not usable": "użyteczny, nieużyteczny",
    "Close popup": "Zamknij okienko",
    "No fire detected AND a clear-enough, usable overpass within the last `fresh_hours` (default 12 h). This is the closest the data gets to 'probably no active fire', not a guarantee (small or smouldering fires can be missed).":
      "Nie wykryto pożaru ORAZ w ostatnich `fresh_hours` (domyślnie 12 godz.) był wystarczająco bezchmurny, użyteczny przelot. To najbliższe, co dają dane, stwierdzeniu „prawdopodobnie brak aktywnego pożaru”, a nie gwarancja (małe lub tlące się pożary mogą zostać przeoczone).",
    "No fire detected, but the area was NOT well observed (no overpass, only swath-edge overpasses, or heavy cloud at every overpass). 'No detection' here means unknown, NOT 'no fire'.":
      "Nie wykryto pożaru, ale obszar NIE był dobrze obserwowany (brak przelotu, tylko przeloty przy krawędzi pasa lub gęste chmury podczas każdego przelotu). „Brak detekcji” oznacza tu „nieznane”, a NIE „brak pożaru”.",
    "Fire was detected in this cell earlier (before the window) but not now, and the area is poorly observed (cloud/swath gap): the fire may still be burning unseen.":
      "W tej komórce wykryto wcześniej pożar (przed oknem czasowym), ale nie teraz, a obszar jest słabo obserwowany (chmury / luka między pasami): pożar może nadal płonąć niezauważony.",
    "A satellite detected fire in this 0.05 deg cell during the window.": "Satelita wykrył pożar w tej komórce 0,05° w oknie czasowym.",
    "Observation quality is evaluated per coarse tile centre (overpass geometry and Open-Meteo cloud cover at that point); all 0.05 deg cells inside a tile share its class.":
      "Jakość obserwacji jest oceniana dla środka każdego dużego kafelka (geometria przelotów i zachmurzenie Open-Meteo w tym punkcie); wszystkie komórki 0,05° w kafelku mają tę samą klasę.",
    "Cloud is a model value; smoke, thin cirrus, fire size and pixel geometry are not modelled. 'Good observation' is not proof that no fire exists.":
      "Zachmurzenie to wartość modelowa; dym, cienkie cirrusy, wielkość pożaru i geometria piksela nie są modelowane. „Dobra obserwacja” nie dowodzi, że pożaru nie ma.",
    "S-NPP is excluded (its FIRMS NRT feed is empty). Swath-edge passes (> 45 deg) are not counted as good.":
      "S-NPP jest wykluczony (jego kanał FIRMS NRT jest pusty). Przeloty przy krawędzi pasa (> 45 stopni) nie są liczone jako dobre.",
    "Experimental": "Eksperymentalne",
  });
  e({
    "Per day,": "Dla każdego dnia,", "matching": "dopasowanie", "Trend 2003-2025:": "Trend 2003-2025:",
    "Cancel ": "Anuluj",
  });
  p("Burning calendar, {u} per ISO week for {#a} to {#b}; {#y} has {#n} anomalous weeks", "Kalendarz pożarów, {u|t} na tydzień ISO, lata {#a} do {#b}; w {#y}: {#n} {n|pl:tydzień anomalny,tygodnie anomalne,tygodni anomalnych}");
  p("Weekly {u} in {#y} against the mean and one standard deviation of other years; {#n} anomalous weeks", "Tygodniowe {u|t} w {#y} na tle średniej i jednego odchylenia standardowego pozostałych lat; {#n} {n|pl:tydzień anomalny,tygodnie anomalne,tygodni anomalnych}");
  p("{w} per year, MODIS and VIIRS, {#a} to {#b}. Sensor eras: MODIS only before 2012, S-NPP VIIRS from 20 January 2012, NOAA-20 from 2018.", "{w|t} w roku, MODIS i VIIRS, {#a} do {#b}. Ery sensorów: tylko MODIS przed 2012, S-NPP VIIRS od 20 stycznia 2012, NOAA-20 od 2018.");
  p("{w} per year, {#a} to {#b}. Sensor eras: MODIS only before 2012, S-NPP VIIRS from 20 January 2012, NOAA-20 from 2018.", "{w|t} w roku, {#a} do {#b}. Ery sensorów: tylko MODIS przed 2012, S-NPP VIIRS od 20 stycznia 2012, NOAA-20 od 2018.");
  /* ---- player + next-pass + simulate notes from the backend */
  e({
    "Timeline from the last detection to the next usable pass": "Oś czasu od ostatniej detekcji do następnego użytecznego przelotu",
    "This is not a statistical confidence interval.": "To nie jest statystyczny przedział ufności.",
    "Suomi NPP: FIRMS NRT S-NPP feed is empty (instrument not delivering fire data)": "Suomi NPP: kanał FIRMS NRT dla S-NPP jest pusty (instrument nie dostarcza danych o pożarach)",
    "Experimental screening estimate. Fuel mapping and live-moisture conversions are assumptions; landscape coverage can be partial. Firebreaks use heuristic slowdown, not calibrated stop probabilities. No suppression, spotting or explicit solar fuel heating. Weather is much coarser than the simulation raster. Low/high are sensitivity scenarios, not probability bounds. Prior validation reports apply to the older physics pipeline; the landscape update has not established improved predictive accuracy.":
      "Eksperymentalny szacunek przesiewowy. Mapowanie paliw i przeliczenia wilgotności paliwa żywego to założenia; pokrycie krajobrazu może być częściowe. Przeszkody ogniowe używają heurystycznego spowolnienia, a nie skalibrowanych prawdopodobieństw zatrzymania. Brak gaszenia, przerzutów ognia (spotting) i jawnego nagrzewania paliwa przez słońce. Pogoda jest znacznie grubsza niż raster symulacji. Wartości niskie/wysokie to scenariusze wrażliwości, a nie granice prawdopodobieństwa. Wcześniejsze raporty walidacyjne dotyczą starszego potoku fizyki; aktualizacja krajobrazowa nie wykazała poprawy trafności prognoz.",
    "Mapped Anderson fuel models used per cell; the listed class is the fallback for unmapped cells. Land-cover to fuel and moisture conversions remain assumptions.":
      "Dla każdej komórki użyto zmapowanych modeli paliw Andersona; wymieniona klasa jest wartością zastępczą dla komórek niezmapowanych. Przeliczenia pokrycia terenu na paliwo i wilgotność pozostają założeniami.",
    "Each simulation hour uses that hour's wind, temperature, humidity and rain; fuel moisture carries over between hours. Historical reanalysis replay is not an issued-weather-forecast backtest.":
      "Każda godzina symulacji używa wiatru, temperatury, wilgotności i deszczu z tej godziny; wilgotność paliwa przenosi się między godzinami. Odtworzenie historycznej reanalizy nie jest testem wstecznym na wydanej prognozie pogody.",
    "Heuristic spread slowdown, not a calibrated probability of stopping fire.": "Heurystyczne spowolnienie rozprzestrzeniania, a nie skalibrowane prawdopodobieństwo zatrzymania pożaru.",
    "km² simulated (all groups)": "km² (symulacja, wszystkie grupy)",
  });
  p("{#a} km² simulated (weather scenarios {r})", "{#a} km² (symulacja; scenariusze pogodowe {r})");
  p("{#a} km² simulated across {#n} fire groups (weather scenarios {r})", "{#a} km² (symulacja) w {#n} {n|pl:grupie pożarów,grupach pożarów,grupach pożarów}; scenariusze pogodowe {r}");
  p("km² simulated at end (weather scenarios {r})", "km² (symulacja) na końcu (scenariusze pogodowe {r})");
  p("km² simulated (all groups) (weather scenarios {r})", "km² (symulacja, wszystkie grupy; scenariusze pogodowe {r})");
  p("+{#a} this step", "+{#a} w tym kroku");
  p("simulated window {#h} h", "okno symulacji {#h} godz.");
  p("Area sensitivity: {#a}–{#b} km² across weather scenarios.", "Wrażliwość powierzchni: {#a}–{#b} km² w scenariuszach pogodowych.");
  p("{s} ({i}) {t}, scan {#a} deg ({q}), cloud {#c}% {v}, usable", "{s} ({i}) {t}, skanowanie {#a} stopni ({q|t}), zachmurzenie {#c}% {v|t}, użyteczny");
  p("{s} ({i}) {t}, scan {#a} deg ({q}), cloud {#c}% {v}, not usable: {w}", "{s} ({i}) {t}, skanowanie {#a} stopni ({q|t}), zachmurzenie {#c}% {v|t}, nieużyteczny: {w|t}");
  p("obscured {#c}%", "zasłonięte {#c}%"); p("partly {#c}%", "częściowo {#c}%"); p("clear {#c}%", "bezchmurnie {#c}%");
  p("{#n} FIRMS NRT detections within {#k} km (last 24 h) all assumed burning at start; positions are pixel centres (VIIRS 375 m / MODIS 1 km), not the true fire edge; split into separate fires by a 1 km gap rule", "{#n} {n|pl:detekcja,detekcje,detekcji} FIRMS NRT w promieniu {#k} km (ostatnie 24 godz.), wszystkie założone jako płonące na starcie; pozycje to środki pikseli (VIIRS 375 m / MODIS 1 km), a nie prawdziwa krawędź pożaru; podzielone na osobne pożary regułą przerwy 1 km");
  p("{#n} FIRMS NRT detection within {#k} km (last 24 h) assumed burning at start; positions are pixel centres (VIIRS 375 m / MODIS 1 km), not the true fire edge", "{#n} detekcja FIRMS NRT w promieniu {#k} km (ostatnie 24 godz.) założona jako płonąca na starcie; pozycje to środki pikseli (VIIRS 375 m / MODIS 1 km), a nie prawdziwa krawędź pożaru");

  e({
    "Suomi NPP": "Suomi NPP", "OpenStreetMap": "OpenStreetMap", "ESA WorldCover": "ESA WorldCover", "Copernicus DEM / Sentinel-2": "Copernicus DEM / Sentinel-2",
    "RainViewer": "RainViewer", "NOAA GOES": "NOAA GOES", "Meta/CIESIN HRSL": "Meta/CIESIN HRSL", "NASA FIRMS": "NASA FIRMS",
    "dashed lines: VIIRS 2012, NOAA-20 2018 · * partial year": "linie przerywane: VIIRS 2012, NOAA-20 2018 · * rok niepełny",
    "ERA5 cloud_cover via Open-Meteo archive (proxy, not a satellite cloud mask)": "Zachmurzenie ERA5 (cloud_cover) z archiwum Open-Meteo (przybliżenie, a nie satelitarna maska chmur)",
    "predicted-but-unobserved cells with no clear overpass proxy are excluded from the predicted set; cloud proxy is ERA5 modelled cloud cover, not the satellite cloud mask":
      "prognozowane, lecz niezaobserwowane komórki bez przybliżenia czystego przelotu są wyłączone ze zbioru prognozowanego; przybliżenie chmur to modelowane zachmurzenie ERA5, a nie satelitarna maska chmur",
  });
  p("Day {#d}: modelled cloud cover averaged {a}% at the daytime pass and {b}% at the night pass.", "Dzień {#d}: modelowane zachmurzenie wyniosło średnio {a}% w przelocie dziennym i {b}% w przelocie nocnym.");
  p("{c}% of the window was treated as obscured (cumulative unverifiable fraction {d}).", "{c}% okna uznano za zasłonięte (skumulowany udział nieweryfikowalny {d}).");
  p("{#n} flagged cell could not be verified (grey hatch) and is not counted as false alarms in the adjusted scores.", "{#n} oznaczonej komórki nie dało się zweryfikować (szary deseń) i nie wlicza się jej do fałszywych alarmów w wynikach skorygowanych.");
  /* ---- composite tooltips (aria-label "Title: text") and scenario notes from the backend */
  e({
    "no fire detected": "nie wykryto pożaru", "reference": "odniesienie", "no change": "bez zmian",
    "Every scenario except 'baseline' edits the forecast weather by a fixed amount to show sensitivity. These are NOT forecasts, NOT probabilities and NOT confidence intervals.":
      "Każdy scenariusz poza „bazowym” zmienia prognozowaną pogodę o stałą wartość, aby pokazać wrażliwość. To NIE są prognozy, NIE są prawdopodobieństwa i NIE są przedziały ufności.",
    "All scenarios use the same raster cell size so areas are comparable; it can differ slightly from a plain /simulate run.": "Wszystkie scenariusze używają tego samego rozmiaru komórki rastra, więc powierzchnie są porównywalne; może się on nieznacznie różnić od zwykłego przebiegu /simulate.",
    "Growth = burned area minus the initial footprint; reach per compass sector = farthest new burn beyond the initial footprint.": "Przyrost = spalona powierzchnia minus początkowy zasięg; zasięg w sektorze kompasu = najdalsze nowe spalenie poza początkowym zasięgiem.",
    "Physics model: experimental screening estimate (see /simulate method and uncertainty).": "Model fizyczny: eksperymentalny szacunek przesiewowy (zob. metoda i niepewność w /simulate).",
    "Simulated burned area over time for each weather scenario": "Symulowana spalona powierzchnia w czasie dla każdego scenariusza pogodowego",
    "This is a model/landscape result, not evidence that the fire cannot spread.": "To wynik modelu i krajobrazu, a nie dowód, że pożar nie może się rozprzestrzeniać.",
    "Likely the land around the detections is mapped non-burnable (e.g. cropland, water, built-up) or fuels are too wet; weather scenarios then show no difference.": "Prawdopodobnie teren wokół detekcji jest zmapowany jako niepalny (np. grunty orne, woda, zabudowa) albo paliwa są zbyt mokre; scenariusze pogodowe nie pokazują wtedy różnicy.",
  });
  p("the baseline simulation shows no spread beyond the initial footprint (blocked cells in the domain: {#n}).", "symulacja bazowa nie pokazuje rozprzestrzenienia poza początkowy zasięg (zablokowane komórki w obszarze: {#n}).");
  p("Baseline: {#a} km² burned by the end (growth {#g} km²), fastest head about {#r} m/min, forecast wind averaging {#w} km/h from {#d}°.", "Bazowy: {#a} km² spalone do końca (przyrost {#g} km²), najszybszy czoło ognia ok. {#r} m/min, prognozowany wiatr średnio {#w} km/h z kierunku {#d}°.");
  p("Wind speed and gusts multiplied by {#x} for every simulated hour (direction unchanged): still no spread beyond the initial footprint, so this weather change cannot be compared here (see warnings).", "Prędkość wiatru i porywy pomnożone przez {#x} w każdej symulowanej godzinie (kierunek bez zmian): nadal brak rozprzestrzenienia poza początkowy zasięg, więc tej zmiany pogody nie da się tu porównać (zob. ostrzeżenia).");
  p("Wind direction turned {a} deg (clockwise positive) in every simulated hour (speed unchanged): still no spread beyond the initial footprint, so this weather change cannot be compared here (see warnings).", "Kierunek wiatru obrócony o {a} stopni (dodatnie = zgodnie z ruchem wskazówek zegara) w każdej symulowanej godzinie (prędkość bez zmian): nadal brak rozprzestrzenienia poza początkowy zasięg, więc tej zmiany pogody nie da się tu porównać (zob. ostrzeżenia).");
  p("{#m} mm of rain added evenly over the first {#h} simulated hours; humidity raised to at least {#r}% in those hours; fuel moisture then recovers by the model's drying rules: still no spread beyond the initial footprint, so this weather change cannot be compared here (see warnings).", "{#m} mm deszczu dodane równomiernie w pierwszych {#h} symulowanych godzinach; wilgotność podniesiona do co najmniej {#r}% w tych godzinach; wilgotność paliwa następnie wraca zgodnie z regułami schnięcia modelu: nadal brak rozprzestrzenienia poza początkowy zasięg, więc tej zmiany pogody nie da się tu porównać (zob. ostrzeżenia).");
  p("Relative humidity {a} percentage points in every simulated hour: still no spread beyond the initial footprint, so this weather change cannot be compared here (see warnings).", "Wilgotność względna {a} punktów procentowych w każdej symulowanej godzinie: nadal brak rozprzestrzenienia poza początkowy zasięg, więc tej zmiany pogody nie da się tu porównać (zob. ostrzeżenia).");
  p("this week: {#a} burning cell-days vs usual {#b} (z {#z})", "w tym tygodniu: {#a} komórko-dni z pożarem wobec zwykle {#b} (z {#z})");
  p("Satellite ignition: {v}", "Zapłon wg satelity: {v|t}");
  p("Ignition estimate: {v}", "Szacunek zapłonu: {v|t}");
  p("Assumed ignition: {v}", "Zapłon założony: {v|t}");
  p("Fuel assumption: {v}", "Założenie dotyczące paliwa: {v|t}");
  p("Weather source: {v}", "Źródło pogody: {v|t}");
  p("Weather estimate: {v}", "Szacunek pogody: {v|t}");
  p("Landscape: {v}", "Krajobraz: {v|t}");
  p("Model note: {v}", "Uwaga o modelu: {v|t}");
  p("Confidence not calibrated. {v}", "Pewność nieskalibrowana. {v|t}");
  p("Fallback: {v}", "Wartość zastępcza: {v|t}");
  p("Truncated: {v}", "Obcięte: {v|t}");
  p("Calibrated variant: {v}", "Wariant skalibrowany: {v|t}");
  /* ---- exposure texts from the backend */
  e({
    "ESTIMATED EXPOSURE: an asset inside the simulated burned footprint. Not damage, not loss, not a forecast of casualties.": "SZACOWANA EKSPOZYCJA: obiekt wewnątrz symulowanego zasięgu spalenia. To nie szkoda, nie strata i nie prognoza liczby poszkodowanych.",
    "Ignition (estimate)": "Zapłon (szacunek)",
    "People inside the simulated footprint, not casualties or evacuees; static gridded or proxy estimates, no time-of-day or occupancy model. Not insured loss.": "Osoby wewnątrz symulowanego zasięgu, a nie poszkodowani ani ewakuowani; statyczne szacunki siatkowe lub przybliżone, bez modelu pory dnia ani zajętości. To nie jest strata ubezpieczeniowa.",
    "Experimental and uncalibrated wildfire spread simulation; scenario ranges (low/high weather) are sensitivity scenarios, not confidence intervals.": "Eksperymentalna i nieskalibrowana symulacja rozprzestrzeniania pożaru; zakresy scenariuszy (niska/wysoka pogoda) to scenariusze wrażliwości, a nie przedziały ufności.",
    "OpenStreetMap completeness varies by region: counts and lengths are lower bounds where the map is incomplete.": "Kompletność OpenStreetMap różni się w zależności od regionu: liczby i długości są dolnymi ograniczeniami tam, gdzie mapa jest niekompletna.",
    "This tool does not estimate insured or monetary loss and never outputs such values.": "To narzędzie nie szacuje strat ubezpieczeniowych ani pieniężnych i nigdy nie podaje takich wartości.",
    "Insured-loss modelling needs per-asset exposure values (replacement cost, contents, business interruption), vulnerability curves (damage ratio versus fire intensity, ember exposure, construction type, defensible space), and policy terms (deductibles, limits, exclusions), none of which are available here.": "Modelowanie strat ubezpieczeniowych wymaga wartości ekspozycji dla każdego obiektu (koszt odtworzenia, zawartość, przerwa w działalności), krzywych podatności (stosunek szkód do intensywności pożaru, narażenie na iskry, typ konstrukcji, strefa obronna) oraz warunków polis (franszyzy, limity, wyłączenia); żadne z nich nie są tu dostępne.",
    "Counts below are exposure (what could be in the way), not damage.": "Liczby poniżej to ekspozycja (co może znaleźć się na drodze ognia), a nie szkody.",
    "Static estimates with no time-of-day model.": "Szacunki statyczne, bez modelu pory dnia.",
    "Not a loss figure.": "To nie jest kwota strat.",
    "Not insured loss.": "To nie jest strata ubezpieczeniowa.",
  });
  p("Some OpenStreetMap requests failed ({e}). Counts below are lower bounds and may miss buildings and the building-based population proxy. Try again later; failed requests are retried after a short delay.", "Część zapytań do OpenStreetMap nie powiodła się ({e}). Liczby poniżej są dolnymi ograniczeniami i mogą pomijać budynki oraz oparte na budynkach przybliżenie liczby ludności. Spróbuj ponownie później; nieudane zapytania są ponawiane po krótkim opóźnieniu.");
  p("Some OpenStreetMap requests failed ({e}). Counts below are lower bounds and may miss assets. Try again later; failed requests are retried after a short delay.", "Część zapytań do OpenStreetMap nie powiodła się ({e}). Liczby poniżej są dolnymi ograniczeniami i mogą pomijać obiekty. Spróbuj ponownie później; nieudane zapytania są ponawiane po krótkim opóźnieniu.");
  p("Assets from OpenStreetMap contributors (ODbL), queried {t}, {#s} s. Footprint {#a} km² (weather scenarios {#l} to {#h} km²; not a confidence interval).", "Obiekty od współtwórców OpenStreetMap (ODbL), zapytanie: {t}, {#s} s. Zasięg {#a} km² (scenariusze pogodowe {#l} do {#h} km²; to nie przedział ufności).");
  p("Assets from OpenStreetMap contributors (ODbL), queried {t}. Footprint {#a} km² (weather scenarios {#l} to {#h} km²; not a confidence interval).", "Obiekty od współtwórców OpenStreetMap (ODbL), zapytanie: {t}. Zasięg {#a} km² (scenariusze pogodowe {#l} do {#h} km²; to nie przedział ufności).");
  e({
    "Drag a rectangle, or tap two opposite corners. Press Escape to cancel.": "Przeciągnij prostokąt lub dotknij dwóch przeciwległych narożników. Escape anuluje.",
    "Now select the opposite corner.": "Teraz wybierz przeciwległy narożnik.",
    "Area too large. Draw a smaller rectangle (maximum 40° × 40°).": "Obszar za duży. Narysuj mniejszy prostokąt (maksymalnie 40° × 40°).",
    "Area too large. Draw a smaller rectangle (maximum 25,000 km²).": "Obszar za duży. Narysuj mniejszy prostokąt (maksymalnie 25 000 km²).",
    "Area changed. Select a fire in this area.": "Zmieniono obszar. Wybierz pożar w tym obszarze.",
    "Area selected. The custom replay starts from the centre of this rectangle; choose a date and run replay.": "Wybrano obszar. Odtworzenie rozpocznie się w środku prostokąta; wybierz datę i uruchom odtworzenie.",
    "Current detection context; separate from the selected archive date. Use Predict spread to start a live forecast.": "Bieżące wykrycie, niezależne od wybranej daty archiwalnej. Wybierz Prognozuj rozprzestrzenianie, aby rozpocząć prognozę bieżącą.",
    "This is a current detection. Select Nearest live fire to forecast it instead of the archive date.": "To bieżące wykrycie. Wybierz Najbliższy bieżący pożar, aby prognozować je zamiast daty archiwalnej.",
    "Optional barriers": "Bariery opcjonalne",
    "Uncached OpenStreetMap barriers were not awaited; spread may cross unmapped roads or waterways.": "Nie oczekiwano na brakujące bariery OpenStreetMap; symulowany ogień może przekraczać nieodwzorowane drogi lub cieki wodne.",
    "Only MODIS has detected this fire. The combined ML + physics run may use MODIS as estimated ignition. MODIS detects fewer burning cells than VIIRS, so the initial footprint may be too small.": "Ten pożar wykrył tylko MODIS. Model ML + fizyka może użyć go jako przybliżonego źródła zapłonu. MODIS wykrywa mniej płonących komórek niż VIIRS, więc obszar początkowy może być zbyt mały."
  });
  p("Road and waterway barriers: {n}% cached coverage. Spread may cross unmapped barriers.", "Bariery drogowe i wodne: {n}% pokrycia z pamięci. Symulowany ogień może przekraczać nieodwzorowane bariery.");

  /* ---- sync 1: readability cleanup (agent A) */
  e({
    "Area": "Obszar",
    "Experimental, not calibrated.": "Eksperymentalne, nieskalibrowane.",
    "Estimates, not confirmed perimeters.": "Szacunki, a nie potwierdzone obrysy pożarów.",
    "Radar: RainViewer. Wind: Open-Meteo. Map view only.": "Radar: RainViewer. Wiatr: Open-Meteo. Tylko w widoku mapy.",
    "Live positions. Lines: past 50 min, next 70 min.": "Pozycje na żywo. Linie: ostatnie 50 min, następne 70 min.",
    "Run a prediction to use the timeline.": "Uruchom prognozę, aby użyć osi czasu.",
    "Colours show timing, not confidence.": "Kolory pokazują czas, a nie pewność.",
    "Pick a fire on the map, set the horizon, then run.": "Wybierz pożar na mapie, ustaw horyzont i uruchom.",
    "An earlier physics version over-predicted grass, savanna and shrub fires and under-predicted forest fires in a 10-fire test. That test does not validate the new landscape model. Daily ML forecasts have roughly 70–75% day-3 overlap with one-cell tolerance, but only about 25–31% exact-cell overlap.":
      "Wcześniejsza wersja fizyki zawyżała wyniki dla pożarów traw, sawann i krzewów, a zaniżała dla pożarów lasów w teście na 10 pożarach. Ten test nie waliduje nowego modelu krajobrazu. Dzienne prognozy ML mają około 70–75% pokrycia w 3. dniu przy tolerancji jednej komórki, ale tylko około 25–31% pokrycia dokładnie w tej samej komórce.",
    "Pixel centre: about 375 m (VIIRS) or 1 km (MODIS), larger at the swath edge. The fire can be anywhere inside.": "Środek piksela: około 375 m (VIIRS) lub 1 km (MODIS), większy przy krawędzi pasa skanowania. Ogień może być w dowolnym miejscu wewnątrz.",
    "Points are pixel centres.": "Punkty to środki pikseli.",
    "Draw, click the map, or enter coordinates. Max 40° × 40°.": "Narysuj, kliknij mapę lub wpisz współrzędne. Maks. 40° × 40°.",
    "Or bounding box: W, S, E, N": "Lub ramka: Z, S, W, N",
    "Tap a cell for details, a row to compare years.": "Dotknij komórki, aby zobaczyć szczegóły, a wiersza, aby porównać lata.",
    "Burning 5 km cells in the last 7 days vs. the same week in 2008–2025. Only areas far above normal are listed.": "Płonące komórki 5 km w ostatnich 7 dniach na tle tego samego tygodnia w latach 2008–2025. Na liście są tylko obszary znacznie powyżej normy.",
    "Global detections unavailable; showing earlier data. Use Load detections to retry.": "Detekcje globalne niedostępne; pokazano wcześniejsze dane. Użyj „Wczytaj detekcje”, aby spróbować ponownie.",
    "Loading detections…": "Wczytywanie detekcji…",
    "No detections in this area in the last 24 h.": "Brak detekcji w tym obszarze w ostatnich 24 godz.",
    "No detections. Draw another area or pick a quick location.": "Brak detekcji. Narysuj inny obszar lub wybierz szybką lokalizację.",
    "Daily ML + physics risk per ~5 km cell. Local accuracy is unknown.": "Dzienne ryzyko ML + fizyka na komórkę ~5 km. Lokalna trafność jest nieznana.",
    "Drag a rectangle, or tap two opposite corners.": "Przeciągnij prostokąt lub dotknij dwóch przeciwległych narożników.",
    "Forecast service unavailable. Try again later.": "Usługa prognozy niedostępna. Spróbuj ponownie później.",
    "Checking forecast service…": "Sprawdzanie usługi prognozy…",
    "Pick a fire, set a horizon, press Run": "Wybierz pożar, ustaw horyzont i naciśnij „Uruchom”",
    "Hourly spread · daily risk": "Rozprzestrzenianie godzinowe · ryzyko dzienne",
    "Compare activity across years": "Porównaj aktywność w kolejnych latach",
    "Run": "Uruchom",
  });
  p("{#n} strongest global detections (24 h) plus your area. Not a full global count.", "{#n} {n|pl:najsilniejsza detekcja globalna,najsilniejsze detekcje globalne,najsilniejszych detekcji globalnych} (24 godz.) plus Twój obszar. To nie jest pełna liczba globalna.");
  p("{#n} detections in the last 24 h. Click one to select it.", "{#n} {n|pl:detekcja,detekcje,detekcji} w ostatnich 24 godz. Kliknij jedną, aby ją wybrać.");
  p("{#n} detections in view, last 24 h. List shows the newest 150.", "{#n} {n|pl:detekcja,detekcje,detekcji} w widoku, ostatnie 24 godz. Lista pokazuje 150 najnowszych.");
  p("{n} strongest global detections (24 h) plus your area. Not a full global count.", "{n} najsilniejszych detekcji globalnych (24 godz.) plus Twój obszar. To nie jest pełna liczba globalna.");


  /* ---- sync 2: ui-*.js text cleanup (agent C) */
  e({
    "Experimental estimates from satellite detections and OpenStreetMap. Not calibrated.": "Eksperymentalne szacunki z detekcji satelitarnych i OpenStreetMap. Nieskalibrowane.",
    "Fit to this fire's recent growth": "Dopasuj do niedawnego wzrostu tego pożaru",
    "Off: physics uses the issued weather and mapped landscape only.": "Wyłączone: fizyka używa wyłącznie wydanej pogody i zmapowanego krajobrazu.",
    "Select a fire on the map.": "Wybierz pożar na mapie.",
    "One-page brief: footprint, estimated exposure, limits, sources. First request can take 1-2 min.": "Jednostronicowy raport: zasięg, szacowana ekspozycja, ograniczenia, źródła. Pierwsze żądanie może potrwać 1-2 min.",
    "Hatched areas were poorly observed. No detection there does not mean no fire.": "Zakreskowane obszary były słabo obserwowane. Brak detekcji w nich nie oznacza braku pożaru.",
    "Click the map to inspect tiles (off: select fires)": "Kliknij mapę, aby sprawdzić kafelki (wyłączone: wybór pożarów)",
    "Baseline against fixed wind, rain and humidity edits. Not probabilities or confidence intervals.": "Wariant bazowy na tle stałych zmian wiatru, deszczu i wilgotności. To nie są prawdopodobieństwa ani przedziały ufności.",
    "Assets from OpenStreetMap inside the simulated burned area. Needs a finished ML + physics run.": "Obiekty z OpenStreetMap wewnątrz symulowanego obszaru spalenia. Wymaga zakończonego przebiegu ML + fizyka.",
    "Same instruments throughout: the steadiest basis for trends across 2012, but it counts only a fraction of what VIIRS sees.": "Te same instrumenty przez cały czas: najstabilniejsza podstawa do trendów w poprzek 2012, ale liczy tylko ułamek tego, co widzi VIIRS.",
    "Across Amazon, N. Australia, India, W. USA and Canada, MODIS counted 13-56% of VIIRS cell-days and 8-31% of detections.": "W Amazonii, północnej Australii, Indiach, zachodnich USA i Kanadzie MODIS liczył 13-56% komórko-dni VIIRS i 8-31% detekcji.",
    "Step across 2012 here (2013-15 over 2009-11): any-sensor cell-days": "Skok w poprzek 2012 tutaj (2013-15 względem 2009-11): komórko-dni dowolnego sensora",
    ". Part of any step is real change.": ". Część każdego skoku to rzeczywista zmiana.",
    "(dashed lines mark changes):": "(linie przerywane oznaczają zmiany):",
    "Hover, tap or press arrow keys to read values.": "Najedź, dotknij lub użyj klawiszy strzałek, aby odczytać wartości.",
    "Harmonization reduces the 2012 step but does not remove it for every measure; fire detections are the cleanest. The shaded pre-2012 part is a statistical reconstruction, not an observation.": "Harmonizacja zmniejsza skok z 2012, ale nie usuwa go dla każdej miary; najczystszą miarą są detekcje pożarów. Zacieniona część sprzed 2012 to statystyczna rekonstrukcja, a nie obserwacja.",
    "Forecast as issued, using only data up to the cutoff. Reveal compares it with later satellite detections.": "Prognoza taka, jaka zostałaby wydana, z użyciem wyłącznie danych do chwili odcięcia. Odkrycie wyników porównuje ją z późniejszymi detekcjami satelitarnymi.",
    "Computed on demand, up to 1 min. Dates before 2024: ML only.": "Liczone na żądanie, do 1 min. Daty sprzed 2024: tylko ML.",
    "Hit: newly burning cell predicted in the same 0.05 degree cell (about 5.5 km). Cumulative by day.": "Trafienie: nowo płonąca komórka przewidziana w tej samej komórce 0,05 stopnia (około 5,5 km). Skumulowane do każdego dnia.",
    "Ring baseline: marks every cell within about 5 km of the burning area as burned. Not a forecast model. Within 5 km is an overlap score, not accuracy.": "Linia bazowa „pierścień”: oznacza jako spaloną każdą komórkę w promieniu około 5 km od płonącego obszaru. To nie jest model prognostyczny. „W promieniu 5 km” to wynik nakładania, a nie trafność.",
    "Scores are uncalibrated; see the model card for the walk-forward benchmark.": "Wyniki są nieskalibrowane; benchmark walk-forward znajdziesz w karcie modelu.",
    "Shading shows relative, uncalibrated scores, not chances.": "Odcienie pokazują wyniki względne, nieskalibrowane, a nie szanse.",
  });
  p("Day {#d}: strict IoU {a} is {c} the ring baseline ({b}); within-5 km score {x} is {d} it ({y}).", "Dzień {#d}: ścisłe IoU {a} jest {c|t} linii bazowej „pierścień” ({b}); wynik w promieniu 5 km {x} jest {d|t} niej ({y}).");
  p("Day {#d}: modelled cloud {a}% (day pass), {b}% (night pass).", "Dzień {#d}: modelowane zachmurzenie {a}% (przelot dzienny), {b}% (przelot nocny).");
  p("{a}% of the window obscured; {b}% unverifiable cumulatively.", "{a}% okna zasłonięte; {b}% nieweryfikowalne łącznie.");
  p("{#n} flagged cells could not be verified (grey hatch) and are excluded from adjusted false alarms.", "{#n} {n|pl:oznaczonej komórki,oznaczone komórki,oznaczonych komórek} nie dało się zweryfikować (szary deseń) i wyłączono je z fałszywych alarmów w wynikach skorygowanych.");
  p("{#n} flagged cell could not be verified (grey hatch) and is excluded from adjusted false alarms.", "{#n} oznaczonej komórki nie dało się zweryfikować (szary deseń) i wyłączono ją z fałszywych alarmów w wynikach skorygowanych.");

  window.EQUI_I18N_PL = { exact: E, patterns: P };
})();
