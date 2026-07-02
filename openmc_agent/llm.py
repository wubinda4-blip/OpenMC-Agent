import json
import os
import time
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

import aisuite
import httpx
from pydantic import BaseModel, ValidationError


T = TypeVar("T", bound=BaseModel)
DEFAULT_MODEL = "zhipu:glm-5.2"
ZHIPU_DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
ZHIPU_DEFAULT_TIMEOUT_SECONDS = 180.0
ZHIPU_DEFAULT_MAX_RETRIES = 2


@dataclass(frozen=True)
class StructuredOutputResult(Generic[T]):
    ok: bool
    value: T | None = None
    error: str = ""
    raw_response: str = ""


class ZhipuChatClient:
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
        self.base_url = base_url or os.getenv("ZHIPUAI_BASE_URL", ZHIPU_DEFAULT_BASE_URL)
        self.http_client = http_client or httpx.Client()
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_sleep_seconds = retry_sleep_seconds
        self.chat = _ZhipuChat(self)


class _ZhipuChat:
    def __init__(self, client: ZhipuChatClient) -> None:
        self.completions = _ZhipuCompletions(client)


class _ZhipuCompletions:
    def __init__(self, client: ZhipuChatClient) -> None:
        self.client = client

    def create(self, *, model: str, messages: list[dict], **kwargs: Any) -> Any:
        api_key = self.client.api_key or os.getenv("ZHIPUAI_API_KEY")
        if not api_key:
            raise RuntimeError("ZHIPUAI_API_KEY is required for zhipu models")

        provider, model_name = _split_model(model)
        if provider != "zhipu":
            raise ValueError(f"ZhipuChatClient only supports zhipu models, got {model!r}")

        payload = {
            "model": model_name.lower(),
            "messages": messages,
        }
        payload.update(kwargs)
        timeout = _zhipu_timeout_seconds(self.client.timeout)
        max_retries = _zhipu_max_retries(self.client.max_retries)
        response = self._post_with_retries(
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
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
            try:
                return self.client.http_client.post(
                    self.client.base_url,
                    headers=headers,
                    json=payload,
                    timeout=timeout,
                )
            except httpx.TransportError:
                if attempt >= max_retries:
                    raise
                if self.client.retry_sleep_seconds > 0:
                    time.sleep(self.client.retry_sleep_seconds)
        raise RuntimeError("unreachable retry loop state")


def generate_structured_output(
    *,
    requirement: str,
    schema: type[T],
    model: str | None = None,
    client: Any | None = None,
) -> StructuredOutputResult[T]:
    selected_model = model or os.getenv("OPENMC_AGENT_MODEL", DEFAULT_MODEL)
    llm_client = client or _client_for_model(selected_model)
    messages = _build_messages(requirement, schema)

    try:
        response = llm_client.chat.completions.create(
            model=selected_model,
            messages=messages,
            temperature=0,
        )
    except Exception as exc:
        return StructuredOutputResult(
            ok=False,
            error=_sanitize_text(f"Model call failed: {exc}"),
        )

    raw_content = _extract_content(response)
    sanitized_raw_content = _sanitize_text(raw_content)
    try:
        payload = _parse_json_object(raw_content)
    except ValueError as exc:
        return StructuredOutputResult(
            ok=False,
            error=_sanitize_text(f"Could not parse model response: {exc}"),
            raw_response=sanitized_raw_content,
        )

    try:
        value = schema.model_validate(payload)
    except ValidationError as exc:
        return StructuredOutputResult(
            ok=False,
            error=_sanitize_text(f"Could not validate model response: {exc}"),
            raw_response=sanitized_raw_content,
        )

    return StructuredOutputResult(ok=True, value=value, raw_response=sanitized_raw_content)


def repair_structured_output(
    *,
    requirement: str,
    schema: type[T],
    model: str | None = None,
    previous_spec: BaseModel,
    validation_errors: list[str],
    client: Any | None = None,
) -> StructuredOutputResult[T]:
    repair_requirement = (
        f"{requirement}\n\n"
        "The previous structured output failed validation. "
        "Return a corrected JSON object only.\n"
        f"Validation errors: {validation_errors}\n"
        f"Previous output: {previous_spec.model_dump(mode='json')}"
    )
    return generate_structured_output(
        requirement=repair_requirement,
        schema=schema,
        model=model,
        client=client,
    )


def _build_messages(requirement: str, schema: type[BaseModel]) -> list[dict[str, str]]:
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    return [
        {
            "role": "system",
            "content": (
                "You generate structured data for OpenMC model construction. "
                "Return exactly one JSON object and no surrounding prose."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Requirement: {requirement}\n"
                f"Target Pydantic JSON schema: {schema_json}"
            ),
        },
    ]


def _client_for_model(model: str) -> Any:
    provider, _ = _split_model(model)
    if provider == "zhipu":
        return ZhipuChatClient()
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


def _parse_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("response did not contain a JSON object")

    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(str(exc)) from exc

    if not isinstance(payload, dict):
        raise ValueError("top-level JSON value must be an object")
    return payload


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


def _zhipu_timeout_seconds(explicit_timeout: float | None) -> float:
    if explicit_timeout is not None:
        return explicit_timeout
    value = os.getenv("ZHIPUAI_TIMEOUT_SECONDS")
    if not value:
        return ZHIPU_DEFAULT_TIMEOUT_SECONDS
    try:
        timeout = float(value)
    except ValueError as exc:
        raise ValueError("ZHIPUAI_TIMEOUT_SECONDS must be a number") from exc
    if timeout <= 0:
        raise ValueError("ZHIPUAI_TIMEOUT_SECONDS must be greater than 0")
    return timeout


def _zhipu_max_retries(explicit_retries: int | None) -> int:
    if explicit_retries is not None:
        return explicit_retries
    value = os.getenv("ZHIPUAI_MAX_RETRIES")
    if not value:
        return ZHIPU_DEFAULT_MAX_RETRIES
    try:
        retries = int(value)
    except ValueError as exc:
        raise ValueError("ZHIPUAI_MAX_RETRIES must be an integer") from exc
    if retries < 0:
        raise ValueError("ZHIPUAI_MAX_RETRIES must be greater than or equal to 0")
    return retries
