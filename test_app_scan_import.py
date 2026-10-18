"""Prevent silent NameError in AUTO/MANUAL scan logging after deploy."""
import ast
from pathlib import Path

import pandas as pd
import store
from runtime_compat import ensure_store_api


def test_app_imports_scan_writer_name_it_calls():
    tree=ast.parse(Path("app.py").read_text(encoding="utf-8"))
    used=any(
        isinstance(x, ast.Call) and isinstance(x.func, ast.Name)
        and x.func.id == "ensure_store_api" for x in ast.walk(tree))
    imports=any(
        isinstance(x, ast.ImportFrom) and x.module == "runtime_compat"
        and any(a.name == "ensure_store_api" for a in x.names)
        for x in ast.walk(tree))
    assert used, "App no longer invokes scan compatibility guard"
    assert imports, "Missing import silently breaks AUTO/MANUAL audit"


def test_scan_writer_reachable_and_persists_wait(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB", str(tmp_path / "test.sqlite"))
    now=pd.Timestamp("2026-10-18T20:37:00Z")
    writer=ensure_store_api(store).log_scan
    assert writer("CAPITAL_US500", "Capital.com DEMO", now, "AUTO",
                  {"side":"WAIT","reason":"weekend / stale broker",
                   "bars":450,"age_min":2856.7,"reference":None,
                   "score_long":50}, "WAIT")
    scans=store.load_scans("CAPITAL_US500")
    assert len(scans)==1
    assert scans.iloc[0]["direction"]=="WAIT"
    assert scans.iloc[0]["model"]=="ema-rsi-mtf-macro-v6"
