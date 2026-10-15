"""Recover Streamlit's stale imported store module after a GitHub redeploy.

Streamlit reruns app.py, but Python may retain an earlier imported store
module in sys.modules. A new UI can then call a method not yet defined
in the process memory (AttributeError). We refresh only if the store
API is outdated, keeping identical DB/model names and old PAPER data.
"""
from __future__ import annotations

import importlib

MINIMUM_VERSION = 20261015
REQUIRED = ("log_scan", "load_scans", "log_signal", "settle", "load")


def _compatible(module):
    return (getattr(module, "STORE_API_VERSION", 0) >= MINIMUM_VERSION
            and all(callable(getattr(module, key, None)) for key in REQUIRED))


def ensure_store_api(module, reloader=None):
    """Return currently deployed store code; fail visibly if reload fails."""
    if _compatible(module):
        return module
    importlib.invalidate_caches()
    module = (reloader or importlib.reload)(module)
    if not _compatible(module):
        raise RuntimeError("STARE_MODULY: Restart aplikacji wymagany")
    return module
