# Pogoda z archiwalnych prognoz: izolowany provider i rzeczywisty pilot

Stan: 2026-11-15. Zadanie #3, .

**Zaimplementowano i sprawdzono odrębnego providera Single Runs, chronologię, cache i mały rzeczywisty test wejść zamrożonych modeli. Nie wykazano poprawy operacyjnej trafności. Żaden z ośmiu przypadków nie spełnia wymagań ścisłego testu as-issued. Nie zmieniono produkcji, wag, reguł wyboru komórek ani silnika fizyki.**

## Co udostępnia źródło — i czego nie należy mylić

Historical Forecast skleja początkowe godziny kolejnych wydań. Pobranie z niego całego przyszłego okna nie odtwarza informacji dostępnej w jednej chwili. Daty pokrycia zależą od modelu. [Oficjalna dokumentacja Historical Forecast](https://open-meteo.com/en/docs/historical-forecast-api).

Single Runs wybiera inicjalizację parametrem `run`. Dokumentacja podaje IFS HRES od 2024-03-14 jako **IFS Cycle 49R1 hindcasts**, przejście na 50R1 od 2026-05-12 06UTC; inne modele mają archiwum od 2026-04-02. Inicjalizacja nie jest publikacją: obliczenia globalne trwają zwykle kolejne 4–6 godzin. Dlatego sam warunek `run <= cutoff` nie wystarcza. [Oficjalna dokumentacja Single Runs, Data Sources](https://open-meteo.com/en/docs/single-runs-api#data_sources).

Bieżące metadata zawierają czas inicjalizacji, modyfikacji i dostępności ostatniego wydania. Modyfikacja nie oznacza dostępności w API; replikacja serwerów dodatkowo wymaga ostrożności. Odczyt dzisiejszej metadanej nie jest dowodem publikacji konkretnego wydania z 2024/2025. [Oficjalne Model Updates](https://open-meteo.com/en/docs/model-updates).

**Interpretacja ostrożna:** provider klasyfikuje wcześniejsze IFS jako `cycle49r1_hindcast_or_unverified_operational`. Nie twierdzi, że żaden historyczny forecast operacyjny nigdy nie istniał. Twierdzi, że użyty zasób/API i zachowane dowody nie potwierdzają równoważności danych z rzeczywistym wydaniem dostępnym w zadanym t0. Nawet dostarczona przez wywołującego deklaracja czasu dostępności nie usuwa tej blokady dla tego okresu.

## Działający kod

- `equifire/forecast_weather.py`: osobny reader, bez importu produkcyjnych modeli i bez fallbacku do reanalizy.
- `scripts/evaluate_forecast_weather.py`: powtarzalny, mały eksperyment wejść istniejących modeli. Odczytuje zastany indeks pamięciowo mapowany, nie uruchamia automatycznego rebuild.
- `tests/test_forecast_weather.py`: 25 testów offline chronologii, dowodów dostępności, pokrycia, jednostek, ograniczeń API i cache.
- `reports/forecast_weather_pilot.json`: wszystkie przypadki, źródła, daty, pokrycie, reguły, hashe modeli i wyniki. `cache/forecast_weather/*.json`: 8 surowych odpowiedzi z dokładnymi parametrami, czasem pobrania i SHA-256.

`ForecastWeather.fetch(...)` domyślnie wymaga as-issued. Odrzuca przyszły `run`, nieprawidłowy cykl UTC, nieudokumentowane pokrycie, best-match/seamless, hindcast/niezweryfikowane pochodzenie i brak dowodu dostępności w t0. `AvailabilityEvidence` musi odpowiadać modelowi i inicjalizacji oraz wskazywać zachowany rekord API. Reader sprawdza spójność tej zaufanej deklaracji, **nie uwierzytelnia dowolnego zewnętrznego rekordu**. Dozwolenie współczesnego archiwum nie oznacza automatycznego potwierdzenia dostępności — dowód nadal jest wymagany.

Jawny tryb badawczy `require_as_issued=False` pozwala pobrać niezweryfikowane dane, zachowując `as_issued_eligible=false`; domyślne `daily_tuple()` nadal odmawia użycia takiego wyniku jako as-issued ML. Nie ma cichej zamiany niepowodzenia na ERA5, stitched forecast ani NaN udające wynik.

Metadane obejmują żądaną inicjalizację, cutoff, źródło, klasę archiwum, informację o dostępności, cyklu, jednostkach, zwróconym punkcie siatki i pokryciu. Każda godzina ma lead względem inicjalizacji i cutoff. API nie zwróciło własnego znacznika inicjalizacji w JSON; zapisywany jest jawnie żądany `run`, a pełna oś godzin jest kontrolowana względem niego. To ograniczenie ma własne pole `run_init_returned_by_api=false`.

Wymagane są jawne strefy czasowe. Dla wyjściowych godzin ISO bez offsetu UTC wynika z żądania GMT i sprawdzonego `utc_offset_seconds=0`. Jednostki są weryfikowane: km/h, stopnie kierunku FROM, °C, %, mm. Niepełna doba nie otrzymuje zestawu cech, brak opadu nie staje się zerem. Cache jest atomowy, sprawdza SHA-256, odtwarza dane offline i ponownie kontroluje cutoff przy każdym użyciu.

## Rzeczywista pułapka endpointu i budżet HTTP

Pierwsze 8 zapytań z inicjalizacją 12UTC zwróciło HTTP400. Diagnostyka wskazała konkretną przyczynę: parametr `daily` w Single Runs wymaga `run` rozpoczynającego się o 00:00 w żądanej strefie. Naprawa cofa wybór do wcześniejszej północy UTC; nie przesuwa go na nowsze, przyszłe wydanie. Provider ma jawny guard tego ograniczenia.

W badaniu wykonano łącznie **18 zapytań HTTP**: 1 pierwszy probe godzinowy200, 8 pierwszych odrzuconych zapytań400, 1 diagnostyczne400, 8 poprawnych200. Nie uruchamiano automatycznych retry ani obejścia innym zbiorem. Końcowe odtworzenie używało wyłącznie 8 lokalnych odpowiedzi i 0 HTTP. Nie wykonano długiego treningu ani przebiegów fizyki.

## Protokół pilota i rzeczywiste pokrycie bazowych wejść

Użyto po 4 przypadki z uprzednio ocenianych list `ensemble_ab_2024.json` i `ensemble_ab_2025.json`. Wybór określają daty kwartałów i dostępność wejściowych danych pogodowych, nie przyszłe etykiety ognia. Uwzględniono pierwszy przypadek z pełnym dotychczasowym tuple pogody; reszta to pierwsze benchmarkowe przypadki po zadanych datach. To retrospektywne ponowne użycie benchmarku, nie nowy niezależny test.

Aktualny lokalny `direct.Weather()` dostarcza pełne pięć cech tylko dla **1/100** przypadków całego benchmarku2024 oraz **0/100** w2025. Pozostałe mają NaN; nie nazywamy ich obserwowaną reanalizą. W pilocie baseline jest pełny w1/8. Łączna zmiana w7/8 dodaje przede wszystkim pokrycie — nie mierzy czystej różnicy „prognoza kontra reanaliza”. Żadnych braków nie uzupełniono pobranymi po fakcie obserwacjami.

| Data cech | Lokalizacja ognia, lat/lon | Zastany baseline | Żądana inicjalizacja UTC | Pełne godziny |
|---|---|---|---|---|
| 2024-04-01 | 23.175, 79.275 | pełne 5/5 | 2024-03-31T00:00:00Z | 119/120 |
| 2024-04-01 | -27.125, 149.125 | brak 5/5 — NaN | 2024-03-31T00:00:00Z | 119/120 |
| 2024-07-07 | -12.575, 38.225 | brak 5/5 — NaN | 2024-07-06T00:00:00Z | 119/120 |
| 2024-10-01 | -11.325, 34.925 | brak 5/5 — NaN | 2024-09-30T00:00:00Z | 119/120 |
| 2025-04-05 | 24.575, 79.925 | brak 5/5 — NaN | 2025-04-04T00:00:00Z | 119/120 |
| 2025-07-02 | -3.725, 103.725 | brak 5/5 — NaN | 2025-07-01T00:00:00Z | 119/120 |
| 2025-10-01 | -13.775, 136.225 | brak 5/5 — NaN | 2025-09-30T00:00:00Z | 119/120 |
| 2025-12-10 | 23.725, 87.475 | brak 5/5 — NaN | 2025-12-09T00:00:00Z | 119/120 |

Dla zgodności przestrzennej zapytania kierowano do środka tej samej 2-stopniowej płytki, jak w starym providerze. Zwrócona najbliższa komórka IFS nie jest identyczną komórką ERA5; jej współrzędne są zapisane. Pozostaje konfuzja zmiany źródła, rozdzielczości i braków danych.

Każda poprawna odpowiedź ma 120 godzin; pierwsza godzina opadu jest null. W efekcie jest119 kompletnych godzin i4 kompletne doby. Do porównania wybierana jest następna pełna doba po inicjalizacji, nigdy niepełna doba początkowa.

Jedyna para z pełną zastaną pogodą: Indie, 2024-04-01. Zastane wartości → IFS: wiatr20,6→20,2km/h, kierunek264→269°, temperatura maksymalna38,0→37,8°C, RH minimalna12→11%, suma opadu0→0mm. To pojedyncze porównanie wejść, nie walidacja prognozy meteorologicznej.

## Zamrożony ML: co dokładnie porównano

Dla każdej pary wykonano inferencję `wf_<year>.json` z niezmienioną listą cech i niezmienionymi regułami z jego metadanych: baseline z zastanego cache oraz jawnie badawczy tuple IFS. Ten sam valid day t0, te same pięć agregatów i jednostki. Historia satelitarna, siatka komórek kwalifikowanych oraz etykiety pozostają wspólne. Kod sprawdza zgodność maski i historii; SHA-256 wag i metadanych przed/po pozostaje identyczne. Brak strojenia pod te osiem wyników.

Pogodowy cutoff to00UTC dnia t0; wybrany run jest jeszcze24h wcześniejszy. Benchmark ognia używa obserwacji przez dzień t0, więc jego proxy odcięcia jest następną północą. Użyto celowo starszej prognozy pogody. Ta ostrożność chronologiczna **nie rozwiązuje** kwestii hindcastów i historycznej dostępności.

Wyniki poniżej to średnia po przypadkach, strict IoU na dotychczasowych regułach oraz Brier na tej samej masce kwalifikowanych komórek. Nie podano pozornej istotności statystycznej dla próbki8.

| Dzień | Brier baseline | Brier badawczy IFS | Strict IoU baseline | Strict IoU badawczy IFS |
|---|---|---|---|---|
| 1 | 0.074453 | 0.073808 | 25.46% | 25.39% |
| 2 | 0.074722 | 0.075286 | 30.55% | 30.25% |
| 3 | 0.081612 | 0.082405 | 22.10% | 21.61% |

Zmiany są małe i mieszane: Brier lepszy tylko dnia1, strict IoU minimalnie gorsze we wszystkich trzech horyzontach. **Nie ma podstaw do wdrożenia ani obietnicy poprawy trafności.** To również nie jest ocena połączonego blendera/fizyki — porównano wyłącznie frozen daily ML. Aktualna strona i produkcyjne modele nadal działają według wcześniejszego pipeline'u.

## Przyszłe cechy i właściwy test as-issued

Raport JSON zawiera `future_feature_harness` z pełnymi dobami t0+1..3 i leadami z tego samego wydania. Nie wkładamy ich w pola oznaczające pogodę dnia t0. Dodanie ich do ML wymaga osobnego schematu cech, chronologicznego treningu, walidacji, zamrożenia reguł oraz niezależnej oceny. Brak archiwum2023 w tym konkretnym Single Runs źródle utrudnia bezpośrednie porównanie modelu testowanego2024, którego trening ma kończyć się wcześniej.

Do uznania testu operacyjnego potrzebne są zweryfikowane archiwalne **wydania operacyjne**, ich wersje i dostępność przed cutoff (albo od teraz prowadzony prospektywny log). Rekord publikacji musi dotyczyć tego samego modelu i run; bufor12/24h jest założeniem, nie dowodem. Dalej należy ustalić wspólne kryteria pokrycia, osobno ocenić dodanie brakujących wejść, zachować okresy trening/kalibracja/test i pokazać wszystkie wyniki. Dziś liczba potwierdzonych as-issued przypadków wynosi0/8.

## Odtwarzanie i zakres walidacji

```bash
.venv/bin/python -m pytest tests/test_forecast_weather.py -q
.venv/bin/python scripts/evaluate_forecast_weather.py --offline
```

Pierwsza komenda:25 PASS, wszystkie bez sieci. Druga:8 odczytów cache i16 inferencji zamrożonych modeli CPU z2wątkami; potrzebuje istniejących modeli i indeksu ognia, nie pobiera brakujących artefaktów. W zapisanym końcowym przebiegu czas wynosił 1.767s. Bez `--offline` provider może pobrać brakujące8 odpowiedzi; budget jest ograniczony, a429 zatrzymuje pilot. Czas pilota nie mierzy pełnego żądania strony ani szybkości pobierania pogody w dowolnej lokalizacji.

Testy obejmują: odmowę przyszłego run, brak dowodu publikacji, mylący hindcast, niezgodne rekordy dostępności, timezone/cycles, jednostki, braki godzin i dat, checksum/cache, limit HTTP i429, midnight daily constraint. Nie obejmują poprawności numerycznej modelu meteorologicznego, prawdziwości zewnętrznie dostarczonego dowodu publikacji ani poprawy pożarowej generalizacji.
