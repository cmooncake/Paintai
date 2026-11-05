"""Credential-free provider presets, based on official API docs (2026-11-03)."""

from .model_settings import RoleEdit


def ark_all():
    preset = bailian_seedream()
    for role in ("planner", "critic"):
        preset[role].update(
            api_flavor="generic",
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            model="doubao-seed-2-0-lite-260428",
            api_key_env="ARK_API_KEY",
            parameters={
                "temperature": 0.2 if role == "planner" else 0.1,
                "max_tokens": 2048 if role == "planner" else 4096,
                "thinking": {"type": "disabled"},
            },
        )
    return preset


def bailian_seedream():
    planner = RoleEdit(
        provider="openai_compatible",
        api_flavor="bailian",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen-plus",
        api_key_env="DASHSCOPE_API_KEY",
        structured_output="json_object",
        prompt_language="en",
        timeout_seconds=180,
        parameters={"temperature": 0.2, "max_tokens": 2048, "enable_thinking": False},
    )
    critic = planner.model_copy(deep=True)
    critic.model = "qwen3-vl-plus"
    critic.prompt_language = "auto"
    critic.parameters = {"temperature": 0.1, "max_tokens": 4096, "enable_thinking": False}
    generator = RoleEdit(
        provider="openai_compatible",
        api_flavor="seedream",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        model="doubao-seedream-4-5-251128",
        api_key_env="ARK_API_KEY",
        timeout_seconds=300,
        parameters={
            "size": "2K",
            "response_format": "url",
            "sequential_image_generation": "disabled",
            "watermark": True,
        },
    )
    return {
        role: config.model_dump(exclude={"api_key"})
        for role, config in [("planner", planner), ("generator", generator), ("critic", critic)]
    }
