import base64
import json

import httpx
import pytest

from paintai.config import ProviderConfig
from paintai.models import Painter, Plan
from paintai.providers import CompatibleProvider, MockProvider, ProviderError


@pytest.fixture
def painter():
    return Painter(painter_id="test", name="测试", style="水墨")


@pytest.fixture
def plan(painter):
    return MockProvider().plan("山", painter)


def config(monkeypatch, **kwargs):
    monkeypatch.setenv("PAINTAI_TEST_KEY", "test-secret")
    return ProviderConfig(
        provider="openai_compatible",
        model="replaceable-model",
        base_url="https://api.example.test/v1",
        api_key_env="PAINTAI_TEST_KEY",
        **kwargs,
    )


@pytest.mark.parametrize("mode", ["json_schema", "json_object", "prompt"])
def test_chat_modes_and_configurable_model(monkeypatch, painter, plan, mode):
    def handler(request):
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-secret"
        body = json.loads(request.content)
        assert body["model"] == "replaceable-model"
        if mode == "prompt":
            assert "response_format" not in body
        else:
            assert body["response_format"]["type"] == mode
        assert "山" in body["messages"][1]["content"][0]["text"]
        return httpx.Response(
            200, json={"choices": [{"message": {"content": plan.model_dump_json()}}]}
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = CompatibleProvider(config(monkeypatch, structured_output=mode), client)
        assert provider.plan("山", painter) == plan


def test_image_base64_and_generator_parameters(monkeypatch, plan):
    raw = MockProvider().generate(plan)

    def handler(request):
        assert request.url.path == "/v1/images/generations"
        body = json.loads(request.content)
        assert body["n"] == 1 and body["prompt"] == plan.prompt
        assert body["size"] == "1024x1024"
        assert "response_format" not in body
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(raw).decode()}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = CompatibleProvider(config(monkeypatch, parameters={"size": "1024x1024"}), client)
        assert provider.generate(plan) == raw


def test_image_download_does_not_leak_authorization(monkeypatch, plan):
    raw = MockProvider().generate(plan)

    def handler(request):
        if request.method == "POST":
            return httpx.Response(
                200, json={"data": [{"url": "https://images.example.test/image.png"}]}
            )
        assert "authorization" not in request.headers
        return httpx.Response(200, content=raw)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert CompatibleProvider(config(monkeypatch), client).generate(plan) == raw


def test_critic_sends_actual_image_and_validates_scores(monkeypatch, painter, plan, tmp_path):
    path = tmp_path / "image.png"
    path.write_bytes(MockProvider().generate(plan))
    expected = MockProvider().evaluate(path, "山", painter, plan)

    def handler(request):
        content = json.loads(request.content)["messages"][1]["content"]
        assert base64.b64decode(content[1]["image_url"]["url"].split(",")[1]) == path.read_bytes()
        result = expected.model_dump(exclude={"overall"})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = CompatibleProvider(config(monkeypatch), client).evaluate(path, "山", painter, plan)
        assert result.overall == 5


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {"content": "not JSON"}}]},
        {"choices": [{"message": {"refusal": "no"}}]},
    ],
)
def test_malformed_or_refused_chat_is_explicit_error(monkeypatch, painter, payload):
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    ) as client:
        with pytest.raises(ProviderError):
            CompatibleProvider(config(monkeypatch), client).plan("山", painter)


def test_http_error_is_redacted_and_not_retried(monkeypatch, plan):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, text="test-secret sensitive server error")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError, match="429") as failure:
            CompatibleProvider(config(monkeypatch), client).generate(plan)
    assert "test-secret" not in str(failure.value)
    assert len(calls) == 1


def test_timeout_is_not_retried(monkeypatch, plan):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("sensitive url", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError, match="timed out"):
            CompatibleProvider(config(monkeypatch), client).generate(plan)
    assert len(calls) == 1


def test_plan_schema_contains_no_optional_defaults():
    schema = Plan.model_json_schema()
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["additionalProperties"] is False


def test_local_api_needs_no_secret(monkeypatch, painter, plan):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    local = ProviderConfig(
        provider="openai_compatible",
        model="paintai-local",
        base_url="http://127.0.0.1:18080/v1",
        auth="none",
    )

    def handler(request):
        assert "authorization" not in request.headers
        return httpx.Response(
            200, json={"choices": [{"message": {"content": plan.model_dump_json()}}]}
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert CompatibleProvider(local, client).plan("山", painter) == plan


def test_no_auth_rejected_for_remote_api():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="loopback"):
        ProviderConfig(provider="openai_compatible", model="remote", auth="none")


def test_truncated_structured_response_is_rejected(monkeypatch, painter, plan):
    payload = {
        "choices": [{"finish_reason": "length", "message": {"content": plan.model_dump_json()}}]
    }
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    ) as client:
        with pytest.raises(ProviderError, match="truncated"):
            CompatibleProvider(config(monkeypatch), client).plan("forest", painter)
