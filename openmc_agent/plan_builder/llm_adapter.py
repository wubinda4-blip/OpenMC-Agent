"""Real LLM adapter for incremental patch generation (Phase 7/7C).

Wraps the project's existing OpenAI-compatible LLM provider (Zhipu, DeepSeek,
aisuite, ...) into the ``Callable[[str], str]`` interface that the patch
generator expects.

Phase 7C additions:
* ``output_mode`` parameter to request structured JSON output.
* ``generate_patch_json()`` method on the returned callable for providers
  that support ``response_format`` / JSON mode.
"""

from __future__ import annotations

from typing import Any, Callable, Literal

# Per-patch-type suggested max token budgets.
PATCH_MAX_TOKENS: dict[str, int] = {
    "facts": 1200,
    "materials": 2500,
    "universes": 3500,
    "pin_map": 1800,
    "axial_layers": 2500,
    "axial_overlays": 2500,
    "settings": 800,
}

OutputMode = Literal["auto", "plain_prompt", "json_object", "json_schema", "tool_call"]


class StructuredPatchLLMClient:
    """Wraps an OpenAI-compatible client with optional JSON mode support.

    Acts as both a plain ``Callable[[str], str]`` (backward compatible) and
    a structured-output client via :meth:`generate_patch_json`.
    """

    def __init__(
        self,
        client: Any,
        *,
        model_name: str,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        output_mode: OutputMode = "auto",
    ) -> None:
        self._client = client
        self._model_name = model_name
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._output_mode = output_mode
        self._json_mode_supported: bool | None = None

    def __call__(self, prompt: str) -> str:
        """Plain callable interface (backward compatible)."""
        return self._call(prompt, response_format=None)

    def generate_patch_json(
        self,
        *,
        prompt: str,
        patch_type: str,
        json_schema: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Structured-output interface for patch generation.

        Tries JSON mode / structured output based on ``output_mode`` setting.
        Falls back to plain prompt if structured output is unavailable.
        """
        effective_max = max_tokens or self._max_tokens

        if self._output_mode in ("json_schema", "auto"):
            # Try response_format with json_schema (OpenAI structured output).
            try:
                return self._call(
                    prompt,
                    response_format={"type": "json_object"},
                    max_tokens=effective_max,
                )
            except Exception:
                if self._output_mode == "json_schema":
                    raise  # strict mode: don't fallback
                # auto mode: try json_object, then plain.

        if self._output_mode in ("json_object", "auto"):
            try:
                return self._call(
                    prompt,
                    response_format={"type": "json_object"},
                    max_tokens=effective_max,
                )
            except Exception:
                if self._output_mode == "json_object":
                    raise
                # auto mode: fallback to plain.

        # Plain prompt fallback.
        return self._call(prompt, response_format=None, max_tokens=effective_max)

    def _call(
        self,
        prompt: str,
        *,
        response_format: dict[str, Any] | None = None,
        max_tokens: int | None = None,
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": self._model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self._temperature,
        }
        if max_tokens is not None or self._max_tokens is not None:
            kwargs["max_tokens"] = max_tokens or self._max_tokens
        if response_format is not None:
            kwargs["response_format"] = response_format
        response = self._client.chat.completions.create(**kwargs)
        try:
            return response.choices[0].message.content
        except (AttributeError, IndexError, TypeError) as exc:
            raise RuntimeError(
                f"patch LLM response had unexpected shape: {exc}"
            ) from exc


def make_patch_llm_client(
    llm: Any | None = None,
    *,
    model_name: str | None = None,
    temperature: float = 0.0,
    max_tokens: int | None = None,
    timeout_s: float | None = None,
    metadata: dict[str, Any] | None = None,
    output_mode: OutputMode = "auto",
) -> Callable[[str], str]:
    """Create a patch LLM client from an existing provider or model name.

    The returned callable also supports ``generate_patch_json()`` for
    structured output if the underlying provider supports it.

    Parameters
    ----------
    output_mode
        Controls how JSON output is requested from the provider:
        ``"auto"`` (try structured, fallback to plain),
        ``"plain_prompt"`` (no response_format),
        ``"json_object"`` (require JSON mode),
        ``"json_schema"`` (require structured output).
    """
    # If llm is already a callable (e.g. FakePatchLLM), use it directly.
    if llm is not None and callable(llm) and not hasattr(llm, "chat"):
        return llm  # type: ignore[return-value]

    # If llm is an OpenAI-compatible client, wrap with StructuredPatchLLMClient.
    if llm is not None and hasattr(llm, "chat"):
        return StructuredPatchLLMClient(
            llm,
            model_name=model_name or "",
            temperature=temperature,
            max_tokens=max_tokens,
            output_mode=output_mode,
        )

    # Construct from model_name.
    if model_name is None:
        raise ValueError(
            "make_patch_llm_client requires either llm or model_name"
        )

    from openmc_agent.llm import _client_for_model

    client = _client_for_model(model_name)
    return StructuredPatchLLMClient(
        client,
        model_name=model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        output_mode=output_mode,
    )


__all__ = [
    "make_patch_llm_client",
    "PATCH_MAX_TOKENS",
    "StructuredPatchLLMClient",
    "OutputMode",
]
