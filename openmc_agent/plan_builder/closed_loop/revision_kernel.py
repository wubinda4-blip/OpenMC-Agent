"""Shared revision invariants documented for gate-specific evaluators.

Gate evaluators retain their own schema and ownership rules, while this small
module centralizes the non-negotiable transactional contract for callers.
"""

TRANSACTIONAL_REVISION_STEPS = (
    "proposal", "clone", "schema_validate", "deterministic_preflight",
    "independent_rereview", "atomic_commit",
)

__all__ = ["TRANSACTIONAL_REVISION_STEPS"]
