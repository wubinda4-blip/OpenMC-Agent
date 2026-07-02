import httpx
import pytest
from types import SimpleNamespace

from openmc_agent.llm import ZhipuChatClient, generate_structured_output
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
