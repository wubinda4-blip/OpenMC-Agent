"""Real LLM adapter for incremental patch generation (Phase 7/7C).

Wraps the project's existing OpenAI-compatible LLM provider (Zhipu, DeepSeek,
aisuite, ...) into the ``Callable[[str], str]`` interface that the patch
generator expects.

Phase 7C additions:
* ``output_mode`` parameter to request structured JSON output.
* ``generate_patch_json()`` method on the returned callable for providers
  that support ``response_format`` / JSON mode.

P0-LARGE-STRUCTURED-PATCH additions:
* ``PatchLLMResponse`` typed telemetry (finish_reason, token usage).
* ``generate_patch_json_with_meta()`` returns ``PatchLLMResponse``.
* ``strict_structured_output`` fail-closed policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

# Reference token budgets for each patch type (multi-assembly / full-core
# case — the largest legitimate patch). These are NOT auto-applied to LLM
# calls: provider defaults (e.g. DeepSeek ~8192) are larger than any safe
# universal cap, and capping below them truncates large patches (observed: a
# 4500-token cap truncated a ~6000-token universes patch). A caller may pass
# these explicitly to generate_patch(max_tokens=...) if it wants to cap a
# specific call. Thinking-mode providers are controlled via reasoning_effort
# in the client instead (see DSChatClient.adjust_payload).
PATCH_MAX_TOKENS: dict[str, int] = {
    "facts": 3000,         # multi-assembly scoped_expected_counts ~2200 tokens
    "materials": 3500,
    "universes": 6500,     # multi-cell universes + nested component profiles
    "pin_map": 2500,
    "axial_layers": 3000,
    "axial_overlays": 3500,
    "settings": 1500,
}

# Output budgets for large multi-assembly / full-core patches whose JSON
# exceeds typical provider output-token defaults (DeepSeek ~8192). Unlike
# ``PATCH_MAX_TOKENS`` above (a reference baseline that is intentionally NOT
# applied — see its comment), these ARE passed explicitly to
# ``generate_patch(max_tokens=...)`` by the incremental executor, overriding
# the provider default so the response is not truncated mid-JSON. Observed:
# a VERA4 11-universe catalog truncated at ~6500 tokens under the default
# budget. Only patches known to exceed the provider default are listed;
# every other patch type keeps the provider default. If a provider rejects
# a value above its own output cap, lower the value here or make it
# per-provider.
LARGE_PATCH_MAX_TOKENS: dict[str, int] = {
    "universes": 16000,        # multi-cell universes + nested component profiles
    "assembly_catalog": 16000, # full pin_map + localized_insert_intents per type
    "core_layout": 12000,      # full-core placement map
}

OutputMode = Literal["auto", "plain_prompt", "json_object", "json_schema", "tool_call"]
ReasoningEffort = Literal["none", "low", "medium", "high"]


@dataclass
class PatchLLMResponse:
    """Typed LLM response carrying telemetry for truncation and budget tracking.

    ``content`` is the raw text returned by the provider.  The remaining
    fields are best-effort telemetry — any field may be ``None`` if the
    provider does not report it.  **No field is ever guessed.**
    """

    content: str = ""
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None
    output_mode_requested: str = ""
    output_mode_used: str = ""
    structured_fallback_used: bool = False
    structured_fallback_reasons: list[str] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_truncated(self) -> bool:
        """Return True if the provider signalled output truncation."""
        if self.finish_reason:
            fr = self.finish_reason.lower()
            if fr in ("length", "max_tokens"):
                return True
            if "context" in fr and "length" in fr:
                return True
        return False

    @property
    def is_context_exhausted(self) -> bool:
        """Return True if the provider signalled context window exhaustion."""
        if self.finish_reason:
            fr = self.finish_reason.lower()
            if "context" in fr and "exhaust" in fr:
                return True
        return False

    @property
    def reasoning_chars(self) -> int | None:
        """Best-effort reasoning size without persisting full reasoning_content."""
        return self.metadata.get("reasoning_chars")

    @property
    def reasoning_hash(self) -> str | None:
        """Short hash of reasoning content (no full text persisted)."""
        return self.metadata.get("reasoning_hash")


def normalize_patch_llm_response(raw: Any) -> PatchLLMResponse:
    """Normalize either a ``str`` or ``PatchLLMResponse`` into ``PatchLLMResponse``.

    ``FakePatchLLM`` and legacy clients return plain ``str``.  This helper
    wraps such returns into a minimal ``PatchLLMResponse`` so downstream
    code can treat both paths uniformly.
    """
    if isinstance(raw, PatchLLMResponse):
        return raw
    if isinstance(raw, str):
        return PatchLLMResponse(content=raw, output_mode_used="plain_prompt")
    return PatchLLMResponse(content=str(raw) if raw is not None else "")


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
        reasoning_effort: ReasoningEffort | None = None,
        strict_structured_output: bool = False,
    ) -> None:
        self._client = client
        self._model_name = model_name
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._output_mode = output_mode
        self._reasoning_effort = reasoning_effort
        self._strict_structured_output = strict_structured_output
        self.last_output_mode_requested: str = output_mode
        self.last_output_mode_used: str = "plain_prompt"
        self.last_output_fallback_used: bool = False
        self.last_output_fallback_reasons: list[str] = []

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

        self.last_output_mode_requested = self._output_mode
        self.last_output_fallback_used = False
        self.last_output_fallback_reasons = []
        schema_name = "".join(
            char if char.isalnum() or char == "_" else "_"
            for char in f"{patch_type}_patch_repair"
        )
        if self._output_mode in ("json_schema", "auto"):
            try:
                text = self._call(
                    prompt,
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": schema_name,
                            "strict": True,
                            "schema": json_schema or {},
                        },
                    },
                    max_tokens=effective_max,
                )
                self.last_output_mode_used = "json_schema"
                return text
            except Exception as exc:
                self._record_structured_fallback("json_schema", exc)

        if self._output_mode in ("json_schema", "json_object", "auto"):
            try:
                text = self._call(
                    prompt,
                    response_format={"type": "json_object"},
                    max_tokens=effective_max,
                )
                self.last_output_mode_used = "json_object"
                return text
            except Exception as exc:
                self._record_structured_fallback("json_object", exc)

        # Providers which reject both structured modes still receive a strict
        # JSON-only prompt and are recorded as an explicit compatibility mode.
        text = self._call(prompt, response_format=None, max_tokens=effective_max)
        self.last_output_mode_used = "plain_prompt"
        return text

    def _record_structured_fallback(self, mode: str, exc: Exception) -> None:
        self.last_output_fallback_used = True
        self.last_output_fallback_reasons.append(f"{mode} unsupported: {type(exc).__name__}")

    def _call_raw(
        self,
        prompt: str,
        *,
        response_format: dict[str, Any] | None = None,
        max_tokens: int | None = None,
    ) -> PatchLLMResponse:
        """Call the provider and return a ``PatchLLMResponse`` with telemetry.

        This is the enriched variant of ``_call`` that also captures
        ``finish_reason`` and token ``usage`` from the provider response.
        Only lightweight aggregates are persisted — never full reasoning text.
        """
        kwargs: dict[str, Any] = {
            "model": self._model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self._temperature,
        }
        if max_tokens is not None or self._max_tokens is not None:
            kwargs["max_tokens"] = max_tokens or self._max_tokens
        if response_format is not None:
            kwargs["response_format"] = response_format
        if self._reasoning_effort is not None:
            kwargs["reasoning_effort"] = self._reasoning_effort
        response = self._client.chat.completions.create(**kwargs)
        content = ""
        finish_reason: str | None = None
        try:
            choice = response.choices[0]
            content = choice.message.content or ""
            finish_reason = getattr(choice, "finish_reason", None)
        except (AttributeError, IndexError, TypeError) as exc:
            raise RuntimeError(f"patch LLM response had unexpected shape: {exc}") from exc
        usage = getattr(response, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", None) if usage else None
        completion_tokens = getattr(usage, "completion_tokens", None) if usage else None
        reasoning_tokens = None
        total_tokens = getattr(usage, "total_tokens", None) if usage else None
        # OpenAI o1-style reasoning token details.
        if usage and hasattr(usage, "completion_tokens_details"):
            details = usage.completion_tokens_details
            reasoning_tokens = getattr(details, "reasoning_tokens", None)
        # Lightweight reasoning fingerprint (no full text persisted).
        reasoning_meta: dict[str, Any] = {}
        try:
            msg = response.choices[0].message
            rc = getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", None)
            if rc and isinstance(rc, str) and rc:
                reasoning_meta["reasoning_chars"] = len(rc)
                import hashlib
                reasoning_meta["reasoning_hash"] = hashlib.sha256(rc.encode()).hexdigest()[:16]
        except Exception:
            pass
        provider_name = type(self._client).__name__
        return PatchLLMResponse(
            content=content,
            finish_reason=str(finish_reason) if finish_reason else None,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            reasoning_tokens=reasoning_tokens,
            total_tokens=total_tokens,
            provider=provider_name,
            model=self._model_name,
            metadata=reasoning_meta,
        )

    def generate_patch_json_with_meta(
        self,
        *,
        prompt: str,
        patch_type: str,
        json_schema: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> PatchLLMResponse:
        """Structured-output interface returning ``PatchLLMResponse`` with telemetry.

        When ``strict_structured_output`` is True, a failure of both
        ``json_schema`` and ``json_object`` modes raises
        ``patch_generation.structured_output_unavailable`` instead of
        silently falling back to plain prompt.
        """
        effective_max = max_tokens or self._max_tokens
        self.last_output_mode_requested = self._output_mode
        self.last_output_fallback_used = False
        self.last_output_fallback_reasons = []
        schema_name = "".join(
            char if char.isalnum() or char == "_" else "_"
            for char in f"{patch_type}_patch_repair"
        )
        if self._output_mode in ("json_schema", "auto"):
            try:
                resp = self._call_raw(
                    prompt,
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": schema_name,
                            "strict": True,
                            "schema": json_schema or {},
                        },
                    },
                    max_tokens=effective_max,
                )
                resp.output_mode_requested = self._output_mode
                resp.output_mode_used = "json_schema"
                self.last_output_mode_used = "json_schema"
                return resp
            except Exception as exc:
                self._record_structured_fallback("json_schema", exc)

        if self._output_mode in ("json_schema", "json_object", "auto"):
            try:
                resp = self._call_raw(
                    prompt,
                    response_format={"type": "json_object"},
                    max_tokens=effective_max,
                )
                resp.output_mode_requested = self._output_mode
                resp.output_mode_used = "json_object"
                self.last_output_mode_used = "json_object"
                return resp
            except Exception as exc:
                self._record_structured_fallback("json_object", exc)

        if self._strict_structured_output:
            resp = PatchLLMResponse(
                content="",
                output_mode_requested=self._output_mode,
                output_mode_used="unavailable",
                structured_fallback_used=True,
                structured_fallback_reasons=list(self.last_output_fallback_reasons),
                provider=type(self._client).__name__,
                model=self._model_name,
            )
            resp.metadata["error_code"] = "patch_generation.structured_output_unavailable"
            self.last_output_mode_used = "unavailable"
            return resp

        resp = self._call_raw(prompt, response_format=None, max_tokens=effective_max)
        resp.output_mode_requested = self._output_mode
        resp.output_mode_used = "plain_prompt"
        resp.structured_fallback_used = self.last_output_fallback_used
        resp.structured_fallback_reasons = list(self.last_output_fallback_reasons)
        self.last_output_mode_used = "plain_prompt"
        return resp

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
        # Some OpenAI-compatible providers reserve part of the response token
        # budget for hidden reasoning unless this is explicitly disabled.  This
        # is deliberately opt-in so existing callers retain their provider's
        # default behaviour.
        if self._reasoning_effort is not None:
            kwargs["reasoning_effort"] = self._reasoning_effort
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
    reasoning_effort: ReasoningEffort | None = None,
    strict_structured_output: bool = False,
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
    reasoning_effort
        Optional provider reasoning budget.  ``"none"`` is useful for
        large JSON-only patch responses on providers that otherwise consume
        output tokens with hidden reasoning.
    strict_structured_output
        When True, fail closed if both json_schema and json_object are
        unavailable instead of falling back to plain prompt.  Used for
        large structured patch generation (e.g. universe fragments).
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
            reasoning_effort=reasoning_effort,
            strict_structured_output=strict_structured_output,
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
        reasoning_effort=reasoning_effort,
        strict_structured_output=strict_structured_output,
    )


__all__ = [
    "LARGE_PATCH_MAX_TOKENS",
    "make_patch_llm_client",
    "PATCH_MAX_TOKENS",
    "PatchLLMResponse",
    "normalize_patch_llm_response",
    "StructuredPatchLLMClient",
    "OutputMode",
    "ReasoningEffort",
]
