"""Real LLM adapter for incremental patch generation (Phase 7).

Wraps the project's existing OpenAI-compatible LLM provider (Zhipu, DeepSeek,
aisuite, ...) into the simple ``Callable[[str], str]`` interface that the
patch generator expects.  No JSON parsing or validation happens here — the
adapter is a pure ``prompt → raw_text`` bridge.
"""

from __future__ import annotations

import time
from typing import Any, Callable


# Per-patch-type suggested max token budgets (informational; not enforced
# unless the underlying provider supports max_tokens).
PATCH_MAX_TOKENS: dict[str, int] = {
    "facts": 1200,
    "materials": 2500,
    "universes": 3500,
    "pin_map": 1800,
    "axial_layers": 2500,
    "axial_overlays": 2500,
    "settings": 800,
}


def make_patch_llm_client(
    llm: Any | None = None,
    *,
    model_name: str | None = None,
    temperature: float = 0.0,
    max_tokens: int | None = None,
    timeout_s: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> Callable[[str], str]:
    """Create a patch LLM client from an existing provider or model name.

    Parameters
    ----------
    llm
        If provided, must be either:
        * A callable ``(prompt: str) -> str`` (already adapted).
        * An OpenAI-compatible client with ``.chat.completions.create()``.
        If ``None``, a client is constructed from ``model_name``.
    model_name
        Model identifier in ``provider:model`` format (e.g.
        ``"zhipu:glm-5.2"``).  Required if ``llm`` is ``None``.
    temperature
        LLM temperature (default 0 for deterministic output).
    max_tokens
        If provided, forwarded to the LLM call.  Per-patch budgets from
        :data:`PATCH_MAX_TOKENS` can be used.
    timeout_s
        Per-request timeout (if supported by the provider).
    metadata
        Optional metadata dict for observability (not used for LLM calls).

    Returns
    -------
    Callable[[str], str]
        A function that takes a prompt string and returns raw LLM text.
    """
    # If llm is already a callable (e.g. FakePatchLLM), use it directly.
    if llm is not None and callable(llm) and not hasattr(llm, "chat"):
        return llm  # type: ignore[return-value]

    # If llm is an OpenAI-compatible client, wrap it.
    if llm is not None and hasattr(llm, "chat"):
        return _wrap_openai_client(
            llm,
            model_name=model_name or "",
            temperature=temperature,
            max_tokens=max_tokens,
        )

    # Construct a client from model_name.
    if model_name is None:
        raise ValueError(
            "make_patch_llm_client requires either llm or model_name"
        )

    from openmc_agent.llm import _client_for_model

    client = _client_for_model(model_name)
    return _wrap_openai_client(
        client,
        model_name=model_name,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def _wrap_openai_client(
    client: Any,
    *,
    model_name: str,
    temperature: float = 0.0,
    max_tokens: int | None = None,
) -> Callable[[str], str]:
    """Wrap an OpenAI-compatible client into a simple callable."""

    def patch_client(prompt: str) -> str:
        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        response = client.chat.completions.create(**kwargs)
        # Use the same extraction logic as the main LLM module.
        try:
            return response.choices[0].message.content
        except (AttributeError, IndexError, TypeError) as exc:
            raise RuntimeError(
                f"patch LLM response had unexpected shape: {exc}"
            ) from exc

    return patch_client


__all__ = [
    "make_patch_llm_client",
    "PATCH_MAX_TOKENS",
]
