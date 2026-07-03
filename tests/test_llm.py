import copy
import json

import httpx
import pytest
from types import SimpleNamespace

from openmc_agent.llm import (
    DeepSeekChatClient,
    ZhipuChatClient,
    generate_structured_output,
    set_llm_progress,
)


@pytest.fixture(autouse=True)
def _non_streaming_by_default(monkeypatch):
    """Production defaults to streaming, but these unit tests use a fake HTTP
    client that only implements plain ``.post()``. Disable streaming module-wide
    so the non-streaming path is exercised; streaming tests opt back in with
    ``OPENMC_AGENT_STREAM=true``."""
    monkeypatch.setenv("OPENMC_AGENT_STREAM", "0")
from openmc_agent.schemas import MaterialSpec, SimulationPlan


class FakeCompletions:
    def __init__(self, content: str | Exception) -> None:
        self.content = content
        self.calls: list[dict] = []

    def create(self, *, model: str, messages: list[dict], **kwargs):
        self.calls.append({"model": model, "messages": messages, "kwargs": kwargs})
        if isinstance(self.content, Exception):
            raise self.content
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=self.content),
                )
            ]
        )


class FakeClient:
    def __init__(self, content: str | Exception) -> None:
        self.completions = FakeCompletions(content)
        self.chat = SimpleNamespace(completions=self.completions)


def test_generate_structured_output_parses_material_spec() -> None:
    client = FakeClient(
        """
        {
          "name": "UO2 fuel",
          "density_unit": "g/cm3",
          "density_value": 10.4,
          "composition": [
            {"name": "U235", "percent": 4.95, "percent_type": "ao"},
            {"name": "U238", "percent": 95.05, "percent_type": "ao"},
            {"name": "O16", "percent": 200.0, "percent_type": "ao"}
          ]
        }
        """
    )

    result = generate_structured_output(
        requirement="创建 UO2 燃料",
        schema=MaterialSpec,
        model="test:material-model",
        client=client,
    )

    assert result.ok is True
    assert result.value is not None
    assert result.value.name == "UO2 fuel"
    assert client.completions.calls[0]["model"] == "test:material-model"


def test_structured_output_separates_system_prompt_from_case_requirement() -> None:
    client = FakeClient(
        """
        {
          "name": "UO2 fuel",
          "density_unit": "g/cm3",
          "density_value": 10.4,
          "composition": [{"name": "U235", "percent": 4.95}]
        }
        """
    )

    result = generate_structured_output(
        requirement="创建 UO2 燃料；这段文字来自 case.md",
        schema=MaterialSpec,
        model="test:material-model",
        client=client,
    )

    assert result.ok is True
    messages = client.completions.calls[0]["messages"]
    assert "You are an OpenMC modeling agent" in messages[0]["content"]
    assert "Do not invent material composition" in messages[0]["content"]
    assert "case.md" not in messages[0]["content"]
    assert "Case requirement and per-run context" in messages[1]["content"]
    assert "这段文字来自 case.md" in messages[1]["content"]


def test_generate_structured_output_parses_simulation_plan() -> None:
    client = FakeClient(
        """
        {
          "schema_version": "simulation_plan.v1",
          "model_spec": {
            "name": "UO2 pin-cell plan",
            "kind": "pin_cell",
            "pin_cell": {
              "fuel": {
                "name": "UO2 fuel",
                "density_unit": "g/cm3",
                "density_value": 10.4,
                "composition": [
                  {"name": "U235", "percent": 4.95},
                  {"name": "U238", "percent": 95.05},
                  {"name": "O16", "percent": 200.0}
                ]
              },
              "moderator": {
                "name": "Water moderator",
                "density_unit": "g/cm3",
                "density_value": 1.0,
                "composition": [
                  {"name": "H1", "percent": 2.0},
                  {"name": "O16", "percent": 1.0}
                ]
              },
              "geometry": {"fuel_radius_cm": 0.41, "pitch_cm": 1.26}
            },
            "settings": {"batches": 50, "inactive": 10, "particles": 1000}
          },
          "plot_specs": [
            {
              "basis": "xy",
              "origin": [0.0, 0.0, 0.0],
              "width_cm": [1.26, 1.26],
              "pixels": [500, 500],
              "color_by": "material",
              "filename": "pin_cell_xy.png"
            }
          ],
          "execution_check": {
            "settings": {"batches": 5, "inactive": 1, "particles": 100},
            "expected_checks": ["geometry check"]
          },
          "expert_assumptions": ["Reflective pin-cell boundary condition."]
        }
        """
    )

    result = generate_structured_output(
        requirement="建立一个 UO2 pin-cell，并给出几何检查图和 smoke test",
        schema=SimulationPlan,
        model="test:plan-model",
        client=client,
    )

    assert result.ok is True
    assert result.value is not None
    assert result.value.model_spec.name == "UO2 pin-cell plan"
    assert result.value.plot_specs[0].basis == "xy"
    assert result.value.execution_check.settings.particles == 100
    prompt = client.completions.calls[0]["messages"][1]["content"]
    assert "plot_specs" in prompt
    assert "execution_check" in prompt


def test_generate_structured_output_reports_missing_plan_plot_specs() -> None:
    result = generate_structured_output(
        requirement="建立一个 UO2 pin-cell",
        schema=SimulationPlan,
        model="test:plan-model",
        client=FakeClient(
            """
            {
              "model_spec": {
                "name": "Missing plots",
                "kind": "pin_cell",
                "pin_cell": {
                  "fuel": {
                    "name": "UO2 fuel",
                    "density_unit": "g/cm3",
                    "density_value": 10.4,
                    "composition": [{"name": "U235", "percent": 1.0}]
                  },
                  "moderator": {
                    "name": "Water",
                    "density_unit": "g/cm3",
                    "density_value": 1.0,
                    "composition": [{"name": "H1", "percent": 2.0}]
                  },
                  "geometry": {"fuel_radius_cm": 0.41, "pitch_cm": 1.26}
                }
              }
            }
            """
        ),
    )

    assert result.ok is False
    assert "Could not validate model response" in result.error
    assert "plot_specs" in result.error


def test_generate_structured_output_sanitizes_raw_response_secrets(monkeypatch) -> None:
    monkeypatch.setenv("ZHIPUAI_API_KEY", "super-secret-token")

    result = generate_structured_output(
        requirement="创建 UO2 燃料",
        schema=MaterialSpec,
        model="test:material-model",
        client=FakeClient("not json super-secret-token"),
    )

    assert result.ok is False
    assert "super-secret-token" not in result.raw_response
    assert "[redacted]" in result.raw_response


def test_generate_structured_output_uses_default_model_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("OPENMC_AGENT_MODEL", "test:from-env")
    client = FakeClient(
        """
        {
          "name": "Water",
          "density_unit": "g/cm3",
          "density_value": 1.0,
          "composition": [
            {"name": "H1", "percent": 2.0},
            {"name": "O16", "percent": 1.0}
          ]
        }
        """
    )

    result = generate_structured_output(
        requirement="创建水材料",
        schema=MaterialSpec,
        client=client,
    )

    assert result.ok is True
    assert client.completions.calls[0]["model"] == "test:from-env"


def test_generate_structured_output_returns_readable_error_on_invalid_json() -> None:
    result = generate_structured_output(
        requirement="创建 UO2 燃料",
        schema=MaterialSpec,
        model="test:material-model",
        client=FakeClient("not json"),
    )

    assert result.ok is False
    assert result.value is None
    assert "Could not parse model response" in result.error


def test_generate_structured_output_returns_readable_error_on_client_failure() -> None:
    result = generate_structured_output(
        requirement="创建 UO2 燃料",
        schema=MaterialSpec,
        model="test:material-model",
        client=FakeClient(RuntimeError("provider unavailable")),
    )

    assert result.ok is False
    assert result.value is None
    assert "Model call failed" in result.error


class FakeHttpResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class FakeHttpClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def post(self, url: str, *, headers: dict, json: dict, timeout: float):
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "json": json,
                "timeout": timeout,
            }
        )
        return FakeHttpResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": """
                            {
                              "name": "UO2 fuel",
                              "density_unit": "g/cm3",
                              "density_value": 10.4,
                              "composition": [
                                {"name": "U235", "percent": 4.95},
                                {"name": "U238", "percent": 95.05},
                                {"name": "O16", "percent": 200.0}
                              ]
                            }
                            """
                        }
                    }
                ]
            }
        )


class RetryHttpClient:
    def __init__(
        self,
        first_error: Exception | None = None,
    ) -> None:
        self.calls: list[dict] = []
        self.first_error = first_error or httpx.ReadTimeout("The read operation timed out")

    def post(self, url: str, *, headers: dict, json: dict, timeout: float):
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "json": json,
                "timeout": timeout,
            }
        )
        if len(self.calls) == 1:
            raise self.first_error
        return FakeHttpResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": """
                            {
                              "name": "UO2 fuel",
                              "density_unit": "g/cm3",
                              "density_value": 10.4,
                              "composition": [
                                {"name": "U235", "percent": 4.95},
                                {"name": "U238", "percent": 95.05},
                                {"name": "O16", "percent": 200.0}
                              ]
                            }
                            """
                        }
                    }
                ]
            }
        )


def test_generate_structured_output_routes_zhipu_models_through_zhipu_client(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ZHIPUAI_API_KEY", "test-key")
    http_client = FakeHttpClient()

    result = generate_structured_output(
        requirement="创建 UO2 燃料",
        schema=MaterialSpec,
        model="zhipu:GLM-5.2",
        client=ZhipuChatClient(http_client=http_client),
    )

    assert result.ok is True
    assert result.value is not None
    assert result.value.name == "UO2 fuel"
    call = http_client.calls[0]
    assert call["url"] == "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer test-key"
    assert call["json"]["model"] == "glm-5.2"
    assert call["json"]["temperature"] == 0
    assert call["timeout"] == 180.0


def test_zhipu_client_uses_timeout_environment(monkeypatch) -> None:
    monkeypatch.setenv("ZHIPUAI_API_KEY", "test-key")
    monkeypatch.setenv("ZHIPUAI_TIMEOUT_SECONDS", "240")
    http_client = FakeHttpClient()

    result = generate_structured_output(
        requirement="创建 UO2 燃料",
        schema=MaterialSpec,
        model="zhipu:glm-5.2",
        client=ZhipuChatClient(http_client=http_client),
    )

    assert result.ok is True
    assert http_client.calls[0]["timeout"] == 240.0


def test_zhipu_client_retries_read_timeout(monkeypatch) -> None:
    monkeypatch.setenv("ZHIPUAI_API_KEY", "test-key")
    monkeypatch.setenv("ZHIPUAI_MAX_RETRIES", "1")
    http_client = RetryHttpClient()

    result = generate_structured_output(
        requirement="创建 UO2 燃料",
        schema=MaterialSpec,
        model="zhipu:glm-5.2",
        client=ZhipuChatClient(http_client=http_client, retry_sleep_seconds=0),
    )

    assert result.ok is True
    assert result.value is not None
    assert result.value.name == "UO2 fuel"
    assert len(http_client.calls) == 2


def test_zhipu_client_retries_transient_ssl_eof(monkeypatch) -> None:
    monkeypatch.setenv("ZHIPUAI_API_KEY", "test-key")
    monkeypatch.setenv("ZHIPUAI_MAX_RETRIES", "1")
    http_client = RetryHttpClient(
        httpx.ConnectError(
            "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol"
        )
    )

    result = generate_structured_output(
        requirement="创建 UO2 燃料",
        schema=MaterialSpec,
        model="zhipu:glm-5.2",
        client=ZhipuChatClient(http_client=http_client, retry_sleep_seconds=0),
    )

    assert result.ok is True
    assert result.value is not None
    assert result.value.name == "UO2 fuel"
    assert len(http_client.calls) == 2


def test_zhipu_client_rejects_invalid_timeout_environment(monkeypatch) -> None:
    monkeypatch.setenv("ZHIPUAI_API_KEY", "test-key")
    monkeypatch.setenv("ZHIPUAI_TIMEOUT_SECONDS", "bad")

    result = generate_structured_output(
        requirement="创建 UO2 燃料",
        schema=MaterialSpec,
        model="zhipu:glm-5.2",
        client=ZhipuChatClient(http_client=FakeHttpClient()),
    )

    assert result.ok is False
    assert "ZHIPUAI_TIMEOUT_SECONDS" in result.error


def test_zhipu_client_requires_api_key(monkeypatch) -> None:
    monkeypatch.delenv("ZHIPUAI_API_KEY", raising=False)

    result = generate_structured_output(
        requirement="创建 UO2 燃料",
        schema=MaterialSpec,
        model="zhipu:glm-5.2",
        client=ZhipuChatClient(http_client=FakeHttpClient()),
    )

    assert result.ok is False
    assert "ZHIPUAI_API_KEY" in result.error


def test_llm_progress_logs_request_and_response_when_enabled(
    monkeypatch, capsys
) -> None:
    monkeypatch.setenv("ZHIPUAI_API_KEY", "test-key")
    set_llm_progress(True)
    try:
        result = generate_structured_output(
            requirement="创建 UO2 燃料",
            schema=MaterialSpec,
            model="zhipu:glm-5.2",
            client=ZhipuChatClient(http_client=FakeHttpClient()),
        )
    finally:
        set_llm_progress(False)

    assert result.ok is True
    stderr = capsys.readouterr().err
    assert "[llm] requesting structured output model=zhipu:glm-5.2" in stderr
    assert "[llm] POST https://open.bigmodel.cn" in stderr
    assert "[llm] model responded in" in stderr


def test_llm_progress_logs_transport_retry_when_enabled(monkeypatch, capsys) -> None:
    monkeypatch.setenv("ZHIPUAI_API_KEY", "test-key")
    monkeypatch.setenv("ZHIPUAI_MAX_RETRIES", "1")
    set_llm_progress(True)
    try:
        result = generate_structured_output(
            requirement="创建 UO2 燃料",
            schema=MaterialSpec,
            model="zhipu:glm-5.2",
            client=ZhipuChatClient(http_client=RetryHttpClient(), retry_sleep_seconds=0),
        )
    finally:
        set_llm_progress(False)

    assert result.ok is True
    stderr = capsys.readouterr().err
    assert "transport error" in stderr
    assert "retrying in" in stderr
    assert "attempt 2/2" in stderr


def test_llm_progress_is_silent_when_disabled(capsys) -> None:
    set_llm_progress(False)
    result = generate_structured_output(
        requirement="创建 UO2 燃料",
        schema=MaterialSpec,
        model="test:material-model",
        client=FakeClient(
            """
            {
              "name": "UO2 fuel",
              "density_unit": "g/cm3",
              "density_value": 10.4,
              "composition": [{"name": "U235", "percent": 4.95}]
            }
            """
        ),
    )

    assert result.ok is True
    assert capsys.readouterr().err == ""


def _sse_line(content: str = "", reasoning_content: str = "") -> str:
    import json as _json

    delta: dict = {}
    if content:
        delta["content"] = content
    if reasoning_content:
        delta["reasoning_content"] = reasoning_content
    return "data: " + _json.dumps({"choices": [{"delta": delta}]})


class _FakeStreamResponse:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def raise_for_status(self) -> None:
        return None

    def iter_lines(self):
        return iter(self._lines)


class _FakeStreamContextManager:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def __enter__(self) -> _FakeStreamResponse:
        return _FakeStreamResponse(self._lines)

    def __exit__(self, *exc) -> bool:
        return False


class FakeStreamHttpClient:
    def __init__(self, lines: list[str] | None = None) -> None:
        self.calls: list[dict] = []
        self._lines = lines

    def stream(self, method: str, url: str, *, headers: dict, json: dict, timeout: float):
        self.calls.append(
            {"method": method, "url": url, "headers": headers, "json": json, "timeout": timeout}
        )
        if self._lines is None:
            self._lines = [
                _sse_line('{"name": "UO2 fuel", '),
                _sse_line('"density_unit": "g/cm3", "density_value": 10.4, '),
                _sse_line('"composition": [{"name": "U235", "percent": 4.95}]}'),
                "data: [DONE]",
            ]
        return _FakeStreamContextManager(self._lines)


def test_zhipu_streaming_accumulates_content(monkeypatch) -> None:
    monkeypatch.setenv("ZHIPUAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENMC_AGENT_STREAM", "true")
    http_client = FakeStreamHttpClient()

    result = generate_structured_output(
        requirement="创建 UO2 燃料",
        schema=MaterialSpec,
        model="zhipu:glm-5.2",
        client=ZhipuChatClient(http_client=http_client),
    )

    assert result.ok is True
    assert result.value is not None
    assert result.value.name == "UO2 fuel"
    call = http_client.calls[0]
    assert call["method"] == "POST"
    assert call["json"]["stream"] is True
    assert call["json"]["model"] == "glm-5.2"


def test_zhipu_streaming_falls_back_to_reasoning_content(monkeypatch) -> None:
    # Thinking models (GLM-4.5/4.6/5) stream the answer via
    # delta.reasoning_content while delta.content stays empty. The reader
    # must fall back so the response is not dropped as "only chunks, no
    # content".
    monkeypatch.setenv("ZHIPUAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENMC_AGENT_STREAM", "true")
    lines = [
        _sse_line(reasoning_content='{"name": "UO2 fuel", '),
        _sse_line(reasoning_content='"density_unit": "g/cm3", "density_value": 10.4, '),
        _sse_line(reasoning_content='"composition": [{"name": "U235", "percent": 4.95}]}'),
        "data: [DONE]",
    ]
    http_client = FakeStreamHttpClient(lines)

    result = generate_structured_output(
        requirement="创建 UO2 燃料",
        schema=MaterialSpec,
        model="zhipu:glm-5.2",
        client=ZhipuChatClient(http_client=http_client),
    )

    assert result.ok is True
    assert result.value is not None
    assert result.value.name == "UO2 fuel"


def test_streaming_can_be_disabled_via_env(monkeypatch) -> None:
    monkeypatch.setenv("ZHIPUAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENMC_AGENT_STREAM", "0")
    http_client = FakeHttpClient()

    result = generate_structured_output(
        requirement="创建 UO2 燃料",
        schema=MaterialSpec,
        model="zhipu:glm-5.2",
        client=ZhipuChatClient(http_client=http_client),
    )

    assert result.ok is True
    # Non-streaming path: plain post(), no stream key in payload.
    assert "stream" not in http_client.calls[0]["json"]


def test_deepseek_routes_through_deepseek_client(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    http_client = FakeHttpClient()

    result = generate_structured_output(
        requirement="创建 UO2 燃料",
        schema=MaterialSpec,
        model="deepseek:deepseek-chat",
        client=DeepSeekChatClient(http_client=http_client),
    )

    assert result.ok is True
    assert result.value is not None
    assert result.value.name == "UO2 fuel"
    call = http_client.calls[0]
    assert call["url"] == "https://api.deepseek.com/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer ds-key"
    assert call["json"]["model"] == "deepseek-chat"
    assert call["json"]["temperature"] == 0


def test_deepseek_reasoner_strips_temperature(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    http_client = FakeHttpClient()

    result = generate_structured_output(
        requirement="创建 UO2 燃料",
        schema=MaterialSpec,
        model="deepseek:deepseek-reasoner",
        client=DeepSeekChatClient(http_client=http_client),
    )

    assert result.ok is True
    payload = http_client.calls[0]["json"]
    assert payload["model"] == "deepseek-reasoner"
    assert "temperature" not in payload


def test_client_for_model_picks_deepseek() -> None:
    from openmc_agent.llm import _client_for_model

    assert isinstance(_client_for_model("deepseek:deepseek-chat"), DeepSeekChatClient)
    assert isinstance(_client_for_model("zhipu:glm-5.2"), ZhipuChatClient)


# --- capability_report normalization for SimulationPlan (LLM draft fixup) ---


def _complex_only_plan_payload() -> dict:
    """An LLM-style plan draft that is internally inconsistent: a complex-only
    plan marked non-executable but with a concrete supported_renderer."""
    return {
        "schema_version": "simulation_plan.v2",
        "model_spec": None,
        "complex_model": {
            "name": "assembly IR",
            "kind": "assembly",
            "materials": [
                {"id": "fuel", "name": "fuel", "requires_human_confirmation": ["density"]}
            ],
        },
        "capability_report": {
            "is_executable": False,
            "supported_renderer": "assembly",
            "executable_subsystems": ["assemblies"],
        },
        "plot_specs": [
            {"basis": "xy", "width_cm": [2.0, 2.0], "filename": "assembly_xy.png"}
        ],
    }


def _valid_complex_only_plan() -> SimulationPlan:
    return SimulationPlan.model_validate(
        {
            "schema_version": "simulation_plan.v2",
            "model_spec": None,
            "complex_model": {
                "name": "assembly IR",
                "kind": "assembly",
                "materials": [
                    {"id": "fuel", "name": "fuel", "requires_human_confirmation": ["density"]}
                ],
            },
            "capability_report": {"is_executable": False, "supported_renderer": "none"},
            "plot_specs": [
                {"basis": "xy", "width_cm": [2.0, 2.0], "filename": "assembly_xy.png"}
            ],
        }
    )


def test_normalize_non_executable_complex_only_renderer() -> None:
    from openmc_agent.llm import normalize_capability_report

    raw = {
        "model_spec": None,
        "complex_model": {"name": "x", "kind": "assembly"},
        "capability_report": {
            "is_executable": False,
            "supported_renderer": "assembly",
            "executable_subsystems": ["assemblies"],
        },
    }
    result = normalize_capability_report(raw)

    cap = result["capability_report"]
    assert cap["is_executable"] is False
    assert cap["supported_renderer"] == "none"
    assert cap["executable_subsystems"] == []


def test_executable_plan_can_keep_supported_renderer() -> None:
    from openmc_agent.llm import normalize_capability_report

    raw = {
        "capability_report": {
            "is_executable": True,
            "supported_renderer": "pin_cell",
        }
    }
    result = normalize_capability_report(raw)

    assert result["capability_report"]["supported_renderer"] == "pin_cell"


def test_physical_fields_not_changed_by_normalization() -> None:
    from openmc_agent.llm import normalize_capability_report

    complex_model = {
        "name": "x",
        "kind": "assembly",
        "materials": [{"id": "fuel", "name": "fuel", "chemical_formula": "UO2"}],
        "surfaces": [{"id": "s1", "kind": "zcylinder", "parameters": {"r": 0.4}}],
        "cells": [{"id": "c1", "name": "c", "fill_type": "material", "fill_id": "fuel"}],
        "universes": [{"id": "u", "name": "u", "cell_ids": ["c1"]}],
        "lattices": [
            {
                "id": "l",
                "name": "l",
                "kind": "rect",
                "pitch_cm": [1.26, 1.26],
                "universe_pattern": [["u"]],
            }
        ],
        "settings": {"batches": 50, "inactive": 10, "particles": 1000},
    }
    raw = {
        "model_spec": None,
        "complex_model": complex_model,
        "capability_report": {"is_executable": False, "supported_renderer": "assembly"},
    }
    snapshot = copy.deepcopy(complex_model)

    result = normalize_capability_report(raw)

    assert result["complex_model"] == snapshot
    assert result["complex_model"]["materials"][0]["chemical_formula"] == "UO2"
    assert result["complex_model"]["settings"]["particles"] == 1000
    assert result["capability_report"]["supported_renderer"] == "none"


def test_generate_structured_output_rejects_inconsistent_plan_without_normalizer() -> None:
    result = generate_structured_output(
        requirement="建立一个组件模型",
        schema=SimulationPlan,
        model="test:plan-model",
        client=FakeClient(json.dumps(_complex_only_plan_payload())),
    )

    assert result.ok is False
    assert result.value is None
    assert "supported_renderer='none'" in result.error


def test_generate_structured_output_normalizes_inconsistent_capability_report() -> None:
    from openmc_agent.llm import normalize_capability_report

    result = generate_structured_output(
        requirement="建立一个组件模型",
        schema=SimulationPlan,
        model="test:plan-model",
        client=FakeClient(json.dumps(_complex_only_plan_payload())),
        normalizer=normalize_capability_report,
    )

    assert result.ok is True
    assert result.value is not None
    assert result.value.capability_report.supported_renderer == "none"
    assert result.value.capability_report.executable_subsystems == []
    assert result.value.complex_model.kind == "assembly"


def test_repair_prompt_spells_out_capability_renderer_rule() -> None:
    from openmc_agent.llm import normalize_capability_report, repair_structured_output

    client = FakeClient(json.dumps(_complex_only_plan_payload()))
    result = repair_structured_output(
        requirement="建立一个组件模型",
        schema=SimulationPlan,
        model="test:plan-model",
        previous_spec=_valid_complex_only_plan(),
        validation_errors=[
            "non-executable complex-only plans must use supported_renderer='none'"
        ],
        client=client,
        normalizer=normalize_capability_report,
    )

    assert result.ok is True
    prompt = client.completions.calls[0]["messages"][1]["content"]
    assert "non-executable complex-only plan used a supported_renderer other than" in prompt
    assert "capability_report.is_executable is false" in prompt
    assert 'supported_renderer must be exactly "none"' in prompt
    assert "executable_subsystems to []" in prompt
