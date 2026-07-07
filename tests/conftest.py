"""Pytest configuration.

Tests that depend on modules not yet implemented live under ``wip/`` and are
excluded from collection until their backing module ships. Move a test back up
to ``tests/`` once the module it imports exists.
"""

collect_ignore_glob = ["wip/*"]
