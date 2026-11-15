# PDF z analizy FireSpot

Przycisk **Pobierz PDF widoku** zapisuje tekst wyników, tabele i dostępne wykresy SVG bieżącej analizy. Jest dostępny w Monitoringu, Prognozie, Historii, Alertach, Odtworzeniu, zharmonizowanym zapisie oraz sekcjach analiz dodatkowych. Przed zakończeniem analizy jest wyłączony. Nie uruchamia modelu ponownie, nie odpytuje OpenStreetMap i nie dokłada nowych danych do wyniku.

To zapis przedstawionych danych. Podkład mapowy i globus nie są dołączane. Prognoza zawiera także tabelę pełnej fizycznej osi czasu. Odtworzenie zachowuje rozdział prognozy wydanej i późniejszego ujawnienia obserwacji: przed ujawnieniem nie eksportuje ukrytych wyników oceny. Raport nie oznacza kalibracji ani potwierdzenia prognozy w terenie.

Osobny **Incident brief** zachowuje dotychczasowe działanie, w tym obliczanie ekspozycji. Jego czas i zawartość mogą się różnić od zapisu widoku.

## Uruchomienie

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
```

Renderer używa Playwright, Chromium i Pillow. Na systemie wymagającym bibliotek systemowych można zainstalować zależności przeglądarki zgodnie z instrukcjami instalatora Playwright. Opcjonalna zmienna `FIRESPOT_CHROMIUM_PATH` wskazuje własny plik wykonywalny Chromium. Brak przeglądarki daje błąd 503; interfejs pozwala ponowić eksport.

## Kontrakt i ograniczenia

`POST /reports/pdf` przyjmuje JSON z `title`, `language` (`pl`/`en`), `generated_at`, `context` (etykieta/wartość) i `blocks` (nagłówek, akapit, tabela lub obraz PNG). Odpowiedź to załącznik `application/pdf` z nazwą pliku. Wszystkie pola tekstowe są escapowane. Nie przyjmuje HTML, SVG ani adresów do pobierania zasobów. Renderer blokuje sieć, wyłącza JavaScript, czyści metadane PNG i ogranicza równoległe renderowanie do dwóch procesów.

Limity: 2 MiB żądania, 120 bloków, 40 par kontekstu, 500 wierszy jednej tabeli, 2500 łącznie, 16 kolumn, 8 obrazów do 500 KiB każdy. Obraz nie może przekraczać 4096 px na wymiar ani 8 megapikseli. Interfejs jawnie sygnalizuje ograniczenie liczby elementów/wierszy w długim widoku; pełne dane pozostają w dotychczasowych eksportach JSON/CSV/GeoJSON.

```bash
.venv/bin/python -m pytest backend/test_analysis_reports.py -q
.venv/bin/python .taste-skill/check_analysis_reports.py
```

Test przeglądarkowy używa zapisanej prognozy i kontrolowanych odpowiedzi analiz dodatkowych. Weryfikuje pobieranie prawdziwych PDF, języki, stany i brak ponownych obliczeń przy eksporcie; nie mierzy nowej trafności modelu.
