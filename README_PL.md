# CLAUDE LIVE SIGNAL — PROGRAM 2 (V4 AUDYT, PAPER)

**To osobny program z repozytorium `live-signal-claude`. Nie edytuje
`SP500-SIGNAL-AI-PRO` (PROGRAM 1).**

## Co działa w V4 (po audycie)

- TradingView: duże świece **US500 CFD** (domyślnie), **SPY**, **QQQ** albo **ES futures**,
  interwały 1/3/5/15/30/60 min. Wykres z TradingView może mieć opóźnienie
  i **nie** jest źródłem danych wejściowych modelu.
- Osobny panel: **ANALIZUJ TERAZ — LONG czy SHORT?** Czyta wyłącznie zamknięte
  minutowe świece z Capital.com DEMO (US500) lub Alpaca IEX / Yahoo (SPY/QQQ).
- Tylko jeśli świeże co najmniej 60 świec M1 są dostępne, pokazuje
  hipotezę LONG/SHORT/WAIT, EMA9/EMA21, RSI14 i momentum 5 min.
- Wypisuje **indeks kierunku 0–100 — to NIE są procentowe szanse wygranej**.
  Nie używaj do prawdziwych transakcji przed wiarygodnym testem.
- Kliknięcie przy świeżym LONG lub SHORT tworzy jeden testowy moment i zapisy
  na 1/3/5/15/30/60 min. Kliknięcie WAIT niczego nie obstawia.\n  Ponowne kliknięcie w czasie blokady pokazuje kierunek, ale NIE ogłasza\n  nieistniejącego nowego zapisu.
  Powtórzony kierunek w tej samej kombinacji symbol/źródło/model ma 15-minutową blokadę.
- Przed prognozą sprawdzamy 60 kolejnych zamkniętych świec M1 oraz poprawność\n  cen, a brak ciągłości lub nieświeże dane oznaczają WAIT.\n- Po odświeżeniu zamknięte świece służą do rozliczenia: TRAFIONY/BLEDNY,
  BRAK_DANYCH, wynik netto po spreadzie CFD i dodatkowych kosztach.
- Dziennik CSV na żądanie i **opcjonalne przywracanie eksportu CSV** po\n  restarcie bez duplikowania zapisów. Wiadomości RSS (opcjonalnie płatna analiza
  nagłówków przez OpenAI) jako **kontekst**, a nie predyktor.
- Model V3 jest oddzielony od V2; **stare wyniki nadal zostają w starym modelu**,
  nie mieszamy ich w jednym rankingu.

## Jak otworzyć

Otwórz istniejącą aplikację `live-signal-claude` w Streamlit.
Nie twórz nowego repo ani nie zmieniaj `sp500-signal-ai-pro`.
Naciśnij Ctrl+F5 po wdrożeniu V4.\nNie zmieniaj kluczy Capital.com — jeśli wcześniej wyświetlało świeże dane,\npozostaw te same Secrets.

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

## Wynik audytu (13.10.2026)

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
