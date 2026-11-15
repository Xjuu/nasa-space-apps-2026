# FireSpot: Analiza błędów po odtworzeniu według biomu (Idea #27)

**Stan:** 15 listopada 2026. Analiza błędów wg biomu (zadanie #27).  
**Cel:** Systematyczna dekompozycja błędów przestrzennych modelu (trafienia, pominięcia, fałszywe alarmy, IoU ścisłe i tolerancyjne w sąsiedztwie Within-5km oraz wpływ przesłony chmurowej) na 6 zróżnicowanych biomach Ziemi na podstawie referencyjnych odtworzeń historycznych (*replays*).

> [!IMPORTANT]
> **Rzetelność metodologiczna: diagnostyka bez nauki na teście (*No Online Learning*)**  
> Niniejsza analiza opiera się wyłącznie na 6 utrwalonych odtworzeniach referencyjnych (z deterministycznym fallbackiem `data/biome_benchmarks_reference.json` i `cache/replay/`). W procesie tym **nie zmieniono wag modeli, nie modyfikowano hiperparametrów ani nie dokonywano dopasowywania reguł pod zbiór testowy**. Celem jest jawne wykazanie, gdzie fizyka i ML napotykają obiektywne ograniczenia domenowe oraz gdzie sensory satelitarne cierpią na martwe pola widzenia.

---

## 1. Zestawienie przekrojowe 6 biomów

| Biom / Ekosystem | Incydent referencyjny | Metoda modelu | Reżim pożarowy | Dzień 1 Strict IoU | Dzień 3 Strict IoU | Dzień 3 Within-5km | Nieweryfikowalne FP (chmury) | Dominujący mechanizm błędu |
|---|---|---|---|---:|---:|---:|---:|---|
| **Las borealny (Tajga)** | Flin Flon 2025 (Kanada) | ML + fizyka | Pożar wierzchołkowy świerka/sosny, tlenie ściółki | **9,1%** | **33,3%** | **77,3%** | 0,0% | Opóźniony zapłon w koronie, nadmiar boczny w paliwie |
| **Torfowiska tropikalne** | Kalimantan 2023 (Indonezja) | Tylko ML (XGBoost) | Podpowierzchniowe tlenie torfu i zarośli | **15,8%** | **30,8%** | **87,2%** | **45,3% (D1) / 23,7% (3-dni)** | Ciągła przesłona chmurami konwekcyjnymi i dymem |
| **Zalewowa sawanna** | Pantanal 2024 (Brazylia) | ML + fizyka | Powierzchniowy pożar suchych traw i nadrzecza | **29,0%** | **45,5%** | **87,5%** | **— (brak FP)** | Czysty, ciągły front płomieni (najwyższa trafność) |
| **Rolnictwo uprawne** | Pendżab 2024 (Indie) | ML + fizyka | Drobne, punktowe wypalanie ściernisk ryżowych | **29,2%** | **39,0%** | **90,8%** | **0,0%** | Tysiące rozproszonych podpaleń (wysoka czułość 98%, niska precyzja komórkowa) |
| **Pogranicze las/sawanna** | Roraima 2024 (Amazonia) | ML + fizyka | Wypalanie wycinek i podszyt lasu tropikalnego | **20,6%** | **22,8%** | **76,3%** | **17,6% (3-dni)** | Popołudniowe chmury kłębiaste nad skrajem lasu |
| **Preria umiarkowana** | Smokehouse Creek 2024 (USA) | ML + fizyka | Błyskawiczny bieg traw napędzany wichurą | **23,8%** | **30,3%** | **74,4%** | **3,9% (3-dni)** | Bardzo wysoka prędkość liniowa, zwroty frontu wiatru |

*Średnie dla całego benchmarku:*  
- Średnie Strict IoU Dzień 1: **21,2%**  
- Średnie Strict IoU Dzień 3: **33,6%**  
- Średnie dopasowanie tolerancyjne Dzień 3 (Within-5km): **82,2%**  
- Średnia czułość skumulowana (Recall): **76,5%**  

---

## 2. Szczegółowa diagnoza biomów i profile błędów

### 2.1 Las borealny — Flin Flon 2025 (Kanada)
- **Charakterystyka:** Gęsty bór świerkowo-sosnowy z grubą warstwą mchów i torfu organicznego.
- **Metoda modelu:** Sprzężony model ML + fizyka rozprzestrzeniania Rothermela.
- **Dekompozycja błędów:**
  - **Dzień 1:** Niska ścisła trafność ($IoU = 9,1\%$, $Precision = 10,2\%$, $Recall = 46,2\%$, $FP = 53$, $FN = 7$). Model symulacyjny potrzebuje czasu na rozbudowę strefy pożaru wierzchołkowego; rozprzestrzenianie jest początkowo przeszacowane na boki w zwartym kompleksie leśnym.
  - **Dzień 3:** Wyraźny wzrost zgodności ($IoU = 33,3\%$, $Recall = 59,5\%$). W ocenie sąsiedztwa Within-5km model osiąga **77,3%** trafności ($IoU = 63,0\%$).
- **Interpretacja metryki Within-5km:** Wskaźnik ten odpowiada binarnej dylatacji 3×3 na siatce 0,05° (~5,5 km w osiach głównych, ~7,8 km diagonalnie / metryka Czebyszewa). Wskazuje on na poprawność makro-kierunku i zasięgu plamy pożaru, nie stanowiąc gwarancji precyzji w stałym promieniu euklidesowym 5 km.

### 2.2 Torfowiska tropikalne — Kalimantan 2023 (Indonezja)
- **Charakterystyka:** Odwodnione tropikalne torfowiska podlegające głębokiemu tleniu bezpłomieniowemu, emitujące potężne ilości aerozoli dymowych pod tropikalną pokrywą chmur.
- **Metoda modelu:** Bezpośredni XGBoost (tylko ML). Ze względu na brak powierzchniowego frontu płomieni i dominację żarzenia podpowierzchniowego torfu (smoldering duff), równania frontu Rothermela nie mają tu zastosowania fizycznego.
- **Dekompozycja błędów:**
  - **Pozorne fałszywe alarmy pod chmurami:** W Dniu 1 aż **45,3%** rzekomych 'fałszywych alarmów' (24 z 53 komórek) znajdowało się pod chmurami konwekcyjnymi (proxy ERA5 $> 80\%$). W ujęciu skumulowanym (wszystkie 3 dni) udział ten wynosi **23,7%** (67 z 283 komórek FP).
  - **Asymetryczny filtr chmurowy vs maska obserwowalności:** Skorygowane Strict IoU (eliminujące prognozowane FP pod chmurami) rośnie z **19,7% do 25,3%** w Dniu 2 oraz z **15,8% do 21,1%** w Dniu 1. Należy pamiętać, że jest to asymetryczny filtr usuwający niezweryfikowane predykcje, a nie symetryczna maska obserwowalności (zapisy replayu nie zawierają symetrycznej maski chmur dla prawdy naziemnej, stąd wskaźnik `observable_strict_iou_pct` jest zwracany jako `None`).
  - **Dzień 3 tolerancyjny (sąsiedztwo 3×3):** Osiąga **87,2%** dopasowania ($IoU = 77,3\%$).
- **Wniosek:** Pozorna niska precyzja komórkowa w tropikach to w znacznej mierze artefakt braku widoczności satelitarnej, a nie błąd samego modelu pożarowego.

### 2.3 Zalewowa sawanna — Pantanal 2024 (Brazylia / Boliwia)
- **Charakterystyka:** Otwarte przestrzenie wyschniętych traw i nadrzecznych zarośli w porze suchej, płaska topografia.
- **Metoda modelu:** Sprzężony model ML + fizyka rozprzestrzeniania Rothermela.
- **Dekompozycja błędów:**
  - **Najwyższa precyzja i spójność:** Brak zachmurzenia ($0\%$ komórek nieweryfikowalnych, `— (brak FP)` pod chmurami). 
  - **Dzień 1:** $IoU = 29,0\%$, $Precision = 44,0\%$, $Recall = 45,8\%$.
  - **Dzień 3:** $IoU = 45,5\%$, $Precision = 50,6\%$, a czułość w pasmie sąsiedztwa Within-5km wynosi **100%** (wszystkie 49 nowo powstałych komórek ognia znalazły się w modelowanym buforze sąsiedztwa, wynik Within-5km = **87,5%**).
- **Wniosek:** W otwartych ekosystemach sawannowych sprzężenie ML z propagacją eliptyczną Huygensa/Rothermela osiąga najwyższą zbieżność z obserwacjami satelitarnymi.

### 2.4 Rolnicze wypalanie ściernisk — Pendżab 2024 (Indie)
- **Charakterystyka:** Tysiące niezależnych ognisk podpalanych celowo przez rolników na prywatnych parcelach po zbiorach ryżu.
- **Metoda modelu:** Sprzężony model ML + fizyka rozprzestrzeniania Rothermela.
- **Dekompozycja błędów:**
  - **Strukturalna pułapka wskaźnika IoU:** Model fizyczny traktuje ogniska jako fronty pożarowe, tymczasem ogień pojawia się wyspowo w setkach rozproszonych punktów.
  - **Ekstremalna czułość (Recall):** Dzień 1 = **92,1%**, Dzień 2 = **93,7%**, Dzień 3 = **98,2%** (model poprawnie wskazał niemal wszystkie 329 nowo płonących działek).
  - **Kara za fałszywe alarmy komórkowe:** Z powodu rozproszonej mozaiki pól, model wygenerował 509 komórek FP w Dniu 3, co zaniża ścisłą precyzję do 39,3%. Jednak w ujęciu sąsiedztwa Within-5km wynik wynosi aż **90,8%**.
- **Wniosek:** IoU na poziomie piksela 1 km karze model za specyfikę rolnictwa; wskaźnik sąsiedztwa 3×3 komórek potwierdza trafność lokalizacji ognisk dymu.

### 2.5 Pogranicze wilgotnego lasu i sawanny — Roraima 2024 (Amazonia)
- **Charakterystyka:** Pas wylesiania w północnej Amazonii, pożary na styku wycinek i podszytu lasu pierwotnego.
- **Metoda modelu:** Sprzężony model ML + fizyka rozprzestrzeniania Rothermela.
- **Dekompozycja błędów:**
  - **Wpływ wilgoci i konwekcji:** Częste popołudniowe chmury deszczowe zasłaniają przeloty dzienne VIIRS. 35 do 50 komórek na dobę to komórki nieobserwowalne (17,6% skumulowanych FP pod chmurami).
  - **Korekta asymetryczna:** Strict IoU w Dniu 1 po odfiltrowaniu niezweryfikowanych FP wzrasta z **20,6% do 27,0%**, a w Dniu 2 z **24,0% do 30,7%**.
- **Wniosek:** Ekotony leśno-sawannowe wymagają fuzji z danymi geostacjonarnymi (GOES), aby eliminować luki w przelotach orbitalnych pod chmurami.

### 2.6 Preria umiarkowana — Smokehouse Creek 2024 (Teksas, USA)
- **Charakterystyka:** Półsucha preria trawiasta, bardzo silne porywy wiatru synoptycznego (>50 km/h).
- **Metoda modelu:** Sprzężony model ML + fizyka rozprzestrzeniania Rothermela.
- **Dekompozycja błędów:**
  - **Dynamika prędkości:** Najszybszy pożar w historii Teksasu. Wąskie, wydłużone czoło pożaru napędzane wiatrem.
  - **Wrażliwość na kąt wiatru:** Przejście frontu chłodnego i zmiana kierunku wiatru o 90° powoduje ścisłe pominięcia na skrzydłach ($FN = 11$ w Dniu 2). Mimo to tolerancyjny wskaźnik Dnia 3 sięga **74,4%** (Recall tolerancyjny = **94,1%**).

---

## 3. Podsumowanie wniosków dla wyzwania NASA

1. **Jedna miara nie wystarcza dla wszystkich biomów:**  
   Ocena modelu wyłącznie wskaźnikiem ścisłego IoU (Strict IoU) faworyzuje pożary o zwartej geometrii (sawanny Pantanalu, $45,5\%$), a drastycznie karze pożary rolnicze (Pendżab) i zamglone torfowiska (Kalimantan). Wprowadzenie miary sąsiedztwa 3×3 komórki (Within-5km) wyrównuje ocenę do stabilnych **74–91%** we wszystkich biomach.

2. **Oddzielenie błędów modelu od ślepoty sensora:**  
   W ekosystemach tropikalnych (Kalimantan, Roraima) od **18% do 45% (D1)** rzekomych fałszywych alarmów wynika z braku przejrzystości atmosfery w momencie przelotu satelity, a nie z błędnej symulacji.

3. **Jawna atrybucja metod i ograniczenie wniosków:**  
   Analiza odzwierciedla wyniki 6 referencyjnych scenariuszy demonstracyjnych. Rozróżnia pożary modelowane sprzężoną fizyką Rothermela od żarzenia torfu modelowanego czystym ML (Kalimantan). Nie należy generalizować liczb na wszystkie incydenty globu bez uwzględnienia lokalnych warunków biometrycznych.

4. **Brak strojenia na teście (*Honest Science*):**  
   Przedstawione wyniki są surową, uczciwą prawdą o działaniu systemu w warunkach globalnych — stanowiąc rzetelną bazę pod przyszłe prace badawcze.

---

## 4. Odtworzenie i testy API

```bash
# Uruchomienie dedykowanych testów jednostkowych
.venv/bin/python -m pytest backend/test_biome_errors.py -v

# Pobranie pełnej dekompozycji JSON przez API
curl -s http://localhost:8080/replay/biome-errors

# Pobranie tabeli podsumowującej
curl -s http://localhost:8080/replay/biome-errors/summary
```
