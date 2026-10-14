# CLAUDE LIVE SIGNAL — PROGRAM 2 (V5 AUTO PAPER)

**To osobny program z repozytorium `live-signal-claude`. Nie edytuje
`SP500-SIGNAL-AI-PRO` (PROGRAM 1).**

## Co działa w V5

- TradingView: duże świece **US500 CFD** (domyślnie), **SPY**, **QQQ** albo **ES futures**,
  interwały 1/3/5/15/30/60 min. Wykres z TradingView może mieć opóźnienie
  i **nie** jest źródłem danych wejściowych modelu.
- **AUTO PAPER co 15 minut jest domyślnie włączony**, ale działa **wyłącznie\n  przy otwartej/aktywnej stronie Streamlit**. Można go wyłączyć w lewym panelu.\n  Rozliczenia PAPER nie oznaczają rzeczywistego handlu.\n- Osobny panel: **ANALIZUJ TERAZ — LONG czy SHORT?** Czyta wyłącznie zamknięte
  minutowe świece z Capital.com DEMO (US500) lub Alpaca IEX / Yahoo (SPY/QQQ).
- Tylko jeśli świeże co najmniej 60 świec M1 są dostępne, pokazuje
  hipotezę LONG/SHORT/WAIT, EMA9/EMA21, RSI14 i momentum 5 min.
- Wypisuje **indeks kierunku 0–100 — to NIE są procentowe szanse wygranej**.
  Nie używaj do prawdziwych transakcji przed wiarygodnym testem.
- Kliknięcie przy świeżym LONG lub SHORT tworzy jeden testowy moment i zapisy
  na 1/3/5/15/30/60 min. Kliknięcie WAIT niczego nie obstawia.\n  Ponowne kliknięcie w czasie blokady pokazuje kierunek, ale NIE ogłasza\n  nieistniejącego nowego zapisu.
  **Każdy kolejny LONG lub SHORT** w tej samej kombinacji instrument/źródło/model\n  ma 15-minutową blokadę, nawet gdy kierunek się odwróci. To ogranicza\n  dublowanie tej samej obserwacji w testach.
- Przed prognozą sprawdzamy 60 kolejnych zamkniętych świec M1 oraz poprawność\n  cen, a brak ciągłości lub nieświeże dane oznaczają WAIT.\n- Po odświeżeniu zamknięte świece służą do rozliczenia: TRAFIONY/BLEDNY,
  BRAK_DANYCH, wynik netto po spreadzie CFD i dodatkowych kosztach.
- Dziennik CSV na żądanie i **opcjonalne przywracanie eksportu CSV** po\n  restarcie bez duplikowania zapisów. Wiadomości RSS (opcjonalnie płatna analiza
  nagłówków przez OpenAI) jako **kontekst**, a nie predyktor.
- Model V3 jest oddzielony od V2; **stare wyniki nadal zostają w starym modelu**,
  nie mieszamy ich w jednym rankingu.

## Jak otworzyć

Otwórz istniejącą aplikację `live-signal-claude` w Streamlit.
Nie twórz nowego repo ani nie zmieniaj `sp500-signal-ai-pro`.
Naciśnij Ctrl+F5 po wdrożeniu V5.\nNie zmieniaj kluczy Capital.com — jeśli wcześniej wyświetlało świeże dane,\npozostaw te same Secrets.

### Capital.com DEMO — tylko dla US500

Streamlit Community Cloud → **live-signal-claude** → Manage app →
Settings → Secrets:

```toml
CAPITAL_API_KEY = "KLUCZ_WLASNEGO_KONTA_DEMO"
CAPITAL_IDENTIFIER = "LOGIN_DEMO"
CAPITAL_PASSWORD = "OSOBNE_HASLO_UTWORZONE_DLA_API"
CAPITAL_EPIC = "US500"
```

Alpaca (tylko SPY/QQQ) opcjonalnie:

```toml
ALPACA_KEY_ID = "KLUCZ"
ALPACA_SECRET_KEY = "SEKRET"
```

**Sekrety są osobne w każdej aplikacji Streamlit. Nigdy nie wpisuj
ich w repozytorium GitHub ani nie przesyłaj na czacie.**
Capital.com REST DEMO nie wykonuje żadnych zleceń.

## Znane ograniczenia

1. TradingView może odświeżać świece we własnym rytmie; sygnały bota
   bazują na **innym** źródle i **zamkniętych** świecach.
2. W chmurze błąd `HTTP 401` wymaga sprawdzenia wpisów Secrets tej
   aplikacji, konta DEMO i osobnego hasła API. Bez rzeczywistych sekretów
   nie można potwierdzić logowania.
3. **SQLite na darmowym Streamlit Cloud nie gwarantuje trwałości po restarcie.**
   Zapis sygnałów oraz ich automatyczne rozliczanie wymagają otwartej aplikacji.
   Do pracy 24/7 i niezawodnego dziennika potrzebne są **osobny harmonogram**
   oraz **zewnętrzna baza** (np. Supabase), których V3 jeszcze nie posiada.
   Pobieraj CSV przed restartami. Jeśli restart już usunął SQLite, wybierz\n   sekcję **Przywróć dziennik z pobranego CSV** i zatwierdź import.\n   Funkcja odtwarza tylko wcześniej zapisane wiersze i nie tworzy\n   żadnych typów wstecz.
4. Ocena EMA/RSI/momentum **nie jest wytrenowanym ani skalibrowanym AI**.
   Samo 100 rozliczeń nie oznacza rentowności; sprawdzaj koszty,
   stabilność na nieznanych danych i dłuższą serię.
5. Nie przeliczaj sum % z wielu nakładających się horyzontów na ROI portfela.

## Testy

```bash
python -m pip install -r requirements.txt
python -m pytest -q
streamlit run app.py
```

Testy lokalne i CI korzystają z udawanych cen, bez autoryzacji do prawdziwego API.

## Audyt pierwotny (13.10.2026)

- Cały istniejący projekt został sprawdzony pod kątem spójności danych
  i wyników PAPER. Model V4 nie używa niedokończonych świec, nie łączy
  instrumentów ani danych różnych dostawców.
- Wyeliminowano błędną możliwość rozliczania pozycji, która miałaby
  wejście dopiero po czasie docelowego wyjścia; pilnujemy również
  zduplikowanych kliknięć i błędnych cen.
- Wskaźniki nie oceniają 60 minut spójnego trendu, jeśli w ostatnich
  60 świecach jest dziura (np. zmiana sesji): wtedy uczciwie WAIT.
- Zmieniono opis LONG/SHORT: odróżnia *wskazanie bota* od
  *rzeczywiście zapisanego* nowego testu PAPER.
- Uruchamiane testy jednostkowe i Streamlit AppTest są symulacjami. Nie
  dowodzą poprawności działania w brokerze, ani dodatniej oczekiwanej wartości.
- **Bez nowego dostępu do prywatnych danych Capital.com poza Streamlit
  nie można uruchomić poza Streamlit odrębnego całodobowego bota dla US500.**
  Program nie przenosi haseł automatycznie do GitHub Actions i
  nigdy nie pokazuje ich w aplikacji.
- Dziennik SQLite w Streamlit pozostaje nietrwały; bieżące dane pobieraj
  przez przycisk eksportu. Nie określamy tego jako trwałego archiwum.

## Co zostało poprawione 14.10.2026 — V5 AUTO PAPER

- Uruchomiono **opcjonalną automatyzację PAPER co 15 minut w czasie aktywnej\n  sesji przeglądarki** (bez płatnego OpenAI API). Domyślnie włączona,\n  wyłączana przełącznikiem. Start skanowania w chwili otwarcia strony,\n  dalej maksymalnie raz na 15 minut. To nie jest osobny proces 24/7.\n- Bot zapisuje tylko kierunki LONG/SHORT przy poprawnych, świeżych świecach.\n  WAIT jest komunikatem bez nowej transakcji PAPER.\n- W dzienniku pojawiło się **AUTO** / **MANUAL**. Starsze wersje\n  bazy/plików CSV otrzymują oznaczenie MANUAL bez zmiany starych wyników.\n- Limit jednego testowego wejścia na 15 minut w obrębie instrumentu, źródła\n  i wersji modelu działa także przy natychmiastowym zwrocie LONG→SHORT.\n- Oczekujące testy V3/V4 są ponownie sprawdzane, jeśli dostępne są historyczne\n  świece do ich uczciwego rozliczenia. Nie rekonstruujemy cen, których\n  źródło nie udostępnia. Starsze wyniki wciąż pozostają w osobnych tabelach.\n- CI bada także automat 15-minutowy, blokady, przywracanie starszych CSV\n  i rozliczanie oczekujących testów ze wcześniejszych modeli.\n\n**Ograniczenie:** ta zmiana nie pozwala na zbieranie danych przy zamkniętej\nprzeglądarce i nie rozwiązuje utraty SQLite po restarcie Streamlit.\nNie przenosimy sekretów Capital.com do GitHub Actions. Do prawdziwego 24/7\nkonieczny byłby osobny bezpieczny harmonogram i trwały magazyn; nie są\njeszcze uruchomione i nie należy twierdzić inaczej.\n
## 14.10.2026 — bezpłatna automatyzacja bez otwartej przeglądarki

**Osobny skaner GitHub Actions dla kontraktu ES=F (publiczne Yahoo),
NIE dla CFD US500 Capital.com.** GitHub uruchamia go co około 15 min
(w dni robocze, UTC) także wtedy, gdy Streamlit nie jest otwarty.
Godzina nie jest gwarantowana: zaplanowane workflow mogą być opóźniane
lub pomijane przez GitHub, a darmowy Yahoo może być chwilowo niedostępny.
Nie ma tu żadnych sekretów i żadnego płatnego API OpenAI.

- Harmonogram: `.github/workflows/public_futures_paper.yml`.
- Silnik: `github_paper_worker.py` — ten sam heurystyczny
  `signal_logic.evaluate`, ale odrębne oznaczenie modelu
  `ema-rsi-v5-auto-futures-github`.
- Trwały zapis: publiczne pliki tygodniowe
  `data/auto_futures/ES_F_RRRR-Www.csv` i `status.json`.
  Zapis jest w repo GitHub, nie w nietrwałym SQLite Streamlit.
- Jeden moment testowy LONG/SHORT jest rozliczany na 1/3/5/15/30/60 min;
  przy braku danych lub rynku WAIT / NO DATA. Żadnych realnych zleceń.
- Rozliczane są sygnały z ostatnich dwóch tygodni w miarę dostępności
  minutowych świec. Starsze tygodnie pozostają w historii repo.
  Testy przy granicy tygodnia są kontynuowane w kolejnym tygodniu.
- Na dole istniejącej aplikacji Claude widać niezależny panel
  „GitHub AUTO PAPER — historia bez otwartej strony”,
  podgląd ostatniego skanu, statystyki i eksport CSV.

### Koszt i prywatność

GitHub Actions standardowych runnerów w **publicznym** repozytorium jest
zgodnie z zasadami GitHub bezpłatny. Publiczne są też wyniki PAPER;
żadnych loginów, kluczy ani danych konta nie zapisujemy do repozytorium.
Wersja nie korzysta z OpenAI API, czyli nie zużywa Twojego salda 9,99 USD.

### Czego ta automatyzacja NIE robi

- **Nie pobiera notowań Capital.com przez GH Actions**. Klucze są
  tylko w oddzielnych Streamlit Secrets, których GitHub nie posiada.
- **Nie porównuj kursu ES=F z kursem US500 CFD 1:1.** Futures
  i CFD mają różne kwotowania, godziny i koszty.
- **Nie działa niezawodnie 24/7.** GitHub może opóźniać/wyłączać
  zaplanowane uruchomienia, Yahoo może ograniczać dostęp, a w weekend
  rynek futures jest zamknięty. Nie ma płatnego SLA.
- Gdy zechcesz rejestrować dokładnie `Capital.com DEMO US500`
  niezależnie od otwartej strony, konieczne jest **jednorazowe**
  skonfigurowanie prywatnych `CAPITAL_*` w środowisku uruchamiającym
  skaner, np. GH Secrets, wraz z odpowiednim reżimem ochrony danych.
  Nie zrobię tego przez kopiowanie haseł do publicznego repo ani czatu.

### Weryfikacja

```bash
python -m pytest -q
# Po mergu: GitHub → Actions → CLAUDE PROGRAM 2 - Auto PAPER Futures (public)
# → Run workflow, lub poczekaj na pierwszy harmonogram.
```

Seria publicznego ES=F zawsze pozostaje **oddzielna** od
ręcznych/aktywnych testów CFD i od wyników PROGRAMU 1.
