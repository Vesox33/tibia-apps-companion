# Tibia Apps Companion

Aplikacja na Windows dla graczy Tibii. Pomaga rezerwować respy na **tibialoot.com** i wysyłać hunty na **hunt-analyser.com**, bez przełączania się między kartami przeglądarki.

## Funkcje

**Respy**
- Ulubione respy na jednym ekranie: kto teraz huntuje, do kiedy i jaka jest następna rezerwacja.
- Oś czasu na najbliższą dobę z wolnymi oknami. Termin wybierasz jednym kliknięciem.
- Rezerwacja z kontrolą zasad: maks. 3 h, bez kolizji, maks. dobę do przodu.
- Zakładka „Moje” z Twoimi rezerwacjami i ich usuwaniem.
- Powiadomienie, gdy ulubiony resp się zwolni.

**Snajper**
- Jeden przycisk **Pilnuj** – tryb dobiera się sam z wybranych godzin.
- Termin jeszcze zamknięty: rezerwacja w sekundzie, gdy strona go odblokuje (albo samo powiadomienie, jeśli tak wybierzesz). Może podążać za czyjąś rezerwacją: jeśli ktoś ją skróci albo usunie, termin się przesunie, w ustawionych przez Ciebie widełkach.
- Termin zajęty: dźwięk i powiadomienie, gdy w wybranym przedziale zwolni się miejsce. Opcjonalnie od razu rezerwuje.

**Hunty**
- Przechwytuje „Copy to clipboard” z Hunt, Party Hunt i Input Analyzera.
- Rozpoznaje spot i piętro po zabitych potworach i dobiera template.
- Ostrzega, gdy analizator był już wysłany.
- Prey, XP Boost, Boosted creature i eventy.
- Wypełnia formularz „New hunt session” jednym kliknięciem. Na życzenie od razu zapisuje.

**Dziś** (pasek boczny)
- Boosted creature i boosted boss: obrazek, a po najechaniu nazwa.
- Rashid: po najechaniu mapa okolicy, nazwa miasta i opis, gdzie dokładnie stoi. Zmiana następuje o server save.

**Imbuementy**
- Kalkulator kosztu Vampirism, Void i Strike (Basic / Intricate / Powerful), także dla kilku sztuk naraz.
- Sprawdza wszystkie warianty: wszystko z marketu albo pakiet u Yany za Gold Tokeny (grupa 1, grupy 1–2, grupy 1–3) z resztą z marketu – i pokazuje najtańszy.
- Pamięta ostatnio wpisane ceny. Ceny można wpisywać jak w grze: 1.5k, 45k, 1kk.
- Opłata przy shrine jest zablokowana (wartości z gry); kłódka pozwala ją zmienić, gdyby CipSoft zmienił cennik.

**Całość**
- Jedno logowanie do obu stron, w okienku aplikacji. Aplikacja nie zna Twoich haseł.
- Jedno okno ustawień i wspólny log.
- Automatyczne aktualizacje.

## Wymagania

- Windows 10 lub 11 (64-bit)
- Microsoft Edge, Google Chrome albo Brave (do wysyłania huntów)
- Konta na tibialoot.com i/lub hunt-analyser.com

## Instalacja

1. Pobierz `TibiaAppsCompanion-win64.zip` z [najnowszego wydania](../../releases/latest).
2. Rozpakuj w folderze, w którym masz prawo zapisu, np. `Dokumenty\TibiaAppsCompanion`. Nie wybieraj `Program Files`, bo wtedy aktualizacje nie będą działać.
3. Uruchom `TibiaAppsCompanion.exe`.

Przy pierwszym uruchomieniu Windows może pokazać „System Windows ochronił ten komputer”. Kliknij **Więcej informacji → Uruchom mimo to**. Komunikat pojawia się przy programach bez płatnego certyfikatu podpisu.

## Pierwsze kroki

1. W pasku po lewej kliknij **tibialoot.com** i zaloguj się tak jak na stronie. Zrób to samo dla **hunt-analyser.com**.
2. **Respy → Wszystkie:** kliknij ☆ przy respach, które Cię interesują.
3. Kliknij resp, wybierz wolne okno i kliknij **Zarezerwuj**.
4. **Hunty:** w grze kliknij „Copy to clipboard” w analizatorze. Aplikacja sama go przechwyci.

Wszystkie opcje są w **⚙ Ustawienia**.

## Aktualizacje

Aplikacja sprawdza nowe wersje przy starcie i co 6 godzin. Gdy jakaś jest dostępna, pokazuje listę zmian i po potwierdzeniu sama się aktualizuje. Ustawienia i dane zostają. Możesz to wyłączyć albo sprawdzić ręcznie w **Ustawienia → Aktualizacje**.

Lista spawnów (lokacje, piętra i potwory używane do rozpoznawania huntów) aktualizuje się osobno, raz na dobę, w tle.

## Dane i prywatność

- Wszystko jest zapisywane tylko na Twoim komputerze, w `%APPDATA%\TibiaAppsCompanion\`. Hunty są w podfolderze `hunty\`.
- Aplikacja łączy się wyłącznie z tibialoot.com, hunt-analyser.com i GitHubem (aktualizacje) i api.tibiadata.com (boosted creature i boss). Nie wysyła żadnych statystyk.
- Logowanie odbywa się bezpośrednio na stronach. Aplikacja przechowuje tylko sesję (ciasteczka), tak jak przeglądarka.
- Wysyła do stron te same zapytania co ich własne przyciski i odświeża tablicę nie częściej niż co 30 s.

**Odinstalowanie:** usuń folder aplikacji i `%APPDATA%\TibiaAppsCompanion\`.

## Rozwiązywanie problemów

| Problem | Co zrobić |
|---|---|
| Ciągle „niezalogowano” | Kliknij konto w pasku po lewej i zaloguj się ponownie. Jeśli to nie pomaga: Ustawienia → Dane i log → wyślij `error.log`. |
| Nie wypełnia formularza huntu | Ustawienia → Hunty → wybierz inną przeglądarkę. |
| Aktualizacja się nie udała | Zamknij drugie okno aplikacji i spróbuj ponownie. Upewnij się, że folder aplikacji nie jest w `Program Files`. |
| Antywirus blokuje plik | Dodaj folder aplikacji do wyjątków. |

## Podziękowania

Wycinki mapy przy Rashidzie: [tibiamaps.io / tibia-map-data](https://github.com/tibiamaps/tibia-map-data), dołączone do aplikacji, więc działają offline. Boosted creature i boss: [TibiaData](https://tibiadata.com).

## Zastrzeżenia

Tibia Apps Companion jest niezależnym projektem fanowskim. Nie jest powiązany z CipSoft GmbH, tibialoot.com ani hunt-analyser.com, ani przez nie wspierany. Tibia jest znakiem towarowym CipSoft GmbH. Korzystając z aplikacji, przestrzegaj regulaminów tych stron i zasad Twojej społeczności.
