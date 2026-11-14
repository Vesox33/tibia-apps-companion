# Rozwój i wydawanie wersji

## Uruchomienie ze źródeł

Wymagany Python 3.10+.

- `uruchom.bat` – tworzy `.venv`, instaluje zależności i uruchamia aplikację.
- Ręcznie: `pip install -r requirements.txt`, potem `python main.py`.
- Testy: `pip install pytest`, potem `python -m pytest tests` (parser, spawny i wypełnianie formularza na atrapie strony).

## Struktura

| Plik | Co robi |
|---|---|
| `main.py` | punkt wejścia |
| `app.py` | okno główne: powłoka, respy, snajper, logowanie |
| `theme.py` | wygląd w jednym miejscu: kolory, czcionki, skala odstępów (4/8/12/16/24), ikony wektorowe, arkusz stylów, wspólne elementy (przyciski, stany puste, okna komunikatów). Inne pliki nie wpisują kolorów na sztywno. |
| `settings_ui.py` | okno ustawień |
| `api.py`, `parser.py` | zapytania do tibialoot.com i parsowanie tablicy |
| `slots.py` | wolne okna i walidacja terminów |
| `sniper.py` | snajp i obserwacja |
| `imbu.py`, `imbu_data.py` | kalkulator imbuementów (strona) i dane materiałów / opłat |
| `imbu_icons.py`, `tools/fetch_imbu_icons.py` | ikonki imbuementów i materiałów: `assets/imbu/` → cache → pobranie w tle; skrypt dołącza je do wydania |
| `today.py` | sekcja „Dziś”: boosted (TibiaData API), Rashid i mapka |
| `assets/maps/`, `tools/make_rashid_maps.py` | wycinki mapy przy Rashidzie (tibiamaps.io) i skrypt do ich odtworzenia |
| `sounds.py` | dźwięki i głośność |
| `paths.py` | folder danych i migracja ze starych lokalizacji |
| `updater.py`, `version.py` | aktualizacje z GitHub Releases |
| `huntuploader/` | moduł huntów (Playwright, analizatory, spawny, historia) |
| `assets/` | ikona |
| `tools/release.py` | budowanie i publikacja wydania |
| `tools/scrape_spawns.py`, `tools/publish_spawns.py` | zbieranie i publikacja listy spawnów |
| `huntuploader/data/spawns.json` | lista spawnów (dołączana do aplikacji i pobierana z repozytorium) |

## Budowanie .exe

`zbuduj_exe.bat` tworzy `dist\TibiaAppsCompanion\TibiaAppsCompanion.exe`. Dystrybuuje się cały folder, nie sam plik .exe.

## Ostrzeżenia antywirusa i SmartScreen

Build ogranicza fałszywe alarmy na dwa sposoby:
- `--noupx`: plik .exe nie jest pakowany UPX-em, a to najczęstszy powód alarmów.
- `--version-file`: plik .exe ma metadane, czyli nazwę, wersję i opis.

Komunikat „System Windows ochronił ten komputer” (SmartScreen) pojawia się przy każdym niepodpisanym pliku od nowego wydawcy. Trwale znika dopiero po **podpisaniu** pliku certyfikatem:
- **Azure Trusted Signing**: ok. 10 USD/mies., działa dla osób prywatnych z weryfikacją tożsamości. Najtańsza oficjalna droga.
- **SignPath Foundation**: darmowy podpis dla projektów open source, wymaga zgłoszenia projektu.
- Klasyczny certyfikat OV/EV od CA: od ok. 200 USD/rok.

Bez podpisu pomaga:
- Zgłoszenie każdego wydania jako fałszywego alarmu do Microsoftu: https://www.microsoft.com/wdsi/filesubmission. Wybierz „Software developer” i plik .zip z wydania.
- Reputacja SmartScreen, która rośnie z liczbą pobrań tego samego pliku.

## Wydanie nowej wersji

**Jednorazowo:**
1. Zainstaluj [Git](https://git-scm.com/download/win) i [GitHub CLI](https://cli.github.com/).
2. Uruchom `gh auth login`.

**Każde wydanie:**
1. Dopisz zmiany do `CHANGELOG.md` pod nagłówkiem `## [X.Y.Z] – data`.
2. Uruchom `wydaj_wersje.bat`.

Skrypt przy pierwszym wydaniu zakłada publiczne repozytorium i wpisuje je do `version.py`. Potem podbija wersję, buduje `.exe`, pakuje go do `TibiaAppsCompanion-win64.zip`, tworzy tag i publikuje wydanie z opisem z `CHANGELOG.md`.

Zainstalowane kopie aplikacji wykryją nowe wydanie przy starcie albo w ciągu 6 h.

**Repozytorium musi być publiczne**, bo aplikacja pobiera wydania bez logowania. Kod źródłowy jest wtedy widoczny. Do repozytorium nie trafiają żadne dane użytkownika, bo te są w `%APPDATA%`.

## Aktualizacja listy spawnów

Lista spawnów jest aktualizowana osobno od aplikacji, bez wydawania nowej wersji.

1. Uruchom `wydaj_spawny.bat`.
2. Skrypt zbiera dane z tibiadraptor.com. Trwa to kilka minut. Opcja `--show` pokazuje okno przeglądarki.
3. Skrypt pokazuje zmiany: nowe, usunięte i zmienione lokacje. Wpisz `t`, żeby opublikować.
4. Plik `huntuploader/data/spawns.json` zostaje zapisany w repozytorium (commit „Spawny <wersja>” i push).

Zainstalowane aplikacje sprawdzają `raw.githubusercontent.com/<repo>/main/huntuploader/data/spawns.json` raz na dobę, a ręcznie przez Ustawienia → Dane i log → Aktualizuj teraz. Pobierają plik tylko wtedy, gdy jego wersja jest nowsza od tej, którą mają.

Zabezpieczenia:
- Nowa lista jest sprawdzana pod kątem poprawności formatu.
- Jeśli lokacji albo potworów jest o ponad 15% mniej niż w obecnej liście, skrypt nic nie publikuje. Taki spadek zwykle oznacza, że strona się zmieniła albo nie wczytała. Opcja `--force` pozwala opublikować mimo to.
- Publikować może tylko ktoś z prawem zapisu do repozytorium, czyli domyślnie tylko właściciel. Nie dodawaj współpracowników, jeśli tylko Ty masz aktualizować spawny.

Każde wydanie aplikacji zawiera aktualny plik spawnów, więc nowe instalacje od razu mają najnowszą listę.

Przydatne opcje:
- `python tools/scrape_spawns.py --csv` tylko zbiera dane i zapisuje `dist/spawns.new.json` oraz pliki CSV w `dist/spawny/`.
- `tools/publish_spawns.py --from-file dist/spawns.new.json` publikuje wcześniej zebrany plik.

## Jak działa aktualizacja

1. Aplikacja pobiera `releases/latest` z API GitHuba i porównuje numer wersji z `version.py`.
2. Pobiera paczkę: `.exe` przy wersji zbudowanej, kod źródłowy przy uruchamianiu z Pythona. Rozpakowuje ją do `%TEMP%`.
3. Uruchamia skrypt, który czeka na zamknięcie aplikacji, kopiuje nowe pliki (`robocopy /E`), uruchamia aplikację ponownie i sprząta po sobie.
4. Jeśli kopiowanie się nie uda, przy następnym starcie aplikacja pokazuje komunikat.
