"""Real LLM adapter for the Phase 8A plan investigation agent.

Produces a :class:`Callable[[str], str]` client suitable for
:class:`openmc_agent.plan_investigation.agent.InvestigationAgent`.

Distinct from :mod:`openmc_agent.plan_builder.llm_adapter`:

* Dedicated recorder role: ``plan_investigator`` (NOT ``planning_patch``).
* Dedicated client-instance id prefix: ``plan_investigator_`` so per-role
  budget and truthfulness summaries can attribute calls unambiguously.
* No Fake / reference / monolithic fallback.  A network failure surfaces
  as an infrastructure error rather than being silently swallowed.
* Strict structured output: the investigator MUST return a complete JSON
  object.  Prose-only responses are rejected upstream by the agent's
  ``_parse_investigation_plan``.

The adapter never persists ``reasoning_content``.  Provider-specific
thinking traces stay inside the provider response and are dropped before
the recorder sees the result.
"""

from __future__ import annotations

from typing import Any, Callable

from pydantic import Field

from openmc_agent.schemas import AgentBaseModel

__all__ = [
    "InvestigatorClientConfig",
    "make_investigation_llm_client",
    "PLAN_INVESTIGATOR_ROLE",
    "PLAN_INVESTIGATOR_INSTANCE_PREFIX",
]


PLAN_INVESTIGATOR_ROLE: str = "plan_investigator"
PLAN_INVESTIGATOR_INSTANCE_PREFIX: str = "plan_investigator"


class InvestigatorClientConfig(AgentBaseModel):
    """Per-run configuration for the investigator LLM client."""

    model: str
    temperature: float = 0.0
    max_tokens: int | None = None
    output_mode: str = "auto"
    reasoning_effort: str | None = None
    strict_structured_output: bool = True
    client_instance_id: str | None = None


def make_investigation_llm_client(
    *,
    base_llm: Any | None = None,
    model_name: str,
    temperature: float = 0.0,
    max_tokens: int | None = None,
    output_mode: str = "auto",
    reasoning_effort: str | None = None,
    strict_structured_output: bool = True,
    recorder: Any | None = None,
    client_instance_id: str | None = None,
) -> Callable[[str], str]:
    """Build a recorder-wrapped investigator LLM client.

    Parameters
    ----------
    base_llm
        An existing OpenAI-compatible provider client (``client.chat...
        .create``).  If ``None``, the provider is resolved from
        ``model_name`` via :func:`openmc_agent.llm._client_for_model`.
    model_name
        Provider-qualified model id, e.g. ``"ds:deepseek-v4-flash"`` or
        ``"deepseek:deepseek-chat"``.
    recorder
        Optional :class:`LLMCallRecorder`.  When supplied, every
        investigator call is recorded with role
        :data:`PLAN_INVESTIGATOR_ROLE` and the supplied (or
        auto-registered) ``client_instance_id``.
    client_instance_id
        Optional explicit cid.  When ``None`` and a recorder is supplied,
        a new cid is registered with the
        :data:`PLAN_INVESTIGATOR_INSTANCE_PREFIX` prefix.
    strict_structured_output
        Default True.  When True, the underlying StructuredPatchLLMClient
        is constructed with ``strict_structured_output=True`` so a
        provider that cannot deliver JSON fails closed instead of
        falling back to prose.
    """

    from openmc_agent.plan_builder.llm_adapter import (
        StructuredPatchLLMClient,
        OutputMode,
    )

    if base_llm is None:
        from openmc_agent.llm import _client_for_model

        base_llm = _client_for_model(model_name)

    if not hasattr(base_llm, "chat"):
        raise ValueError(
            "make_investigation_llm_client requires an OpenAI-compatible base_llm"
        )

    # Validate output_mode through the Literal alias.
    try:
        normalized_output_mode: OutputMode = output_mode  # type: ignore[assignment]
    except Exception:  # pragma: no cover - defensive
        normalized_output_mode = "auto"  # type: ignore[assignment]

    client = StructuredPatchLLMClient(
        base_llm,
        model_name=model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        output_mode=normalized_output_mode,
        reasoning_effort=reasoning_effort,  # type: ignore[arg-type]
        strict_structured_output=strict_structured_output,
    )

    if recorder is None:
        return client  # type: ignore[return-value]

    # Resolve / register the client instance id.
    if client_instance_id is None:
        # Stamp the prefix onto the cid so the role is visible in the
        # serialized recorder output without relying on task-name
        # matching.
        client_instance_id = recorder.register_client(
            client_instance_id=PLAN_INVESTIGATOR_INSTANCE_PREFIX
        )
    elif not client_instance_id.startswith(PLAN_INVESTIGATOR_INSTANCE_PREFIX):
        prefixed = f"{PLAN_INVESTIGATOR_INSTANCE_PREFIX}_{client_instance_id}"
        # Register the prefixed cid with the recorder so its
        # ``_client_instance_ids`` set contains the same value that
        # later recordings will carry.
        client_instance_id = recorder.register_client(client_instance_id=prefixed)

    return recorder.wrap_prompt_only_client(
        client,
        client_instance_id,
        role=PLAN_INVESTIGATOR_ROLE,
        task_name="investigation",
    )
