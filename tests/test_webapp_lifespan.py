"""Startup lifespan should degrade gracefully when sandbox config is invalid."""

from __future__ import annotations

import pytest

from agent import webapp

_OPTIONAL_SANDBOX_VARS = (
    "DEFAULT_SANDBOX_SNAPSHOT_FS_CAPACITY_BYTES",
    "DEFAULT_SANDBOX_VCPUS",
    "DEFAULT_SANDBOX_MEM_BYTES",
    "DEFAULT_SANDBOX_IDLE_TTL_SECONDS",
    "DEFAULT_SANDBOX_DELETE_AFTER_STOP_SECONDS",
)


async def test_lifespan_survives_invalid_sandbox_config(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("SANDBOX_TYPE", "langsmith")
    monkeypatch.delenv("DEFAULT_SANDBOX_SNAPSHOT_ID", raising=False)
    with caplog.at_level("ERROR", logger="agent.webapp"):
        async with webapp.lifespan(webapp.app):
            pass
    assert "Sandbox startup config invalid" in caplog.text


async def test_lifespan_clean_with_valid_sandbox_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SANDBOX_TYPE", "langsmith")
    monkeypatch.setenv("DEFAULT_SANDBOX_SNAPSHOT_ID", "snapshot-123")
    for name in _OPTIONAL_SANDBOX_VARS:
        monkeypatch.delenv(name, raising=False)
    async with webapp.lifespan(webapp.app):
        pass
