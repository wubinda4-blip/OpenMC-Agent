import json
import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar

import aisuite
import httpx
from pydantic import BaseModel, ValidationError

from openmc_agent.prompts import system_prompt_for_schema


T = TypeVar("T", bound=BaseModel)
DEFAULT_MODEL = "zhipu:glm-5.2"

ZHIPU_DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
ZHIPU_DEFAULT_TIMEOUT_SECONDS = 180.0
ZHIPU_DEFAULT_MAX_RETRIES = 2

DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_DEFAULT_TIMEOUT_SECONDS = 180.0
DEEPSEEK_DEFAULT_MAX_RETRIES = 2
LLM_HEARTBEAT_DEFAULT_SECONDS = 10.0

_llm_progress_enabled = False


def set_llm_progress(enabled: bool) -> None:
    """Toggle stderr progress logging for slow LLM HTTP calls.

    Off by default so library and test usage stays silent. The CLI enables it
    alongside ``--verbose`` so a long-running remote model call can be told
    apart from a hung process: every few seconds a heartbeat line confirms the
    call is still in flight, and request/response timing lines mark when the
    connection is attempted and when the model actually answers.
    """
    global _llm_progress_enabled
    _llm_progress_enabled = bool(enabled)


def _llm_log(message: str) -> None:
    if _llm_progress_enabled:
        print(f"[llm] {message}", file=sys.stderr, flush=True)


def _normalize_log(message: str) -> None:
    """Log deterministic fixups applied to LLM structured-output drafts.

    Shares the progress-enabled gate with ``_llm_log`` so normalization is
    visible alongside the request/response logs when a CLI run opts in, and
    silent in library/test usage.
    """
    if _llm_progress_enabled:
        print(f"[normalize] {message}", file=sys.stderr, flush=True)


def _heartbeat_seconds() -> float:
    raw = os.getenv("OPENMC_AGENT_LLM_HEARTBEAT_SECONDS")
    if not raw:
        return LLM_HEARTBEAT_DEFAULT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return LLM_HEARTBEAT_DEFAULT_SECONDS
    return value if value > 0 else LLM_HEARTBEAT_DEFAULT_SECONDS


@dataclass
class _HeartbeatHandle:
    thread: threading.Thread
    stop: threading.Event


def _start_heartbeat(started_at: float) -> _HeartbeatHandle | None:
    """Print a heartbeat every few seconds while an LLM call is in flight.

    Returns ``None`` when progress logging is disabled, so callers pay no
    thread overhead in library or test mode.
    """
    if not _llm_progress_enabled:
        return None
    stop = threading.Event()

    def _loop() -> None:
        interval = _heartbeat_seconds()
        while not stop.wait(interval):
            _llm_log(f"... still waiting for LLM response ({time.monotonic() - started_at:.0f}s elapsed)")

    thread = threading.Thread(target=_loop, daemon=True, name="openmc-llm-heartbeat")
    thread.start()
    return _HeartbeatHandle(thread=thread, stop=stop)


def _stop_heartbeat(handle: _HeartbeatHandle | None) -> None:
    if handle is None:
        return
    handle.stop.set()
    handle.thread.join(timeout=1.0)


@dataclass(frozen=True)
class StructuredOutputResult(Generic[T]):
    ok: bool
    value: T | None = None
    error: str = ""
    raw_response: str = ""
    candidate_payload: dict[str, Any] | None = None
    parse_notes: list[str] | None = None


class OpenAICompatibleChatClient:
    """Minimal OpenAI-compatible chat client used for Zhipu, DeepSeek, and alike.

    Talks to a ``/chat/completions`` endpoint that accepts the OpenAI
    request/response shape. Subclasses pin the provider name, base URL, API
    key env var, and timeout/retry env vars. Supports transport retries,
    configurable timeouts, and SSE streaming (on by default).
    """

    provider: str = ""
    default_base_url: str = ""
    api_key_env: str = ""
    timeout_env: str = ""
    max_retries_env: str = ""
    default_timeout: float = 0.0
    default_max_retries: int = 0

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        http_client: Any | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        retry_sleep_seconds: float = 1.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url or self.default_base_url
        self.http_client = http_client or httpx.Client()
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_sleep_seconds = retry_sleep_seconds
        self.chat = _Chat(self)

    def resolve_api_key(self) -> str | None:
        if self.api_key:
            return self.api_key
        return os.getenv(self.api_key_env) if self.api_key_env else None

    def resolve_timeout(self) -> float:
        return _env_seconds(self.timeout_env, self.timeout, self.default_timeout)

    def resolve_max_retries(self) -> int:
        return _env_int(self.max_retries_env, self.max_retries, self.default_max_retries)

    def adjust_payload(self, payload: dict[str, Any], model_name: str) -> dict[str, Any]:
        """Hook for provider-specific request tweaks. Base implementation is a no-op."""
        return payload


class ZhipuChatClient(OpenAICompatibleChatClient):
    provider = "zhipu"
    default_base_url = ZHIPU_DEFAULT_BASE_URL
    api_key_env = "ZHIPUAI_API_KEY"
    timeout_env = "ZHIPUAI_TIMEOUT_SECONDS"
    max_retries_env = "ZHIPUAI_MAX_RETRIES"
    default_timeout = ZHIPU_DEFAULT_TIMEOUT_SECONDS
    default_max_retries = ZHIPU_DEFAULT_MAX_RETRIES


class DeepSeekChatClient(OpenAICompatibleChatClient):
    provider = "deepseek"
    default_base_url = DEEPSEEK_DEFAULT_BASE_URL
    api_key_env = "DEEPSEEK_API_KEY"
    timeout_env = "DEEPSEEK_TIMEOUT_SECONDS"
    max_retries_env = "DEEPSEEK_MAX_RETRIES"
    default_timeout = DEEPSEEK_DEFAULT_TIMEOUT_SECONDS
    default_max_retries = DEEPSEEK_DEFAULT_MAX_RETRIES

    def adjust_payload(self, payload: dict[str, Any], model_name: str) -> dict[str, Any]:
        # deepseek-reasoner does not accept sampling params such as temperature;
        # sending them can make the API return HTTP 400. Strip them for the
        # reasoning model so structured-output calls keep working.
        if "reasoner" in model_name:
            payload.pop("temperature", None)
        return payload


class _Chat:
    def __init__(self, client: OpenAICompatibleChatClient) -> None:
        self.completions = _Completions(client)


class _Completions:
    def __init__(self, client: OpenAICompatibleChatClient) -> None:
        self.client = client

    def create(self, *, model: str, messages: list[dict], **kwargs: Any) -> Any:
        api_key = self.client.resolve_api_key()
        if not api_key:
            raise RuntimeError(
                f"{self.client.api_key_env} is required for {self.client.provider} models"
            )

        provider, model_name = _split_model(model)
        if provider != self.client.provider:
            raise ValueError(
                f"{type(self.client).__name__} only supports "
                f"{self.client.provider!r} models, got {model!r}"
            )

        model_name = model_name.lower()
        payload = {
            "model": model_name,
            "messages": messages,
        }
        payload.update(kwargs)
        payload = self.client.adjust_payload(payload, model_name)
        timeout = self.client.resolve_timeout()
        max_retries = self.client.resolve_max_retries()
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if _stream_enabled():
            payload["stream"] = True
            content = self._stream_with_retries(
                headers=headers,
                payload=payload,
                timeout=timeout,
                max_retries=max_retries,
            )
            return _SimpleResponse(content)
        response = self._post_with_retries(
            headers=headers,
            payload=payload,
            timeout=timeout,
            max_retries=max_retries,
        )
        response.raise_for_status()
        data = response.json()
        return _response_from_openai_compatible_payload(data)

    def _post_with_retries(
        self,
        *,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: float,
        max_retries: int,
    ) -> Any:
        for attempt in range(max_retries + 1):
            _llm_log(
                f"POST {self.client.base_url} "
                f"(timeout={timeout:.0f}s, attempt {attempt + 1}/{max_retries + 1})"
            )
            post_start = time.monotonic()
            try:
                return self.client.http_client.post(
                    self.client.base_url,
                    headers=headers,
                    json=payload,
                    timeout=timeout,
                )
            except httpx.TransportError as exc:
                elapsed = time.monotonic() - post_start
                if attempt >= max_retries:
                    _llm_log(f"transport error after {elapsed:.1f}s, giving up: {exc}")
                    raise
                _llm_log(
                    f"transport error after {elapsed:.1f}s ({type(exc).__name__}), "
                    f"retrying in {self.client.retry_sleep_seconds:.1f}s..."
                )
                if self.client.retry_sleep_seconds > 0:
                    time.sleep(self.client.retry_sleep_seconds)
        raise RuntimeError("unreachable retry loop state")

    def _stream_with_retries(
        self,
        *,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: float,
        max_retries: int,
    ) -> str:
        """Stream a chat completion and return the concatenated content.

        Streaming decouples the read timeout from total generation time: as
        long as the model emits tokens (content or reasoning) within each
        ``timeout`` window, the call keeps going, so a slow-but-progressing
        glm-5.2 generation no longer hits the 240s non-streaming wall. The
        progress logger prints periodic token counts so a live call can be
        told apart from a stalled one.
        """
        for attempt in range(max_retries + 1):
            _llm_log(
                f"POST (stream) {self.client.base_url} "
                f"(read timeout={timeout:.0f}s, attempt {attempt + 1}/{max_retries + 1})"
            )
            stream_start = time.monotonic()
            try:
                content = _consume_sse_stream(
                    http_client=self.client.http_client,
                    url=self.client.base_url,
                    headers=headers,
                    payload=payload,
                    timeout=timeout,
                    started_at=stream_start,
                )
                _llm_log(f"stream finished in {time.monotonic() - stream_start:.1f}s")
                return content
            except httpx.TransportError as exc:
                elapsed = time.monotonic() - stream_start
                if attempt >= max_retries:
                    _llm_log(f"stream transport error after {elapsed:.1f}s, giving up: {exc}")
                    raise
                _llm_log(
                    f"stream transport error after {elapsed:.1f}s ({type(exc).__name__}), "
                    f"retrying in {self.client.retry_sleep_seconds:.1f}s..."
                )
                if self.client.retry_sleep_seconds > 0:
                    time.sleep(self.client.retry_sleep_seconds)
        raise RuntimeError("unreachable retry loop state")


def generate_structured_output(
    *,
    requirement: str,
    schema: type[T],
    model: str | None = None,
    client: Any | None = None,
    normalizer: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> StructuredOutputResult[T]:
    selected_model = model or os.getenv("OPENMC_AGENT_MODEL", DEFAULT_MODEL)
    llm_client = client or _client_for_model(selected_model)
    messages = _build_messages(requirement, schema)
    schema_name = getattr(schema, "__name__", "schema")
    _llm_log(f"requesting structured output model={selected_model} schema={schema_name}")

    started = time.monotonic()
    heartbeat = _start_heartbeat(started)
    try:
        response = llm_client.chat.completions.create(
            model=selected_model,
            messages=messages,
            temperature=0,
        )
    except Exception as exc:
        _llm_log(f"model call failed after {time.monotonic() - started:.1f}s")
        return StructuredOutputResult(
            ok=False,
            error=_sanitize_text(f"Model call failed: {exc}"),
        )
    finally:
        _stop_heartbeat(heartbeat)
    _llm_log(f"model responded in {time.monotonic() - started:.1f}s")

    raw_content = _extract_content(response)
    sanitized_raw_content = _sanitize_text(raw_content)
    try:
        payload, parse_notes = _parse_json_object(raw_content)
    except ValueError as exc:
        return StructuredOutputResult(
            ok=False,
            error=_sanitize_text(f"Could not parse model response: {exc}"),
            raw_response=sanitized_raw_content,
            parse_notes=[],
        )

    if normalizer is not None:
        payload = normalizer(payload)

    try:
        value = schema.model_validate(payload)
    except ValidationError as exc:
        return StructuredOutputResult(
            ok=False,
            error=_sanitize_text(f"Could not validate model response: {exc}"),
            raw_response=sanitized_raw_content,
            candidate_payload=payload,
            parse_notes=parse_notes,
        )

    return StructuredOutputResult(
        ok=True,
        value=value,
        raw_response=sanitized_raw_content,
        candidate_payload=payload,
        parse_notes=parse_notes,
    )


def repair_structured_output(
    *,
    requirement: str,
    schema: type[T],
    model: str | None = None,
    previous_spec: BaseModel,
    validation_errors: list[str],
    client: Any | None = None,
    normalizer: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> StructuredOutputResult[T]:
    repair_requirement = (
        f"{requirement}\n\n"
        "The previous structured output failed validation. "
        "Return a corrected JSON object only.\n"
        f"{_repair_guidance_for_errors(validation_errors)}"
        f"Validation errors: {validation_errors}\n"
        f"Previous output: {previous_spec.model_dump(mode='json')}"
    )
    return generate_structured_output(
        requirement=repair_requirement,
        schema=schema,
        model=model,
        client=client,
        normalizer=normalizer,
    )


_CAPABILITY_RENDERER_ERROR_MARKER = (
    "non-executable complex-only plans must use supported_renderer='none'"
)


def _repair_guidance_for_errors(validation_errors: list[str]) -> str:
    """Targeted, error-specific repair rules appended to the repair prompt.

    The generic 'return corrected JSON' nudge gives the model no actionable
    hint, so it often repeats the same mistake. When the failure is the
    capability_report consistency rule, spell out exactly what must change.
    """
    if not any(_CAPABILITY_RENDERER_ERROR_MARKER in error for error in validation_errors):
        return ""
    return (
        "The previous response failed validation because a non-executable "
        "complex-only plan used a supported_renderer other than \"none\".\n\n"
        "Repair the JSON only.\n\n"
        "Required rule:\n"
        "- If model_spec is null\n"
        "- and complex_model is not null\n"
        "- and capability_report.is_executable is false\n"
        "then capability_report.supported_renderer must be exactly \"none\".\n"
        "Also set executable_subsystems to [].\n"
        "Do not change physical modeling fields unless they are invalid.\n\n"
    )


def normalize_capability_report(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize deterministic SimulationPlan draft inconsistencies.

    The LLM sometimes emits a complex-only plan that is marked
    ``is_executable=false`` but keeps ``supported_renderer`` set to a concrete
    renderer such as ``assembly``. ``SimulationPlan`` rejects that combination
    and the whole plan collapses to null before the local capability assessor
    can override the report.

    The capability_report is a draft the executor recomputes locally anyway
    (see ``_capability_for_plan``), so relax only this consistency field before
    Pydantic validation. Physical modeling fields (materials, geometry, lattice
    layout, cross-section paths, settings, ...) are never touched.
    """
    capability = raw.get("capability_report")
    if not isinstance(capability, dict):
        return raw
    if (
        raw.get("model_spec") is None
        and raw.get("complex_model") is not None
        and capability.get("is_executable") is False
        and capability.get("supported_renderer") != "none"
    ):
        previous_renderer = capability.get("supported_renderer")
        capability["supported_renderer"] = "none"
        capability["executable_subsystems"] = []
        _normalize_log(
            "non-executable complex-only plan: forced supported_renderer='none' "
            f"(was {previous_renderer!r})"
        )
    return raw

def _build_messages(requirement: str, schema: type[BaseModel]) -> list[dict[str, str]]:
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    return [
        {
            "role": "system",
            "content": system_prompt_for_schema(schema),
        },
        {
            "role": "user",
            "content": (
                f"Case requirement and per-run context:\n{requirement}\n\n"
                f"Target Pydantic JSON schema: {schema_json}"
            ),
        },
    ]


def _client_for_model(model: str) -> Any:
    provider, _ = _split_model(model)
    if provider == "zhipu":
        return ZhipuChatClient()
    if provider == "deepseek":
        return DeepSeekChatClient()
    return aisuite.Client()


def _split_model(model: str) -> tuple[str, str]:
    if ":" not in model:
        raise ValueError(f"Invalid model format. Expected 'provider:model', got {model!r}")
    return model.split(":", 1)


def _response_from_openai_compatible_payload(payload: dict[str, Any]) -> Any:
    choices = payload.get("choices")
    if not choices:
        raise ValueError("response did not contain choices")

    content = choices[0].get("message", {}).get("content")
    return _SimpleResponse(content)


def _consume_sse_stream(
    *,
    http_client: Any,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: float,
    started_at: float,
) -> str:
    """Read an OpenAI-compatible SSE stream and return concatenated content.

    Zhipu (and other OpenAI-compatible servers) emit ``data: {json}`` lines
    with ``choices[0].delta.content``. Reasoning models (e.g. GLM-4.5/4.6/5)
    stream the answer via ``delta.reasoning_content`` while ``content`` is
    empty, so we fall back to it to avoid dropping the response as "only
    chunks, no content". We count any delta for progress logging. The stream
    terminates on a ``data: [DONE]`` sentinel.
    """
    content_parts: list[str] = []
    chunk_count = 0
    last_progress = started_at
    with http_client.stream(
        "POST", url, headers=headers, json=payload, timeout=timeout
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            text = line.strip() if isinstance(line, str) else str(line).strip()
            if not text or not text.startswith("data:"):
                continue
            data = text[len("data:") :].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            delta = _first_choice_delta(chunk)
            if not delta:
                continue
            chunk_count += 1
            text = delta.get("content") or delta.get("reasoning_content")
            if text:
                content_parts.append(text)
            now = time.monotonic()
            if _llm_progress_enabled and now - last_progress >= 5.0:
                _llm_log(
                    f"... streaming, {chunk_count} chunks / "
                    f"{sum(len(p) for p in content_parts)} content chars "
                    f"({time.monotonic() - started_at:.0f}s elapsed)"
                )
                last_progress = now
    return "".join(content_parts)


def _first_choice_delta(chunk: dict[str, Any]) -> dict[str, Any] | None:
    choices = chunk.get("choices")
    if not choices:
        return None
    delta = choices[0].get("delta")
    if not isinstance(delta, dict):
        return None
    return delta


class _SimpleResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_SimpleChoice(content)]


class _SimpleChoice:
    def __init__(self, content: str) -> None:
        self.message = _SimpleMessage(content)


class _SimpleMessage:
    def __init__(self, content: str) -> None:
        self.content = content


def _extract_content(response: Any) -> str:
    try:
        return response.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as exc:
        raise ValueError(f"Unexpected response shape: {exc}") from exc


def _parse_json_object(content: str) -> tuple[dict[str, Any], list[str]]:
    parse_notes: list[str] = []
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
        parse_notes.append("stripped_markdown_fence")

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("response did not contain a JSON object")

    json_text = text[start : end + 1]
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as exc:
        repaired_text = _repair_missing_json_commas(json_text)
        if repaired_text == json_text:
            raise ValueError(str(exc)) from exc
        try:
            payload = json.loads(repaired_text)
            parse_notes.append("repaired_missing_json_commas")
        except json.JSONDecodeError:
            raise ValueError(str(exc)) from exc

    if not isinstance(payload, dict):
        raise ValueError("top-level JSON value must be an object")
    return payload, parse_notes


_JSON_VALUE_END_RE = re.compile(
    r'(?m)(?P<value>(?:\}|\]|"(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null))'
    r'(?P<ws>[ \t]*\r?\n[ \t]*)'
    r'(?P<next>(?:"[^"\n\r]+":|\{))'
)


def _repair_missing_json_commas(text: str) -> str:
    """Repair obvious LLM JSON punctuation misses at line boundaries.

    The model most often emits almost-valid JSON with a missing comma between
    adjacent object fields or adjacent array objects. Keep this intentionally
    narrow: it runs only after strict parsing failed and inserts commas only
    where a complete JSON scalar/container is followed on the next line by a
    property name or another object.
    """

    previous = text
    for _ in range(4):
        repaired = _JSON_VALUE_END_RE.sub(r"\g<value>,\g<ws>\g<next>", previous)
        if repaired == previous:
            return repaired
        previous = repaired
    return previous


def _sanitize_text(text: str) -> str:
    sanitized = text
    for name, value in os.environ.items():
        if not _looks_like_secret_env(name, value):
            continue
        sanitized = sanitized.replace(value, "[redacted]")
    return sanitized


def _looks_like_secret_env(name: str, value: str) -> bool:
    if len(value) < 8:
        return False
    secret_markers = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
    return any(marker in name.upper() for marker in secret_markers)


def _env_seconds(env_name: str, explicit: float | None, default: float) -> float:
    if explicit is not None:
        return explicit
    value = os.getenv(env_name)
    if not value:
        return default
    try:
        seconds = float(value)
    except ValueError as exc:
        raise ValueError(f"{env_name} must be a number") from exc
    if seconds <= 0:
        raise ValueError(f"{env_name} must be greater than 0")
    return seconds


def _env_int(env_name: str, explicit: int | None, default: int, *, min_value: int = 0) -> int:
    if explicit is not None:
        return explicit
    value = os.getenv(env_name)
    if not value:
        return default
    try:
        result = int(value)
    except ValueError as exc:
        raise ValueError(f"{env_name} must be an integer") from exc
    if result < min_value:
        raise ValueError(f"{env_name} must be greater than or equal to {min_value}")
    return result


def _stream_enabled() -> bool:
    """Streaming is on by default for OpenAI-compatible providers.

    Set ``OPENMC_AGENT_STREAM=0`` to force non-streaming requests (e.g. for a
    fake client that only implements plain POST). On by default so slow
    reasoning models do not hit the non-streaming read-timeout wall and so
    token-level progress is visible.
    """
    value = os.getenv("OPENMC_AGENT_STREAM", "").strip().lower()
    if not value:
        return True
    return value not in {"0", "false", "no", "off"}
