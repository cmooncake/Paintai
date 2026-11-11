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
        if len(content) > 1:
            assert (
                base64.b64decode(content[1]["image_url"]["url"].split(",")[1]) == path.read_bytes()
            )
        body = json.loads(request.content)
        if body["messages"][0]["content"].startswith("Describe only"):
            result = {"description": "A simple test image."}
        elif body["messages"][0]["content"].startswith("Compare ONE"):
            result = {"evidence": "visible", "verdict": "met"}
        else:
            result = expected.model_dump(exclude={"overall"})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = CompatibleProvider(config(monkeypatch), client).evaluate(path, "山", painter, plan)
        assert result.overall == 5


def test_blind_facts_override_sycophantic_portrait_checks(monkeypatch, painter, plan, tmp_path):
    from paintai.providers import locked_requirements

    topic = "waist-up half-body portrait with entire head visible, long white hair, white and purple dress"
    assert "white and purple dress" in locked_requirements(topic, None).values()
    path = tmp_path / "portrait.png"
    path.write_bytes(MockProvider().generate(plan))

    def handler(request):
        body = json.loads(request.content)
        system = body["messages"][0]["content"]
        content = body["messages"][1]["content"]
        if system.startswith("Describe only"):
            reply = {"description": "深色头发的脸部特写，衣服看不见。"}
        elif system.startswith("Inspect visible"):
            assert topic not in str(content)
            reply = {
                "hair": "black",
                "framing": "face_closeup",
                "head_complete": False,
                "clothing": "看不见",
                "eyes": "蓝色",
            }
        elif system.startswith("Compare ONE"):
            assert (
                base64.b64decode(content[1]["image_url"]["url"].split(",")[1]) == path.read_bytes()
            )
            reply = {"evidence": "完全符合", "verdict": "met"}
        else:
            reply = {
                "scores": dict.fromkeys(
                    ["topic", "technique", "composition", "color", "style", "creativity"], 10
                ),
                "feedback": "白发和半身构图完美还原",
                "improvements": ["继续保持"],
            }
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(reply)}}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        critique = CompatibleProvider(config(monkeypatch), client).evaluate(
            path, topic, painter, plan
        )
    assert critique.scores.topic <= 4 and critique.scores.composition <= 4
    assert "完美还原" not in critique.feedback
    assert "long white hair [missed]" in critique.feedback
    assert "white and purple dress [uncertain]" in critique.feedback
    assert "头顶完整：False" in critique.feedback


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


@pytest.mark.parametrize("length,accepted", [(250, True), (4001, False)])
def test_descriptive_plan_has_separate_bounded_acceptance(
    monkeypatch, painter, plan, length, accepted
):
    data = plan.model_dump()
    data["palette"] = "warm red and muted brown " * 8
    data["prompt"] = "a" * length
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, json={"choices": [{"message": {"content": json.dumps(data)}}]}
            )
        )
    ) as client:
        provider = CompatibleProvider(config(monkeypatch), client)
        if accepted:
            assert provider.plan("山", painter).palette == data["palette"].strip()
        else:
            with pytest.raises(ProviderError):
                provider.plan("山", painter)


@pytest.mark.parametrize("all_ark", [False, True])
def test_bailian_seedream_request_contract(monkeypatch, painter, plan, tmp_path, all_ark):
    from paintai.model_presets import ark_all, bailian_seedream

    monkeypatch.setenv("DASHSCOPE_API_KEY", "fixture-bailian")
    monkeypatch.setenv("ARK_API_KEY", "fixture-ark")
    preset = ark_all() if all_ark else bailian_seedream()
    raw = MockProvider().generate(plan)
    path = tmp_path / "image.png"
    path.write_bytes(raw)
    expected = MockProvider().evaluate(path, "山", painter, plan)
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == "GET":
            assert "authorization" not in request.headers
            return httpx.Response(200, content=raw)
        body = json.loads(request.content)
        if request.url.path.endswith("/images/generations"):
            assert request.url.path == "/api/v3/images/generations"
            assert request.headers["authorization"] == "Bearer fixture-ark"
            assert "n" not in body and body["stream"] is False
            assert body["size"] == "2K" and body["watermark"] is True
            assert body["sequential_image_generation"] == "disabled"
            return httpx.Response(
                200, json={"data": [{"url": "https://images.example.test/image.png"}]}
            )
        assert request.url.path == (
            "/api/v3/chat/completions" if all_ark else "/compatible-mode/v1/chat/completions"
        )
        assert request.headers["authorization"] == (
            "Bearer fixture-ark" if all_ark else "Bearer fixture-bailian"
        )
        if all_ark:
            assert body["thinking"] == {"type": "disabled"}
            assert "enable_thinking" not in body
            assert body["model"] == "doubao-seed-2-0-lite-260428"
        else:
            assert body["enable_thinking"] is False
        assert body["response_format"] == {"type": "json_object"}
        assert "max_tokens" in body and "max_completion_tokens" not in body
        if body["messages"][0]["content"].startswith(
            ("Describe only", "You are an evidence auditor")
        ):
            content = body["messages"][1]["content"]
            if len(content) > 1:
                assert base64.b64decode(content[1]["image_url"]["url"].split(",")[1]) == raw
            result = json.dumps(
                {"description": "A simple test image."}
                if body["messages"][0]["content"].startswith("Describe only")
                else expected.model_dump(exclude={"overall"})
            )
        elif body["messages"][0]["content"].startswith("Compare ONE"):
            result = json.dumps({"evidence": "visible", "verdict": "met"})
        elif body["messages"][0]["content"].startswith("Select at most"):
            result = json.dumps({"tags": ["soft shading"]})
        elif body["messages"][0]["content"].startswith("Translate"):
            result = json.dumps({"text": "mountain"})
        else:
            result = plan.model_dump_json()
        return httpx.Response(200, json={"choices": [{"message": {"content": result}}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        providers = {
            r: CompatibleProvider(ProviderConfig.model_validate(v), client)
            for r, v in preset.items()
        }
        assert providers["planner"].plan("山", painter).prompt.startswith("mountain")
        assert providers["generator"].generate(plan) == raw
        assert providers["critic"].evaluate(path, "山", painter, plan).scores == expected.scores
    assert len(calls) == 8


def test_context_uses_rules_and_bounded_output_schema(monkeypatch, painter, plan):
    def handler(request):
        body = json.loads(request.content)
        user = json.loads(body["messages"][1]["content"][0]["text"])
        assert user["prior_experience"] == ["Use a clear focal point."]
        assert "old-private-id" not in body["messages"][1]["content"][0]["text"]
        schema = body["response_format"]["json_schema"]["schema"]
        assert schema["properties"]["prompt"]["maxLength"] == 450
        assert schema["$defs"]["CompactPerception"]["properties"]["constraints"]["maxItems"] == 3
        return httpx.Response(
            200, json={"choices": [{"message": {"content": plan.model_dump_json()}}]}
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = CompatibleProvider(config(monkeypatch), client)
        assert (
            provider.plan(
                "山",
                painter,
                context=[{"entry_id": "old-private-id", "rules": ["Use a clear focal point."]}],
            )
            == plan
        )


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
