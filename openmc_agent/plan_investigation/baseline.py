"""Deterministic mandatory investigation baseline (Phase 8A Step 5).

The Step 4 investigation agent relied on the LLM to remember to call the
required tools (``inspect_patch_schema``, ``inspect_requirement_structure``,
``search_source_index``).  Real VERA3 runs showed this is unreliable:
with ``reasoning_effort=low`` the LLM skipped schema inspection and the
controlled coverage gate blocked the whole run.

Step 5 fixes this by giving Python a mandatory-action policy that runs
BEFORE the LLM action planner.  The LLM can only add *supplemental*
actions; it cannot skip mandatory ones.

Modes:
* ``off`` — preserve legacy behaviour (no mandatory baseline).
* ``advisory`` / ``controlled`` — Python executes the mandatory baseline
  for each patch_type before the LLM action planner runs.

Hard rules:
* Mandatory failures are blocking in controlled mode (the same way
  LLM-driven failures are).
* Mandatory calls count against the tool-call budget.
* Mandatory calls use the same ``ToolCallLedger`` so artifacts and
  truthfulness summaries see them.
* No benchmark-specific actions.  Search terms are derived from the
  accepted Facts + Inventory when available.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator, model_validator

from openmc_agent.schemas import AgentBaseModel

from .errors import PlanInvestigationIssue
from .hashing import content_hash, short_id

__all__ = [
    "RequiredInvestigationAction",
    "InvestigationBaselinePolicy",
    "facts_baseline_policy",
    "materials_baseline_policy",
    "universes_baseline_policy",
    "baseline_policy_for_patch_type",
    "BASELINE_POLICY_HASH_SEED",
]


# ---------------------------------------------------------------------------
# Required-action model
# ---------------------------------------------------------------------------


class RequiredInvestigationAction(AgentBaseModel):
    """One mandatory tool call the Python baseline must execute.

    ``arguments`` is a JSON-compatible dict matching the tool's input
    schema.  ``required_for_controlled`` flags whether the action is
    enforced in controlled mode (it always is in Step 5; the flag exists
    so a future step can relax specific actions in advisory mode).
    """

    action_id: str
    patch_type: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    required_for_controlled: bool = True
    result_requirement: str = ""
    source: str = "baseline_policy"
    action_hash: str = ""

    @model_validator(mode="after")
    def _compute_action_hash(self) -> "RequiredInvestigationAction":
        expected = short_id(
            "act",
            {
                "p": self.patch_type,
                "t": self.tool_name,
                "a": self.arguments,
                "r": self.result_requirement,
            },
        )
        if not self.action_hash:
            object.__setattr__(self, "action_hash", expected)
        elif self.action_hash != expected:
            raise PlanInvestigationIssue(
                "plan_investigation.baseline_action_hash_mismatch",
                "action_hash does not match the deterministic value",
                details={"expected": expected, "actual": self.action_hash},
            )
        return self


# ---------------------------------------------------------------------------
# Baseline policy
# ---------------------------------------------------------------------------


class InvestigationBaselinePolicy(AgentBaseModel):
    """A set of mandatory actions for one ``patch_type``.

    Policies are deterministic functions of (patch_type, accepted_facts,
    inventory).  The default policy table below covers ``facts``,
    ``materials``, and ``universes``; other patch types get an empty
    policy (no mandatory actions) so existing behaviour is unchanged.
    """

    patch_type: str
    actions: tuple[RequiredInvestigationAction, ...] = Field(default_factory=tuple)
    policy_hash: str = ""

    @model_validator(mode="after")
    def _compute_policy_hash(self) -> "InvestigationBaselinePolicy":
        payload = {
            "p": self.patch_type,
            "a": [a.model_dump(mode="json") for a in self.actions],
        }
        expected = content_hash(payload)
        if not self.policy_hash:
            object.__setattr__(self, "policy_hash", expected)
        elif self.policy_hash != expected:
            raise PlanInvestigationIssue(
                "plan_investigation.baseline_policy_hash_mismatch",
                "policy_hash does not match the deterministic value",
                details={"expected": expected, "actual": self.policy_hash},
            )
        return self

    def action_count(self) -> int:
        return len(self.actions)

    def tool_names(self) -> tuple[str, ...]:
        return tuple(action.tool_name for action in self.actions)


# ---------------------------------------------------------------------------
# Default policy builders
# ---------------------------------------------------------------------------


BASELINE_POLICY_HASH_SEED: str = "step5_baseline_v0.1"


def facts_baseline_policy() -> InvestigationBaselinePolicy:
    """Mandatory baseline for the Facts investigation.

    Every Facts investigation MUST:
    1. Inspect the Facts patch schema so the LLM knows the contract.
    2. Inspect the requirement structure (scope indicators + grid size).
    3. Run at least one source search to produce source-backed evidence.

    These were the three coverage gates the Step 4 contract enforced
    after-the-fact; Step 5 makes them explicit pre-conditions.
    """

    return InvestigationBaselinePolicy(
        patch_type="facts",
        actions=(
            RequiredInvestigationAction(
                action_id="",
                patch_type="facts",
                tool_name="inspect_patch_schema",
                arguments={"patch_type": "facts"},
                result_requirement="patch_schema_resolved",
                source="baseline_policy",
            ),
            RequiredInvestigationAction(
                action_id="",
                patch_type="facts",
                tool_name="inspect_requirement_structure",
                arguments={},
                result_requirement="requirement_structure_scoped",
                source="baseline_policy",
            ),
            RequiredInvestigationAction(
                action_id="",
                patch_type="facts",
                tool_name="search_source_index",
                arguments={"query": "core"},
                result_requirement="at_least_one_source_backed_claim",
                source="baseline_policy",
            ),
        ),
    )


def materials_baseline_policy(
    *,
    accepted_facts: Any | None = None,
    inventory: Any | None = None,
) -> InvestigationBaselinePolicy:
    """Mandatory baseline for the Materials investigation.

    Always:
    1. Inspect the Materials patch schema.
    2. Query the ledger for material-role claims (so the LLM can see
       what roles the Inventory has compiled).

    Plus: one source search whose query is derived from the Inventory's
    declared material roles when available, falling back to a generic
    ``"material"`` query otherwise.  This keeps the baseline
    reactor-neutral: no VERA/PWR/BWR-specific terms.
    """

    queries = _derive_material_search_queries(accepted_facts, inventory)
    primary_query = queries[0] if queries else "material"
    actions: list[RequiredInvestigationAction] = [
        RequiredInvestigationAction(
            action_id="",
            patch_type="materials",
            tool_name="inspect_patch_schema",
            arguments={"patch_type": "materials"},
            result_requirement="patch_schema_resolved",
            source="baseline_policy",
        ),
        RequiredInvestigationAction(
            action_id="",
            patch_type="materials",
            tool_name="query_evidence_ledger",
            arguments={"predicate": "material_role_required"},
            result_requirement="existing_material_roles_queried",
            source="baseline_policy",
        ),
        RequiredInvestigationAction(
            action_id="",
            patch_type="materials",
            tool_name="search_source_index",
            arguments={"query": primary_query},
            result_requirement="at_least_one_source_backed_claim",
            source="baseline_policy",
        ),
    ]
    return InvestigationBaselinePolicy(patch_type="materials", actions=tuple(actions))


def universes_baseline_policy(
    *,
    accepted_facts: Any | None = None,
    inventory: Any | None = None,
) -> InvestigationBaselinePolicy:
    """Mandatory baseline for the Universes investigation."""

    queries = _derive_universe_search_queries(accepted_facts, inventory)
    primary_query = queries[0] if queries else "universe"
    actions: list[RequiredInvestigationAction] = [
        RequiredInvestigationAction(
            action_id="",
            patch_type="universes",
            tool_name="inspect_patch_schema",
            arguments={"patch_type": "universes"},
            result_requirement="patch_schema_resolved",
            source="baseline_policy",
        ),
        RequiredInvestigationAction(
            action_id="",
            patch_type="universes",
            tool_name="query_evidence_ledger",
            arguments={"predicate": "geometry_profile_required"},
            result_requirement="existing_geometry_profiles_queried",
            source="baseline_policy",
        ),
        RequiredInvestigationAction(
            action_id="",
            patch_type="universes",
            tool_name="search_source_index",
            arguments={"query": primary_query},
            result_requirement="at_least_one_source_backed_claim",
            source="baseline_policy",
        ),
    ]
    return InvestigationBaselinePolicy(patch_type="universes", actions=tuple(actions))


def baseline_policy_for_patch_type(
    patch_type: str,
    *,
    accepted_facts: Any | None = None,
    inventory: Any | None = None,
) -> InvestigationBaselinePolicy:
    """Return the mandatory baseline policy for ``patch_type``.

    Unknown patch types get an empty policy (no mandatory actions) so
    legacy behaviour for axial_layers / pin_map / settings / etc. is
    unchanged.
    """

    if patch_type == "facts":
        return facts_baseline_policy()
    if patch_type == "materials":
        return materials_baseline_policy(accepted_facts=accepted_facts, inventory=inventory)
    if patch_type == "universes":
        return universes_baseline_policy(accepted_facts=accepted_facts, inventory=inventory)
    return InvestigationBaselinePolicy(patch_type=patch_type)


# ---------------------------------------------------------------------------
# Search-query derivation (reactor-neutral)
# ---------------------------------------------------------------------------


def _derive_material_search_queries(
    accepted_facts: Any | None, inventory: Any | None
) -> list[str]:
    """Pick source-search queries from the Inventory / Facts.

    Returns generic reactor-neutral queries ("material", "density",
    "composition", ...) when no Inventory is available.  When an
    Inventory is supplied, the queries are narrowed to the roles it
    declares, so we don't blindly search for "boron" if the source
    document never mentions boron.
    """

    fallback = ["material", "density", "composition"]
    if inventory is not None:
        roles = list(getattr(inventory, "declared_material_roles", []) or [])
        if roles:
            # Map roles to canonical search terms; unknown roles fall
            # back to the role name itself.
            role_to_query = {
                "fuel": "fuel enrichment",
                "coolant": "coolant density",
                "moderator": "moderator",
                "structural": "cladding",
                "absorber": "absorber",
                "poison": "burnable poison",
                "gas": "gas gap",
            }
            queries = [role_to_query.get(r, r) for r in roles[:4]]
            if queries:
                return queries
    if accepted_facts is not None:
        # Look at fuel_variant_requirements for enrichment hints.
        variants = list(getattr(accepted_facts, "fuel_variant_requirements", []) or [])
        if variants:
            return ["fuel enrichment", "density", "composition"]
    return fallback


def _derive_universe_search_queries(
    accepted_facts: Any | None, inventory: Any | None
) -> list[str]:
    """Pick source-search queries for the Universes investigation."""

    fallback = ["universe", "fuel pin", "guide tube"]
    if inventory is not None:
        components = list(getattr(inventory, "declared_component_kinds", []) or [])
        if components:
            # Map component kinds to canonical search terms.
            kind_to_query = {
                "fuel_pin": "fuel pin",
                "guide_tube": "guide tube",
                "instrument_tube": "instrument tube",
                "control_rod": "control rod",
                "pyrex_rod": "Pyrex",
                "thimble_plug": "thimble plug",
                "end_plug": "end plug",
                "gas_gap": "gas gap",
                "water_pin": "water pin",
                "spacer_grid": "spacer grid",
            }
            queries = [kind_to_query.get(c, c.replace("_", " ")) for c in components[:4]]
            if queries:
                return queries
    return fallback
