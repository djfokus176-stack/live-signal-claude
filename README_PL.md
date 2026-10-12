# Live Signal AI Test — osobny projekt (PAPER)

To jest niezależna wersja programu Claude, z poprawkami do logowania i rozliczania sygnałów. Nie modyfikuje poprzedniego projektu **SP500 Signal AI Pro**. Nie zawiera automatycznego handlu.

## Najłatwiej: GitHub + Streamlit

1. Na GitHub utwórz **NOWE** repozytorium `live-signal-ai-test` (nie edytuj starego).
2. Rozpakuj archiwum ZIP na komputerze.
3. GitHub → nowe repo → **Add file → Upload files**. Prześlij pliki z rozpakowanego folderu do katalogu głównego repo (`app.py`, `store.py`, `data_sources.py`, `requirements.txt`, `test_store.py`, `README_PL.md`, `.gitignore`). Nie wysyłaj kluczy ani bazy `signals_test.db`.
4. [Streamlit Community Cloud](https://share.streamlit.io/) → **Create app** → wybierz nowe repo `live-signal-ai-test`, branch `main` i plik `app.py` → Deploy.
5. Uruchom najpierw **SPY / Yahoo**, bez kluczy, żeby zweryfikować wygląd. Uwaga: poza sesją giełdową może pojawić się **WAIT**, prawidłowo.

## Klucze w Streamlit Secrets — opcjonalnie

W **App settings → Secrets** dodaj tylko te pola, z których chcesz korzystać:

```toml
ALPACA_KEY_ID = "TWOJ_KLUCZ"
ALPACA_SECRET_KEY = "TWOJ_SEKRET"
OPENAI_API_KEY = "TWOJ_KLUCZ_OPENAI"

# Wyłącznie konto DEMO Capital.com (nie LIVE):
CAPITAL_API_KEY = "TWOJ_KLUCZ_DEMO"
CAPITAL_IDENTIFIER = "LOGIN_DEMO"
CAPITAL_PASSWORD = "HASLO_API_DEMO"
CAPITAL_EPIC = "EPIC_INSTRUMENTU_Z_PANELU_CAPITAL"
```

**Nie dodawaj kluczy do plików przesyłanych na GitHub.** W panelu Capital.com najpierw znajdź dokładny `EPIC` danego CFD (nie zgadujemy nazwy!). W Capital.com klucze mogą mieć uprawnienia handlowe, dlatego używaj wyłącznie danych DEMO. Integracja Capital.com została sprawdzona testami jednostkowymi formatu świec, nie rzeczywistym logowaniem.

## Local Windows (trwały plik SQLite na własnym komputerze)

W terminalu uruchomionym w folderze programu:

```bat
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m pytest -q
streamlit run app.py
```

Dziennik zapisuje się lokalnie do `signals_test.db`. Przy normalnym zamknięciu programu baza zostaje na dysku. Praca w tle na własnym PC wymaga pozostawienia uruchomionego Streamlit i aktywnej sesji przeglądarki. **Na Streamlit Community Cloud dane w lokalnym SQLite mogą zniknąć przy restarcie**. Jeśli wyniki mają się zbierać w sposób niezależny od otwartej karty i przetrwać restarty, potrzebna będzie zewnętrzna baza (np. Supabase) i harmonogram skanowania (np. GitHub Actions).

## Poprawki wobec oryginału Claude

- Oddzielne wyniki dla źródeł Yahoo / Alpaca IEX / Capital.com DEMO i instrumentów (SPY ETF ≠ US500 CFD).
- Status `TRAFIONY` ustalany według **wyniku netto**, nie brutto.
- Koszt przypięty do sygnału w chwili jego zapisu; nie zmienia historycznych wyników po zmianie suwaka.
- Nie używamy przyszłych, niedokończonych świec; wejście na następnej świecy, horyzont liczony od utworzenia sygnału.
- Capital.com DEMO opcjonalne: cena wykonania LONG na ask/bid, SHORT na bid/ask, co uwzględnia spread CFD w testach. Dodatkowy koszt suwaka oznacza pozostałe koszty/poślizg.
- GPT tylko po zaznaczeniu checkboxa, żeby uniknąć nieoczekiwanych opłat.
- Suma wyników nie jest ROI inwestora ani wynikiem portfela: horyzonty nakładają się, brak rzeczywistej wielkości pozycji.
- Testy jednostkowe obejmują błędy kosztów, rozdzielenie źródeł, przyszłe dane i CFD.

### Ograniczenia

To nadal tylko prosta heurystyka EMA + nagłówki, **nie wytrenowany i skalibrowany model predykcyjny**. Żaden z adapterów nie został w tym środowisku zweryfikowany prawdziwymi danymi z Alpaca / Capital.com / Yahoo. Streamlit Cloud nie uruchomi dziennika stale przy zamkniętej stronie. Wyniki nie powinny decydować o transakcjach na prawdziwe pieniądze.
