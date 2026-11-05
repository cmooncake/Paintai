"""Local model preferences. Windows DPAPI protects stored keys for the current OS user."""

import ctypes
import json
import math
import os
from typing import Literal
from uuid import uuid4

import httpx
from pydantic import Field, SecretStr, ValidationError

from .config import ProviderConfig, Settings
from .learning import LearningError
from .models import Model

ROLES = ("planner", "generator", "critic")


def protect(raw: bytes, *, decrypt=False):
    if os.name != "nt":
        raise LearningError("本系统暂不支持加密保存密钥，请使用环境变量认证。")
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_byte))]

    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    output = Blob()
    function = (
        ctypes.windll.crypt32.CryptUnprotectData
        if decrypt
        else ctypes.windll.crypt32.CryptProtectData
    )
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise LearningError("无法访问本机加密密钥，请在当前 Windows 用户下重新填写。")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(output.data)


class RoleEdit(Model):
    api_flavor: Literal["generic", "bailian", "seedream"] = "generic"
    provider: Literal["mock", "openai_compatible", "sd_cpp"]
    base_url: str = Field(default="http://127.0.0.1:18080/v1", max_length=500)
    model: str = Field(default="mock-v1", max_length=200)
    auth: Literal["env", "none"] = "env"
    api_key_env: str = Field(default="OPENAI_API_KEY", pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,119}$")
    api_key: SecretStr | None = None
    timeout_seconds: float = Field(default=120, gt=0, le=3600)
    prompt_language: Literal["auto", "en"] = "auto"
    structured_output: Literal["json_schema", "json_object", "prompt"] = "json_schema"
    parameters: dict = Field(default_factory=dict)


class ModelEdit(Model):
    revision: int = Field(ge=0)
    planner: RoleEdit
    generator: RoleEdit
    critic: RoleEdit


class ModelRevision(Model):
    revision: int = Field(ge=0)


class ModelSettings:
    def __init__(self, store, defaults):
        self.store, self.defaults = store, defaults
        with store.connect() as db:
            db.executescript("""CREATE TABLE IF NOT EXISTS model_preferences (
                id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS model_credentials (
                id TEXT PRIMARY KEY, ciphertext BLOB NOT NULL);""")

    def current(self, db=None):
        if db is None:
            with self.store.connect() as connection:
                return self.current(connection)
        row = db.execute("SELECT * FROM model_preferences WHERE id=1").fetchone()
        settings = self.defaults.model_copy(deep=True)
        if row:
            values = settings.model_dump(mode="json")
            values.update(json.loads(row["payload"]))
            settings = Settings.model_validate(values)
        # New local capabilities may be installed after preferences were saved.
        if settings.generator.provider == "sd_cpp" and self.defaults.generator.local_image:
            for field in ("ip_adapter_path", "clip_vision_path"):
                if getattr(settings.generator.local_image, field) is None:
                    setattr(
                        settings.generator.local_image,
                        field,
                        getattr(self.defaults.generator.local_image, field),
                    )
        for row in db.execute("SELECT id,ciphertext FROM model_credentials"):
            if row["id"] not in os.environ:
                try:
                    os.environ[row["id"]] = protect(row["ciphertext"], decrypt=True).decode()
                except LearningError:
                    pass  # Settings remain inspectable so the user can replace an inaccessible key.
        return settings

    def public(self):
        with self.store.connect() as db:
            settings = self.current(db)
            row = db.execute("SELECT revision FROM model_preferences WHERE id=1").fetchone()
        result = {"revision": row["revision"] if row else 0, "key_storage": os.name == "nt"}
        for role in ROLES:
            config = getattr(settings, role)
            value = config.model_dump(mode="json", exclude={"local_image"})
            value["key_configured"] = bool(os.environ.get(config.api_key_env))
            value["stored_key"] = config.api_key_env.startswith("PAINTAI_STORED_")
            result[role] = value
        result["local_generator_available"] = self.defaults.generator.local_image is not None
        return result

    def prepare(self, role, edit, current):
        data = edit.model_dump(exclude={"api_key"})
        if edit.api_flavor != "generic" and edit.provider != "openai_compatible":
            raise LearningError("服务适配仅用于兼容 API。")
        if (edit.api_flavor == "seedream" and role != "generator") or (
            edit.api_flavor == "bailian" and role == "generator"
        ):
            raise LearningError("服务适配与模型角色不匹配。")
        # Executables and model files are configured by the server, never arbitrary browser commands.
        if edit.provider == "sd_cpp":
            if role != "generator" or not self.defaults.generator.local_image:
                raise LearningError("没有已安装的本地生图配置；请使用兼容生图 API。")
            data["local_image"] = self.defaults.generator.local_image.model_dump(mode="json")
            data["model"] = self.defaults.generator.model
            data["parameters"] = {}
        old = getattr(current, role)
        supplied = edit.api_key.get_secret_value().strip() if edit.api_key else ""
        if supplied and len(supplied) > 4096:
            raise LearningError("密钥长度超出限制。")
        if supplied and (edit.provider != "openai_compatible" or edit.auth != "env"):
            raise LearningError("只有需要认证的兼容 API 才能保存密钥。")
        if (
            not supplied
            and old.provider == "openai_compatible"
            and edit.provider == "openai_compatible"
            and edit.base_url.rstrip("/") != old.base_url.rstrip("/")
            and edit.api_key_env == old.api_key_env
            and os.environ.get(old.api_key_env)
        ):
            raise LearningError("服务地址改变时，请重新填写密钥或选择新的环境变量。")
        allowed = (
            {
                "size",
                "quality",
                "output_format",
                "response_format",
                "style",
                "watermark",
                "sequential_image_generation",
            }
            if role == "generator"
            else {
                "temperature",
                "max_completion_tokens",
                "max_tokens",
                "enable_thinking",
                "thinking",
                "reasoning_effort",
            }
        )
        if {"max_tokens", "max_completion_tokens"} <= data["parameters"].keys():
            raise LearningError("输出长度只能设置 max_tokens 或 max_completion_tokens 中的一个。")
        for name, value in data["parameters"].items():
            if name not in allowed:
                raise LearningError("此角色不支持该高级参数，请检查参数名称。")
            if name == "temperature":
                valid = type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 2
            elif name in ("max_completion_tokens", "max_tokens"):
                valid = type(value) is int and 1 <= value <= 131072
            elif name in ("enable_thinking", "watermark"):
                valid = type(value) is bool
            elif name == "thinking":
                valid = (
                    isinstance(value, dict)
                    and set(value) == {"type"}
                    and value["type"] in ("enabled", "disabled", "auto")
                )
            elif name == "sequential_image_generation":
                valid = value == "disabled"
            else:
                valid = isinstance(value, str) and 0 < len(value) <= 80
            if not valid:
                raise LearningError("高级参数类型或范围不合法。")
        if edit.api_key_env.startswith("PAINTAI_STORED_"):
            if edit.api_key_env != old.api_key_env:
                raise LearningError("已保存密钥引用无效，请重新填写密钥。")
            if not supplied and (
                edit.base_url.rstrip("/") != old.base_url.rstrip("/")
                or edit.provider != old.provider
            ):
                raise LearningError(
                    "服务地址改变时，请重新填写密钥或改用环境变量，避免把原密钥发往新服务。"
                )
        try:
            return ProviderConfig.model_validate(data), supplied
        except ValidationError:
            raise LearningError(
                "模型配置不合法：请检查地址、模型名、认证方式和参数名称。远程地址必须使用 HTTPS。"
            ) from None

    def save(self, body):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT revision FROM model_preferences WHERE id=1").fetchone()
            revision = row["revision"] if row else 0
            if revision != body.revision:
                raise LearningError("配置已被另一页面更新，请重新加载后再保存。")
            current = self.current(db)
            roles, keys = {}, {}
            for role in ROLES:
                config, key = self.prepare(role, getattr(body, role), current)
                if key:
                    key_id = "PAINTAI_STORED_" + uuid4().hex
                    ciphertext = protect(key.encode())
                    db.execute("INSERT INTO model_credentials VALUES (?,?)", (key_id, ciphertext))
                    config.api_key_env, config.auth = key_id, "env"
                    keys[key_id] = key
                roles[role] = config.model_dump(mode="json")
            db.execute(
                "INSERT INTO model_preferences VALUES (1,?,?) ON CONFLICT(id) "
                "DO UPDATE SET revision=excluded.revision,payload=excluded.payload",
                (revision + 1, json.dumps(roles)),
            )
        os.environ.update(keys)
        return self.public()

    def reset(self, revision):
        return self.save(
            ModelEdit(
                revision=revision,
                **{
                    role: RoleEdit.model_validate(
                        getattr(self.defaults, role).model_dump(exclude={"local_image"})
                    )
                    for role in ROLES
                },
            )
        )

    def check(self, role, edit):
        config, key = self.prepare(role, edit, self.current())
        if config.provider == "mock":
            return {
                "ok": True,
                "message": "模拟模式，仅用于流程验证，不代表模型质量。",
                "models": [],
            }
        if config.provider == "sd_cpp":
            ok = config.local_image.executable.is_file() and config.local_image.model_path.is_file()
            return {
                "ok": ok,
                "message": "本地程序与模型文件存在；未执行生图。"
                if ok
                else "本地程序或模型文件不存在。",
                "models": [],
            }
        try:
            key = key or config.credential()
            with httpx.Client(timeout=8, follow_redirects=False) as client:
                response = client.get(
                    config.base_url.rstrip("/") + "/models",
                    headers={"Authorization": f"Bearer {key}"} if key else {},
                )
            if not response.is_success:
                return {
                    "ok": False,
                    "message": f"模型列表接口返回 HTTP {response.status_code}。部分服务不提供此接口，可核对文档后保存配置。",
                    "models": [],
                }
            models = [
                x["id"]
                for x in response.json().get("data", [])
                if isinstance(x, dict) and isinstance(x.get("id"), str)
            ][:200]
            return {
                "ok": True,
                "message": "已连接模型列表接口；尚未验证构思、生图或看图能力。"
                + (" 当前模型未列出，请核对名称。" if config.model not in models else ""),
                "models": models,
            }
        except (ValueError, TypeError, AttributeError, httpx.HTTPError):
            return {
                "ok": False,
                "message": "连接失败或密钥未配置，请检查地址、服务状态和认证。",
                "models": [],
            }
