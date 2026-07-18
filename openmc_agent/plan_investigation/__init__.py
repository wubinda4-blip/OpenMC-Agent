"""Typed planning evidence ledger and deterministic source indexing (Phase 8A Step 1).

This package is the foundation data layer for plan-investigation work.  It is
deliberately decoupled from the existing :mod:`openmc_agent.retrieval` loop,
from the LangGraph topology, from any LLM client, and from any tool dispatch.

Step 1 provides:

* Deterministic :class:`SourceIndex` construction from raw user-supplied text.
* Typed :class:`EvidenceClaim` / :class:`PlanningEvidenceLedger` models that
  distinguish explicit, deterministically-derived, assumption, unresolved and
  conflict evidence, with stable IDs and a deterministic ``ledger_hash``.
* Conflict detection, derivation re-computation, human-confirmed immutability.
* Atomic, deterministic artifact writing.

Step 1 does NOT provide (reserved for later steps):

* LLM investigation loops, prompt construction, or tool dispatch.
* Repository search, OpenMC docs search, or web search.
* GraphRAG / knowledge-graph integration.
* Gate retry, Facts prompt changes, or GeometryComponentInventory.
"""

from __future__ import annotations

PLAN_INVESTIGATION_SCHEMA_VERSION: str = "0.1"

__all__: list[str] = ["PLAN_INVESTIGATION_SCHEMA_VERSION"]
