import pytest


@pytest.fixture(autouse=True)
def _no_real_gemini_key(monkeypatch):
    """Tests must never call the real Gemini API, even on a machine that has a key set."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
