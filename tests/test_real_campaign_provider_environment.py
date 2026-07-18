"""Real campaign provider environment detection tests."""

import os
from unittest.mock import patch

import pytest

from openmc_agent.real_campaign_harness import (
    ProviderEnvironmentStatus,
    detect_provider_environment,
)


def test_detect_returns_provider_environment_status():
    status = detect_provider_environment("fake:test")
    assert isinstance(status, ProviderEnvironmentStatus)
    assert status.model == "fake:test"


def test_detect_picks_up_ds_provider_prefix():
    status = detect_provider_environment("ds:deepseek-v4-flash")
    assert status.provider == "ds"


def test_detect_picks_up_deepseek_provider_prefix():
    status = detect_provider_environment("deepseek:deepseek-chat")
    assert status.provider == "deepseek"


def test_detect_picks_up_zhipu_provider_prefix():
    status = detect_provider_environment("zhipu:glm-5")
    assert status.provider == "zhipu"


def test_detect_uses_provider_specific_api_key_env_not_hardcoded_deepseek():
    """DEEPSEEK_API_KEY must NOT be hardcoded as the only check — the
    provider's own api_key_env decides."""
    # ds prefix → SENSENOVA_API_KEY, not DEEPSEEK_API_KEY.
    with patch.dict(os.environ, {"SENSENOVA_API_KEY": "x"}, clear=False):
        os.environ.pop("DEEPSEEK_API_KEY", None)
        status = detect_provider_environment("ds:deepseek-v4-flash")
        assert status.api_key_env == "SENSENOVA_API_KEY"
        assert status.api_key_present is True


def test_detect_flags_missing_api_key():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("SENSENOVA_API_KEY", None)
        status = detect_provider_environment("ds:deepseek-v4-flash")
        assert status.api_key_present is False
        assert "BLOCKED_BY_LLM_ENVIRONMENT" in status.blocked_reasons()


def test_blocked_reasons_when_api_key_missing():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("SENSENOVA_API_KEY", None)
        status = detect_provider_environment("ds:deepseek-v4-flash")
        assert status.llm_environment_available is False


def test_openmc_smoke_environment_available_requires_both():
    status = ProviderEnvironmentStatus(
        provider="ds", model="ds:x", api_key_env="SENSENOVA_API_KEY",
        api_key_present=True,
        openmc_library_present=True,
        openmc_cross_sections_present=False,
        openmc_cross_sections_path="",
        openmc_version="0.15.3", endpoint="",
    )
    assert status.openmc_environment_available is False
    assert status.openmc_smoke_environment_available is False
    assert "BLOCKED_BY_OPENMC_ENVIRONMENT" in status.blocked_reasons()
    assert "BLOCKED_BY_CROSS_SECTIONS_ENVIRONMENT" in status.blocked_reasons()
