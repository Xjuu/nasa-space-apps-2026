# FireSpot: test wyłączenia reprezentowanego kontynentu

Wykonano rzeczywisty retraining pięciu odrębnych modeli: kontrola ze wszystkimi reprezentowanymi grupami oraz cztery modele z wyłączeniem jednej grupy. **To krótka diagnostyka dziennego ML v2, nie benchmark produkcyjnego ML + fizyka ani walidacja globalna.** Nie wymieniono modeli produkcyjnych.

Źródło liczb: `reports/continent_holdout_pilot_2024_20261115.json`; artefakty: `models/continent_holdout/pilot_2024_20261115/`.

## Protokół

- Fitting: 2019–2021. Early stopping: 2022. Dobór progów: 2023. Test: 2024. Wszystkie etykiety danego etapu kończą się przed następnym rokiem.
- Każdy model: **400 przypadków fit, 80 early, 80 policy**. Wykluczenie dotyczy wszystkich trzech etapów, również doboru progów. Kontrola ma identyczny budżet danych, rozdzielony równo między dostępne grupy.
- Test: 20 tych samych przypadków dla obu porównywanych modeli w każdej grupie, **80 łącznie**. Środki przypadków pochodzą z krajowych plików SNPP; lokalna historia i prawda obejmują indeks MODIS/VIIRS.
- Modele XGBoost CPU, 2 wątki, maksymalnie 200 drzew, depth 5, learning rate 0,05; early stop 25. Jeden ustalony seed. Bez wyszukiwania hiperparametrów na teście, bez online learning.
- Próg każdego z dni 1/2/3 dobierany z ustalonej przed testem listy 0,05/0,1/0,15/0,2/0,25/0,3/0,4/0,5 na źródłowych przypadkach policy, maksymalizując średnie IoU po przypadkach.
- Źródłowe grupy: Brazylia jako Ameryka Południowa, Australia jako Oceania, Indie jako Azja, USA i Kanada jako Ameryka Północna. Brak oceny Afryki i Europy; jeden kraj nie reprezentuje całego kontynentu.
- Tylko lokalny cache. Wywołania sieciowe zablokowane programowo. Pogoda: dawny cache ERA5 z brakami pozostawionymi jako NaN, nie archiwalna prognoza wydana przed t0.
- Środki przypadków deduplikowane przez przybliżony blok 28 dni / 10 komórek. To nie są niezależnie zidentyfikowane incydenty i nadal możliwa jest zależność próbek.

Czas pełnego pilota: **52.6 s**, pięć rzeczywistych fitów. Poprzedzający pomiar wykonalności 80/20/20 + 5 testów Australii trwał 10,7 s i jest osobnym raportem.

## Liczebności i odcięcie geograficzne

| Model | Fit według kraju | Early według kraju | Policy według kraju | Drzewa | Brak wiatru w wierszach fit |
|---|---|---|---|---:|---:|
| all_regions_control | Australia: 100, Brazil: 100, India: 100, United_States: 56, Canada: 44 | United_States: 12, Brazil: 20, India: 20, Australia: 20, Canada: 8 | India: 20, Australia: 20, United_States: 8, Brazil: 20, Canada: 12 | 200 | 84.4% |
| South_America | India: 133, Australia: 134, United_States: 67, Canada: 66 | Australia: 27, India: 27, United_States: 16, Canada: 10 | India: 27, United_States: 13, Australia: 27, Canada: 13 | 158 | 81.3% |
| Oceania | Brazil: 134, India: 133, United_States: 69, Canada: 64 | India: 27, United_States: 14, Brazil: 27, Canada: 12 | India: 27, Brazil: 27, United_States: 17, Canada: 9 | 128 | 86.0% |
| Asia | Australia: 133, United_States: 71, Brazil: 134, Canada: 62 | Australia: 27, Brazil: 27, United_States: 16, Canada: 10 | Brazil: 27, Australia: 27, United_States: 11, Canada: 15 | 93 | 89.7% |
| North_America | Australia: 133, India: 133, Brazil: 134 | Australia: 27, India: 26, Brazil: 27 | India: 26, Brazil: 27, Australia: 27 | 77 | 80.7% |

Duży odsetek brakującej pogody ogranicza interpretację: to przede wszystkim test przenoszenia istniejących cech historii i kształtu ognia z dostępną fragmentarycznie pogodą. To nie test nowego providera archiwalnych prognoz. Kontrola osiągnęła limit 200 drzew; nie dowodzi to pełnego zbiegania modelu.

## Wyniki sparowane

W komórkach zapisano **kontrola → model bez ocenianej grupy**. Miary są średnimi po przypadkach, a Brier najpierw średnią po uprawnionych komórkach danego przypadku, potem po przypadkach. Within-5km to tolerancyjny overlap, nie procent poprawnie przewidzianych wszystkich komórek.

| Grupa testowa | Dzień | Strict IoU | Within-5km | Brier (mniej lepiej) | Puste prawdy / 20 |
|---|---:|---:|---:|---:|---:|
| South_America | 1 | 11.04% → 16.75% | 33.50% → 44.05% | 0.04945 → 0.04974 | 3 |
| South_America | 2 | 16.11% → 20.36% | 47.20% → 58.42% | 0.04679 → 0.04696 | 1 |
| South_America | 3 | 16.79% → 17.12% | 54.26% → 58.14% | 0.04659 → 0.04706 | 0 |
| Oceania | 1 | 15.79% → 10.60% | 38.83% → 28.03% | 0.06397 → 0.06638 | 2 |
| Oceania | 2 | 17.42% → 10.95% | 43.61% → 38.05% | 0.05546 → 0.05815 | 2 |
| Oceania | 3 | 13.78% → 11.52% | 45.80% → 43.58% | 0.05218 → 0.05483 | 0 |
| Asia | 1 | 17.83% → 7.73% | 54.64% → 14.11% | 0.07772 → 0.08395 | 1 |
| Asia | 2 | 19.22% → 10.23% | 62.40% → 25.14% | 0.08201 → 0.09167 | 1 |
| Asia | 3 | 22.25% → 24.22% | 68.01% → 68.74% | 0.08487 → 0.09594 | 1 |
| North_America | 1 | 15.53% → 29.47% | 31.33% → 38.22% | 0.03905 → 0.03982 | 5 |
| North_America | 2 | 10.12% → 6.92% | 35.23% → 19.41% | 0.04751 → 0.04825 | 2 |
| North_America | 3 | 9.83% → 11.75% | 37.39% → 37.27% | 0.05208 → 0.05290 | 1 |

Testy krajowe: Brazylia 20, Australia 20, Indie 20, USA 8 i Kanada 12. JSON zawiera precision, recall, mianowniki eligible cells, indywidualne wyniki przypadków i identyczny zestaw testowy użyty w parze modeli.

Przy pustej prawdzie i pustej prognozie IoU i overlap wynoszą 1, zgodnie z konwencją projektu; przy jednym pustym zbiorze wynoszą 0. Precision bez przewidywanych pozytywów i recall bez obserwowanych pozytywów są niezdefiniowane (`null`), nie zamieniane na 100%. Liczby pustych przypadków publikujemy, aby nie ukryć ich wpływu.

## Interpretacja

- Największy spadek w dniu 1 wystąpił dla Indii po wyłączeniu Azji: overlap 54,64% → 14,11%. Ten mały pilot wskazuje ryzyko przenoszenia modelu na niewidzianą geografię; nie wyjaśnia sam mechanizmu błędu.
- Dla Australii overlap dnia 1 spadł 38,83% → 28,03%. W Brazylii i Ameryce Północnej dzień 1 overlap wzrósł, ale inne horyzonty i miary nie wykazują jednolitej poprawy.
- Brier modelu bez grupy był gorszy we wszystkich czterech grupach i trzech horyzontach tego pilota. To inny aspekt niż wynik progowanej maski; wzrost overlap nie oznacza poprawy probabilistycznej.
- Próg zależy od źródłowej kalibracji; zmiana rozkładu treningu może zmienić liczbę wybranych komórek i liczbę pustych prognoz. Lepszy overlap pojedynczego modelu holdout nie jest dowodem, że usunięcie danych geograficznych poprawia model.
- To jeden seed, małe próby i zastępcze grupowanie incydentów, bez wyznaczonej istotności i bez powtarzania całego uczenia na wielu seedach. Nie promujemy tych modeli i nie zmieniamy produkcji.

## Odtworzenie

```bash
.venv/bin/python -m pytest tests/test_continent_holdout.py -q
.venv/bin/python scripts/evaluate_continent_holdout.py \
  --fit-cases 400 --early-cases 80 --policy-cases 80 --test-cases 20 \
  --test-year 2024 --run-id nowy_id_2024
```

`run-id` musi być nowy — skrypt odmawia nadpisania istniejącego katalogu modeli. Niezbędne są istniejące krajowe pliki cache, cache globalnego indeksu i lokalne zależności Python. Skrypt nie pobiera danych.

Raport zawiera SHA256 list przypadków, modeli i metadanych indeksu oraz statystyczny fingerprint plików wejściowych (rozmiar, mtime, liczba wierszy, hash schematu). Fingerprint plików parquet nie jest pełnym hashem ich zawartości. Parametry, kraje i daty przypadków są jawne w metadanych modeli i raporcie.

Walidacja: **10 testów PASS** (separacja grup/krajów, granice etykiet w czasie, duplikaty, równy budżet danych, definicje miar, puste przypadki i źródłowy dobór progów). Osobny rzeczywisty pilot retrainingu wykonany powyższym protokołem.
