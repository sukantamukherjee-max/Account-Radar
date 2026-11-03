"""Shared test configuration.

Tests must be hermetic: they never use real provider credentials or make
real search/AI calls, whatever keys happen to be set in the developer's
environment. Each test starts with the provider keys removed; a test that
needs a key sets a dummy one itself with monkeypatch.setenv.
"""

from __future__ import annotations

import pytest

PROVIDER_KEY_VARS = ("BRAVE_SEARCH_API_KEY", "TAVILY_API_KEY", "SERPER_API_KEY", "OPENROUTER_API_KEY")
# Behaviour switches that must not leak in from the developer's environment
# (each test starts from the default People mode, brave_only).
BEHAVIOUR_VARS = ("ACCOUNT_RADAR_PEOPLE_MODE",)


@pytest.fixture(autouse=True)
def _no_real_provider_keys(monkeypatch):
    for var in PROVIDER_KEY_VARS + BEHAVIOUR_VARS:
        monkeypatch.delenv(var, raising=False)
