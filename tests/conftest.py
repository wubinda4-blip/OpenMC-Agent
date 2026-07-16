"""Pytest configuration.

Tests that depend on modules not yet implemented live under ``wip/`` and are
excluded from collection until their backing module ships. Move a test back up
to ``tests/`` once the module it imports exists.
"""

import logging

import pytest

collect_ignore_glob = ["wip/*"]


@pytest.fixture(autouse=True)
def _quiet_openmc_agent_logging():
    """Suppress ``openmc_agent`` INFO/DEBUG progress messages during tests.

    Individual tests that need to assert on log output can raise the level via
    ``caplog.at_level(logging.INFO, logger="openmc_agent")`` or call
    ``configure_logging("INFO")`` explicitly.
    """
    logger = logging.getLogger("openmc_agent")
    old_level = logger.level
    logger.setLevel(logging.WARNING)
    yield
    logger.setLevel(old_level)
