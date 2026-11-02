"""Global test configuration and fixtures."""

import pytest


@pytest.fixture(autouse=True)
def disable_live_llm_during_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure automated unit and robustness tests run offline without consuming external API rate limits."""
    monkeypatch.setenv("USE_LLM", "false")
