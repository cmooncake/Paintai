import json

import httpx
import pytest

from paintai.config import ProviderConfig
from paintai.models import Painter
from paintai.providers import CompatibleProvider, MockProvider, ProviderError


def test_locked_requirements_preserve_hyphenated_subject_and_eye_color():
    from paintai.providers import locked_requirements

    requirements = locked_requirements(
        "A head-and-shoulders portrait with brown hair and amber eyes. No text.", {}
    )
    assert list(requirements.values()) == [
        "A head-and-shoulders portrait with brown hair",
        "amber eyes",
        "No text",
    ]


def test_original_subject_survives_meta_instruction_and_style_translation():
    painter = Painter(painter_id="test", name="Test")
    bad = MockProvider().plan("portrait", painter)
    bad.prompt = "保持角色要求，不复制参考角色"
    replies = [bad.model_dump(), {"tags": ["delicate linework", "soft facial shadows"]}]

    def handler(request):
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(replies.pop(0))}}]}
        )

    config = ProviderConfig(
        provider="openai_compatible",
        model="test",
        auth="none",
        base_url="http://localhost:18080/v1",
        prompt_language="en",
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        plan = CompatibleProvider(config, client).plan(
            "Brown bob, ivory blouse, teal background. No text.",
            painter,
            requirements={"style_profile": {"traits": {"lines": "细线条", "palette": "暖肤色"}}},
        )
    assert (
        plan.prompt
        == "Brown bob, ivory blouse, teal background. No text.\ndelicate linework\nsoft facial shadows"
    )


@pytest.mark.parametrize("verdict,cap", [("missed", 4), ("uncertain", 6)])
def test_blind_observation_and_fidelity_gate(tmp_path, verdict, cap):
    painter = Painter(painter_id="test", name="Test")
    plan = MockProvider().plan("brown hair", painter)
    path = tmp_path / "image.png"
    path.write_bytes(MockProvider().generate(plan))
    count = 0

    def handler(request):
        nonlocal count
        body = json.loads(request.content)
        count += 1
        if count == 1:
            assert "brown hair" not in json.dumps(body)
            result = {"description": "Blue hair, yellow background, text-like marks."}
        elif count == 2:
            assert "Blue hair" in json.dumps(body)
            assert json.loads(body["messages"][1]["content"][0]["text"])["locked_requirements"] == {
                "req_0": "brown hair"
            }
            result = {
                "scores": dict.fromkeys(
                    ["topic", "technique", "composition", "color", "style", "creativity"], 9
                ),
                "feedback": "Pretty.",
                "improvements": ["Fix hair color"],
            }
        else:
            assert json.loads(body["messages"][1]["content"][0]["text"])["required"] == "brown hair"
            result = {"evidence": "blue hair", "verdict": verdict}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}]})

    config = ProviderConfig(
        provider="openai_compatible",
        model="test",
        auth="none",
        base_url="http://localhost:18081/v1",
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = CompatibleProvider(config, client).evaluate(path, "brown hair", painter, plan)
    assert count == 3 and result.scores.topic == cap and result.overall <= 6
    assert "Blue hair" in result.feedback


def test_translation_failure_stops_generation():
    painter = Painter(painter_id="test", name="Test")
    replies = [MockProvider().plan("头像", painter).model_dump(), {"text": "仍是中文"}]
    config = ProviderConfig(
        provider="openai_compatible",
        model="test",
        auth="none",
        base_url="http://localhost:18080/v1",
        prompt_language="en",
    )
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, json={"choices": [{"message": {"content": json.dumps(replies.pop(0))}}]}
            )
        )
    ) as client:
        with pytest.raises(ProviderError, match="translation failed|invalid structured output"):
            CompatibleProvider(config, client).plan("头像", painter)
