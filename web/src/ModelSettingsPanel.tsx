import { useEffect, useState } from "react";
import { post, request } from "./api";
import "./model-settings.css";

type Role = "planner" | "generator" | "critic";
type Config = {
  provider: string;
  api_flavor: string;
  base_url: string;
  model: string;
  auth: string;
  api_key_env: string;
  api_key?: string;
  timeout_seconds: number;
  prompt_language: string;
  structured_output: string;
  parameters: Record<string, unknown>;
  key_configured?: boolean;
  stored_key?: boolean;
};
type Settings = {
  revision: number;
  key_storage: boolean;
  local_generator_available: boolean;
  planner: Config;
  generator: Config;
  critic: Config;
};
const roles: Role[] = ["planner", "generator", "critic"];
const titles = {
  planner: "构思模型",
  generator: "生图模型",
  critic: "视觉评价模型",
};
const hints = {
  planner: "理解题目、经验与目标，生成绘画构思。",
  generator: "根据构思生成真实图片。",
  critic: "必须支持图片输入，才能看图评价。",
};

export default function ModelSettingsPanel({
  onSaved,
}: {
  onSaved: () => void;
}) {
  const [config, setConfig] = useState<Settings | null>(null);
  const [params, setParams] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [checks, setChecks] = useState<
    Record<string, { ok: boolean; message: string; models: string[] }>
  >({});
  function adopt(value: Settings) {
    setConfig(value);
    setParams(
      Object.fromEntries(
        roles.map((r) => [r, JSON.stringify(value[r].parameters, null, 2)]),
      ),
    );
    setChecks({});
  }
  useEffect(() => {
    let disposed = false;
    request<Settings>("/model-settings")
      .then((value) => {
        if (!disposed) adopt(value);
      })
      .catch((e) => {
        if (!disposed) setError(e.message);
      });
    return () => {
      disposed = true;
    };
  }, []);
  function change(role: Role, patch: Partial<Config>) {
    setConfig((old) =>
      old ? { ...old, [role]: { ...old[role], ...patch } } : old,
    );
    setChecks((old) => {
      const next = { ...old };
      delete next[role];
      return next;
    });
    setNotice("");
  }
  function body(role: Role) {
    const { key_configured, stored_key, ...value } = config![role];
    void key_configured;
    void stored_key;
    return {
      ...value,
      parameters:
        value.provider === "sd_cpp" ? {} : JSON.parse(params[role] || "{}"),
    };
  }
  async function run(fn: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(
        e instanceof SyntaxError
          ? "高级参数需要有效的 JSON 对象。"
          : (e as Error).message,
      );
    } finally {
      setBusy(false);
    }
  }
  if (!config)
    return (
      <section className="model-settings">
        <h2>模型设置</h2>
        <p role="status">{error || "正在加载模型配置…"}</p>
        <button
          onClick={() =>
            void run(async () =>
              adopt(await request<Settings>("/model-settings")),
            )
          }
        >
          重新加载
        </button>
      </section>
    );
  return (
    <section className="model-settings">
      <div className="model-intro">
        <h2>为每一步，选择合适的模型。</h2>
        <p>
          可以混合使用本地模型和远程服务。配置对所有画师生效，新任务使用保存后的配置；已排队和运行中的任务保留原设置。
        </p>
      </div>
      <div className="learning-help">
        <button
          className="secondary"
          disabled={busy}
          onClick={() =>
            void run(async () => {
              const preset = await request<Record<Role, Config>>(
                "/model-settings/presets/ark-all",
              );
              adopt({ ...config, ...preset });
              setNotice(
                "已载入全方舟预设，尚未保存。在下方填写一把方舟密钥，即可同时用于三个模型，然后保存。",
              );
            })
          }
        >
          载入全方舟预设
        </button>
        <p>
          构思与看图评价：Doubao Seed 2.0 Lite · 生图：Seedream
          4.5（2K）。预设使用北京地域，密钥需匹配地域；地址和模型名称均可修改。
        </p>
        {roles.every(
          (role) =>
            config[role].provider === "openai_compatible" &&
            config[role].base_url.replace(/\/$/, "") ===
              "https://ark.cn-beijing.volces.com/api/v3",
        ) && (
          <>
            <label htmlFor="ark-shared-key">方舟统一密钥（三个模型共用）</label>
            <input
              id="ark-shared-key"
              type="password"
              autoComplete="new-password"
              placeholder="在这里填一次，再点击保存模型配置；留空保留已有密钥"
              disabled={busy || !config.key_storage}
              value={
                roles.every((r) => config[r].api_key === config.planner.api_key)
                  ? config.planner.api_key || ""
                  : ""
              }
              onChange={(e) => {
                const key = e.target.value;
                setConfig((old) =>
                  old
                    ? {
                        ...old,
                        ...Object.fromEntries(
                          roles.map((r) => [r, { ...old[r], api_key: key }]),
                        ),
                      }
                    : old,
                );
                setChecks({});
                setNotice("");
              }}
            />
            <p>
              同一把方舟 API Key
              需有上述模型的调用权限。保存后在本机加密存储，不回显。
            </p>
          </>
        )}
      </div>
      {error && (
        <div className="alert" role="alert">
          {error}
        </div>
      )}
      {notice && (
        <div className="learning-help" role="status">
          {notice}
        </div>
      )}
      {roles.map((role, index) => {
        const value = config[role];
        return (
          <section className="model-card" key={role}>
            <header>
              <span>0{index + 1}</span>
              <div>
                <h3>{titles[role]}</h3>
                <p>{hints[role]}</p>
              </div>
            </header>
            <label htmlFor={`${role}-provider`}>接入方式</label>
            <select
              id={`${role}-provider`}
              value={value.provider}
              disabled={busy}
              onChange={(e) => {
                change(role, {
                  provider: e.target.value,
                  api_flavor: "generic",
                  model: e.target.value === "mock" ? "mock-v1" : "",
                  auth: e.target.value === "openai_compatible" ? "env" : "none",
                  api_key_env: `PAINTAI_${role.toUpperCase()}_API_KEY`,
                  api_key: "",
                });
                setParams((old) => ({ ...old, [role]: "{}" }));
              }}
            >
              <option value="openai_compatible">兼容 API（本地或远程）</option>
              {role === "generator" && config.local_generator_available && (
                <option value="sd_cpp">本机已安装的 SD 模型</option>
              )}
              <option value="mock">离线模拟（不产生真实模型结果）</option>
            </select>
            {value.provider === "openai_compatible" && (
              <>
                <label htmlFor={`${role}-flavor`}>服务协议</label>
                <select
                  id={`${role}-flavor`}
                  value={value.api_flavor}
                  disabled={busy}
                  onChange={(e) => change(role, { api_flavor: e.target.value })}
                >
                  <option value="generic">通用兼容 API</option>
                  {role === "generator" ? (
                    <option value="seedream">火山方舟 Seedream</option>
                  ) : (
                    <option value="bailian">阿里云百炼</option>
                  )}
                </select>
                <label htmlFor={`${role}-url`}>
                  API 地址（包含服务的 /v1 等前缀）
                </label>
                <input
                  id={`${role}-url`}
                  value={value.base_url}
                  maxLength={500}
                  disabled={busy}
                  onChange={(e) => change(role, { base_url: e.target.value })}
                  placeholder="https://your-service.example/v1"
                />
                <label htmlFor={`${role}-model`}>模型名称</label>
                <input
                  id={`${role}-model`}
                  value={value.model}
                  maxLength={200}
                  list={`${role}-models`}
                  disabled={busy}
                  onChange={(e) => change(role, { model: e.target.value })}
                  placeholder="填写服务商或本地服务提供的模型 ID"
                />
                <datalist id={`${role}-models`}>
                  {(checks[role]?.models || []).map((id) => (
                    <option key={id} value={id} />
                  ))}
                </datalist>
                <label htmlFor={`${role}-auth`}>认证方式</label>
                <select
                  id={`${role}-auth`}
                  value={value.auth}
                  disabled={busy}
                  onChange={(e) =>
                    change(role, { auth: e.target.value, api_key: "" })
                  }
                >
                  <option value="env">API 密钥 / 环境变量</option>
                  <option value="none">无需认证（仅限本机地址）</option>
                </select>
                {value.auth === "env" && (
                  <>
                    <label htmlFor={`${role}-key`}>
                      API 密钥
                      {value.key_configured ? "（已配置，留空保留）" : ""}
                    </label>
                    <input
                      id={`${role}-key`}
                      type="password"
                      autoComplete="new-password"
                      value={value.api_key || ""}
                      disabled={busy || !config.key_storage}
                      onChange={(e) =>
                        change(role, { api_key: e.target.value })
                      }
                      placeholder={
                        config.key_storage
                          ? "填写后在本机加密保存，不会回显"
                          : "请通过下方环境变量提供密钥"
                      }
                    />
                    <p className="model-help">
                      {value.stored_key
                        ? "当前使用本机加密密钥。"
                        : "可直接填写密钥，也可使用后端进程的环境变量。"}
                      密钥不会写入任务、作品或
                      Git；连接检查会将认证信息发送至上方服务地址。
                    </p>
                    <details>
                      <summary>使用环境变量</summary>
                      <label htmlFor={`${role}-env`}>环境变量名</label>
                      <input
                        id={`${role}-env`}
                        value={value.api_key_env}
                        disabled={busy}
                        onChange={(e) =>
                          change(role, { api_key_env: e.target.value })
                        }
                      />
                      <p className="model-help">
                        环境变量需要在启动后端前设置。改用环境变量时清空上方密钥。
                      </p>
                    </details>
                  </>
                )}
                {role !== "generator" && (
                  <>
                    <label htmlFor={`${role}-structured`}>结构化输出模式</label>
                    <select
                      id={`${role}-structured`}
                      disabled={busy}
                      value={value.structured_output}
                      onChange={(e) =>
                        change(role, { structured_output: e.target.value })
                      }
                    >
                      <option value="json_schema">
                        JSON Schema（严格结构）
                      </option>
                      <option value="json_object">JSON Object</option>
                      <option value="prompt">仅提示词约束</option>
                    </select>
                  </>
                )}
                {role === "planner" && (
                  <>
                    <label htmlFor="planner-language">生图提示词语言</label>
                    <select
                      id="planner-language"
                      disabled={busy}
                      value={value.prompt_language}
                      onChange={(e) =>
                        change(role, { prompt_language: e.target.value })
                      }
                    >
                      <option value="en">英文</option>
                      <option value="auto">自动</option>
                    </select>
                  </>
                )}
                <label htmlFor={`${role}-timeout`}>请求超时（秒）</label>
                <input
                  id={`${role}-timeout`}
                  type="number"
                  min={1}
                  max={3600}
                  value={value.timeout_seconds}
                  disabled={busy}
                  onChange={(e) =>
                    change(role, { timeout_seconds: Number(e.target.value) })
                  }
                />
                <details>
                  <summary>高级模型参数</summary>
                  <p className="model-help">
                    {role === "generator"
                      ? value.api_flavor === "seedream"
                        ? "Seedream 4.5 使用 2K 或 4K；单张生成请保持 sequential_image_generation 为 disabled。"
                        : '例如 {"size":"1024x1024"}。具体参数取决于所选生图服务。'
                      : value.api_flavor === "bailian"
                        ? "百炼预设使用 max_tokens 和 enable_thinking: false，以 JSON Object 返回结果。"
                        : '例如 {"temperature":0.2,"max_completion_tokens":2048}。'}
                    不要在这里填写密钥。
                  </p>
                  <textarea
                    aria-label={`${titles[role]}高级参数`}
                    value={params[role]}
                    disabled={busy}
                    onChange={(e) => {
                      setParams((old) => ({ ...old, [role]: e.target.value }));
                      setChecks({});
                    }}
                    spellCheck={false}
                  />
                </details>
                <p className="model-help">
                  {role === "generator"
                    ? "需要兼容 /images/generations 接口。"
                    : "需要兼容 /chat/completions 接口。"}
                  {role === "critic" ? "模型必须接受 image_url 图片输入。" : ""}
                  保存不会下载模型，也不会发起收费推理。
                </p>
              </>
            )}
            {value.provider === "sd_cpp" && (
              <p className="model-help">
                当前本地模型：<strong>{value.model}</strong>。
                使用服务器启动配置中的本机程序和模型文件。若要换更大的本地模型，可先用本地服务启动它，再选择“兼容
                API”填写地址和模型名。
              </p>
            )}
            <button
              className="secondary"
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  const result = await post<{
                    ok: boolean;
                    message: string;
                    models: string[];
                  }>(`/model-settings/${role}/check`, body(role));
                  setChecks((old) => ({ ...old, [role]: result }));
                })
              }
            >
              检查{titles[role]}连接
            </button>
            {checks[role] && (
              <p
                role="status"
                className={
                  checks[role].ok ? "model-check-ok" : "model-check-error"
                }
              >
                {checks[role].message}
              </p>
            )}
          </section>
        );
      })}
      <div className="model-actions">
        <button
          className="primary"
          disabled={busy}
          onClick={() =>
            void run(async () => {
              const result = await post<Settings>("/model-settings", {
                revision: config.revision,
                ...Object.fromEntries(roles.map((r) => [r, body(r)])),
              });
              adopt(result);
              setNotice("模型配置已保存。下一次创作、课程或实验将使用新配置。");
              onSaved();
            })
          }
        >
          {busy ? "正在处理…" : "保存模型配置"}
        </button>
        <button
          className="secondary"
          disabled={busy}
          onClick={() =>
            void run(async () => {
              adopt(await request<Settings>("/model-settings"));
              setNotice("已重新加载保存的配置。");
            })
          }
        >
          放弃修改并重新加载
        </button>
        <button
          className="secondary"
          disabled={busy}
          onClick={() =>
            void run(async () => {
              adopt(
                await post<Settings>("/model-settings/reset", {
                  revision: config.revision,
                }),
              );
              setNotice("已恢复服务器启动时的配置。");
              onSaved();
            })
          }
        >
          恢复启动配置
        </button>
      </div>
    </section>
  );
}
