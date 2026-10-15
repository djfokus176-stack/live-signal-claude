"""Regression for AttributeError when Streamlit retains old store.py in RAM."""
import importlib
import pandas as pd
import pytest

import store
from runtime_compat import ensure_store_api, safe_failure_id


def test_current_store_is_compatible_without_reload():
    assert ensure_store_api(store) is store
    assert store.STORE_API_VERSION >= 20261015


def test_reload_of_old_module_recreates_missing_log_scan(monkeypatch):
    # Reproduce the broken live state: app.py is updated, Python store
    # module is cached in RAM from an older deploy without log_scan.
    monkeypatch.delattr(store, "log_scan")
    monkeypatch.setattr(store, "STORE_API_VERSION", 20261014)
    assert not hasattr(store, "log_scan")
    reloaded = ensure_store_api(store)
    assert reloaded is store
    assert callable(reloaded.log_scan)
    assert callable(reloaded.load_scans)
    assert reloaded.STORE_API_VERSION >= 20261015


def test_bad_partial_reload_fails_loudly_not_false_success():
    class OldModule:
        STORE_API_VERSION = 20261014
    with pytest.raises(RuntimeError, match="STARE_MODULY"):
        ensure_store_api(OldModule(), reloader=lambda module: module)


def test_safe_diagnostics_never_echo_exception_message():
    try:
        raise AttributeError("SECRET_TOKEN_VALUE_DO_NOT_DISPLAY",
                             name="log_scan")
    except AttributeError as exc:
        label = safe_failure_id(exc)
    assert "AttributeError:log_scan" in label
    assert "SECRET_TOKEN_VALUE" not in label


def test_actual_app_start_writes_wait_scan_after_stale_store(monkeypatch, tmp_path):
    from streamlit.testing.v1 import AppTest

    # New app.py + old cached store.py was not exercised by earlier CI:
    # Streamlit caught AttributeError in a warning, so smoke-test passed.
    db_path = str(tmp_path / "fresh-after-hotreload.sqlite")
    monkeypatch.setenv("SIGNAL_DB", db_path)
    monkeypatch.delattr(store, "log_scan")
    monkeypatch.setattr(store, "STORE_API_VERSION", 0)
    app = AppTest.from_file("app.py", default_timeout=45).run()
    assert not app.exception
    # Reload should have restored store.log_scan and used this isolated DB.
    assert callable(store.log_scan)
    assert store.DB == db_path
    scans = store.load_scans()
    assert not scans.empty
    assert scans.iloc[0]["direction"] == "WAIT"
    assert scans.iloc[0]["origin"] == "AUTO"
    # The database assertion above verifies the real write; it is stronger
    # than the old smoke test that merely required no uncaught exception.
