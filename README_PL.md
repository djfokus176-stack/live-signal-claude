# CLAUDE LIVE SIGNAL — PROGRAM 2 (V6, tylko PAPER)

Aplikacja działa bez hasła właściciela. Link: https://live-signal-claude.streamlit.app/

Na górze widać bilans 15-minutowych prób: winrate, wygrane,
przegrane, wynik w punktach po spreadzie ASK/BID i dodatkowym
symulowanym koszcie round-trip. Poniżej pełna tabela daty/czasu,
LONG/SHORT, cen wejścia/wyjścia, wyniku i **„Dlaczego taki typ?”**.

V6 nie miesza swojej oceny z wcześniejszymi strategiami; tabela
pokazuje także zachowane starsze testy wraz z wersją modelu.
Wartości na ekranie pochodzą wyłącznie z lokalnej bazy Streamlit.
Brak danych oznacza brak możliwości podania wiarygodnego bilansu.

**Fizyczne warunki wejścia w kodzie:**
- `market_context.py:filtered_decision` – sprawdza wcześniej
  zamknięte M15, H1 i poziomy wsparcia/oporu.
- Ta sama funkcja ma **rzeczywisty warunek if**
  `answer["side"] == "LONG" and value <= -0.25`: silnie ujemny
  sentyment VADER blokuje LONG; analogicznie dodatni blokuje SHORT.
  RSS jest heurystyką tonu, a nie zweryfikowanym zaskoczeniem CPI.
- `macro_calendar.py:macro_guard` sprawdza oficjalny kalendarz
  BLS (.ics) oraz zweryfikowane terminy FOMC i BEA w 2026.
  W ciągu 15 minut przed i po kluczowej publikacji bot daje WAIT.
  Jeśli nie udaje się zweryfikować kalendarza BLS, także daje WAIT
  zamiast niebezpiecznego automatycznego sygnału.
- Świece M15 i H1 są budowane z **tego samego** źródła co bot:
  dla US500 Capital.com DEMO. Widget TradingView służy tylko
  do wizualizacji, a dane z niego nigdy nie są cenami transakcji.
- `app.py` uruchamia `filtered_decision` przed
  `store.log_signal` i przekazuje uzasadnienie do bazy.
- Przeglądanie wykresów, tabeli i wyników nie wymaga logowania.
  Wyłącznie **Capital.com read-only API**, już zapisane prywatnie w
  Streamlit Secrets, jest potrzebne do pobierania prawdziwych świec
  brokera. Dane te nie są pokazywane użytkownikom i nie trafiają
  do repozytorium.

**Ograniczenia:** bot nadal zbiera testy automatycznie tylko przy
aktywnej aplikacji Streamlit, a lokalna baza SQLite może przetrwać
lub zniknąć po restarcie Cloud. Nie ma uruchomionego procesu 24/7
ani trwałego zapisu zewnętrznego. Nigdy nie wysyła prawdziwych
zleceń. Wynik punktowy nie jest ROI i nie oznacza realnego zarobku.
Filtry świec i wiadomości nie gwarantują przewagi.

Testy: `python -m pytest -q`. Nie potrzebują konta ani haseł.
