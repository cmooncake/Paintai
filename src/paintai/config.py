import os
import tomllib
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from .models import Model


class LocalImageConfig(Model):
    executable: Path
    model_path: Path
    work_dir: Path = Path("data/local-image/runs")
    width: int = Field(default=512, ge=256, le=1024, multiple_of=64)
    height: int = Field(default=512, ge=256, le=1024, multiple_of=64)
    steps: int = Field(default=4, ge=1, le=50)
    cfg_scale: float = Field(default=1.0, ge=0, le=20, allow_inf_nan=False)
    threads: int = Field(default=8, ge=1, le=64)
    seed: int = Field(default=42, ge=0, le=2147483647)
    sampling_method: Literal["euler", "euler_a", "lcm"] = "euler"
    scheduler: Literal["discrete", "karras", "sgm_uniform"] = "discrete"


class ProviderConfig(Model):
    provider: Literal["mock", "openai_compatible", "sd_cpp"] = "mock"
    base_url: str = "https://api.openai.com/v1"
    model: str = "mock-v1"
    api_key_env: str = "OPENAI_API_KEY"
    auth: Literal["env", "none"] = "env"
    timeout_seconds: float = Field(default=120, gt=0, le=3600)
    prompt_language: Literal["auto", "en"] = "auto"
    local_image: LocalImageConfig | None = None
    structured_output: Literal["json_schema", "json_object", "prompt"] = "json_schema"
    # Only model parameters, never credentials or headers. Deliberately allowlisted.
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_provider(self):
        allowed = {
            "temperature",
            "max_completion_tokens",
            "reasoning_effort",
            "size",
            "quality",
            "output_format",
            "response_format",
            "style",
        }
        if set(self.parameters) - allowed:
            raise ValueError("Unsupported parameters; credentials belong in environment variables")
        if self.provider == "sd_cpp":
            if self.local_image is None:
                raise ValueError("sd_cpp requires local_image configuration")
            if self.parameters:
                raise ValueError("sd_cpp options belong in local_image, not API parameters")
        if self.provider == "openai_compatible":
            url = urlsplit(self.base_url)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
            ):
                raise ValueError(
                    "base_url must be an HTTP(S) URL without credentials/query/fragment"
                )
            if url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("Remote API endpoints must use HTTPS")
            if self.auth == "none" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("Unauthenticated API mode is limited to loopback endpoints")
            if not self.model.strip() or self.model == "mock-v1":
                raise ValueError("Set an explicit model for the API provider")
        return self

    def credential(self) -> str:
        if self.auth == "none" or self.provider in {"mock", "sd_cpp"}:
            return ""
        key = os.environ.get(self.api_key_env, "")
        if self.provider != "mock" and not key:
            raise ValueError(f"Missing API key environment variable: {self.api_key_env}")
        return key


class Settings(Model):
    data_dir: Path = Path("data")
    planner: ProviderConfig = Field(default_factory=ProviderConfig)
    generator: ProviderConfig = Field(default_factory=ProviderConfig)
    critic: ProviderConfig = Field(default_factory=ProviderConfig)

    @model_validator(mode="after")
    def validate_roles(self):
        if self.planner.provider == "sd_cpp" or self.critic.provider == "sd_cpp":
            raise ValueError("sd_cpp is an image generator, not a planner or critic")
        return self


def load_settings(
    path: Path | None = None, *, offline: bool = False, data_dir: Path | None = None
) -> Settings:
    path = path or Path(os.environ.get("PAINTAI_CONFIG", "paintai.toml"))
    if offline:
        settings = Settings()
    else:
        if not path.is_file():
            raise ValueError(
                "Config not found. Pass --offline or --config configs/api.example.toml"
            )
        with path.open("rb") as handle:
            settings = Settings.model_validate(tomllib.load(handle))
        if not settings.data_dir.is_absolute():
            settings.data_dir = path.resolve().parent / settings.data_dir
        local = settings.generator.local_image
        if local:
            for field in ("executable", "model_path", "work_dir"):
                value = getattr(local, field)
                if not value.is_absolute():
                    setattr(local, field, (path.resolve().parent / value).resolve())
    override = data_dir or os.environ.get("PAINTAI_DATA_DIR")
    if override:
        settings.data_dir = Path(override)
    settings.data_dir = settings.data_dir.resolve()
    return settings
