"""Central logging configuration for the openmc_agent package.

All runtime progress messages (``[node:...]``, ``[llm] ...``, ``[normalize] ...``)
go through the standard Python ``logging`` module under the ``openmc_agent``
logger.  Call :func:`configure_logging` from any CLI entry point to wire up a
``StreamHandler(stderr)`` so messages reach the terminal.

Visibility is controlled by logging level:

* ``INFO``    – default; shows all node/LLM progress
* ``WARNING`` – suppresses progress, shows only warnings/errors
* ``DEBUG``   – adds extra diagnostic detail

The level can be set via the ``--log-level`` CLI flag or the
``OPENMC_AGENT_LOG_LEVEL`` environment variable.
"""

from __future__ import annotations

import logging
import os
import sys

LOGGER_NAME = "openmc_agent"

_DEFAULT_LEVEL = "INFO"
_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def configure_logging(level: str | None = None) -> None:
    """Configure the ``openmc_agent`` logger with a stderr handler.

    Parameters
    ----------
    level:
        Logging level name (``"DEBUG"``, ``"INFO"``, ``"WARNING"``, …).
        When *None*, falls back to the ``OPENMC_AGENT_LOG_LEVEL`` environment
        variable, then to ``"INFO"``.
    """
    if level is None:
        level = os.environ.get("OPENMC_AGENT_LOG_LEVEL", _DEFAULT_LEVEL)
    level = level.upper()
    if level not in _VALID_LEVELS:
        level = _DEFAULT_LEVEL

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.propagate = True
