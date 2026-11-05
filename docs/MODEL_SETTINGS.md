# 网页模型配置

左侧「模型设置」独立配置三个角色，对所有画师生效：

- **构思**：兼容 `/chat/completions` 的文本模型。
- **生图**：兼容 `/images/generations` 的模型，或继续使用启动配置中已安装的 SD 本地模型。
- **评价**：兼容 `/chat/completions` 且接受 `image_url` 图片输入的视觉模型。纯文本模型不能代替视觉评价。

不绑定具体服务商或模型版本。地址填写服务的 API 根路径（包含 `/v1` 等前缀），模型名填写服务商或本机服务实际支持的 ID。网页不会下载、安装或启动新模型；更大的本地模型先由本地推理服务运行，再在网页填写地址与模型 ID。

## 操作

### 全方舟预设（当前网页推荐）

点击「载入全方舟预设」，构思和视觉评价均使用 `doubao-seed-2-0-lite-260428`，生图使用 `doubao-seedream-4-5-251128`（2K）。三个角色地址统一为 `https://ark.cn-beijing.volces.com/api/v3`，环境变量统一为 `ARK_API_KEY`。构思和评价使用 JSON Object、`max_tokens` 和 `thinking: {"type": "disabled"}`，视觉评价传入真实图片。

在「方舟统一密钥」中填写一次，点击「保存模型配置」即可为三个角色保存同一把密钥。留空保留各角色现有认证；只有三个角色都使用上述方舟地址时才显示统一输入框，避免把密钥传给其他服务。仍可分别编辑各角色的模型、参数和密钥。账号须有这些模型的调用权限，模型 ID 可按账号实际可用列表调整。

参考：[方舟深度思考参数](https://docs.volcengine.com/docs/ark/deep-thinking?lang=zh)、[结构化输出](https://docs.volcengine.com/docs/ark/structured-output-beta?lang=zh)。未提供密钥时只验证本地配置与模拟 HTTP 请求，不代表实际云端调用成功。

### 百炼 + Seedream 预设

备用预设仍通过 `/api/v1/model-settings/presets/bailian-seedream` 提供，也可按下表手动配置：

| 角色 | 模型 | API 根地址 |
| --- | --- | --- |
| 构思 | `qwen-plus` | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| 视觉评价 | `qwen3-vl-plus` | 同上 |
| 生图 | `doubao-seedream-4-5-251128` | `https://ark.cn-beijing.volces.com/api/v3` |

在构思和评价的密码框分别填同一个百炼密钥，生图填方舟密钥，然后保存。预设对应北京地域；账号需要开通相应模型。可自行修改地域地址、工作空间地址或模型 ID。环境变量分别为 `DASHSCOPE_API_KEY` 和 `ARK_API_KEY`。允许先不填密钥保存，但真实创作须先完成认证配置。

百炼使用 `json_object`、`max_tokens` 和 `enable_thinking: false`；评价上传真实图片。Seedream 4.5 使用 `2K`、URL 图片返回、单图生成和默认水印，非流式请求不携带通用接口的 `n`。图片下载不携带 API 密钥。切换到其他版本时需按其支持范围修改参数。

预设加载会替换三个角色的草稿及认证引用，不会沿用其他服务密钥。保存配置和连接检查均不会执行收费推理。自动化测试验证请求格式与图片传递，不代表真实云端验收；填密钥后请先创建单幅作品验证账号权限与实际效果。

官方依据：[百炼兼容接口](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)、[视觉理解](https://help.aliyun.com/zh/model-studio/vision)、[结构化输出](https://help.aliyun.com/zh/model-studio/qwen-structured-output)、[方舟生图接口](https://console.volcengine.com/ark/region:cn-beijing/docs/image-generation-api)。

### 手动配置

1. 选择角色接入方式，填写地址和模型名。
2. 远程服务使用 HTTPS 与密钥认证；本机地址可选择无认证。
3. 点击连接检查：兼容 API 仅请求 `/models`，可返回模型 ID 供输入框选择；本地 SD 仅检查文件存在。不会发起生图、构思或评价推理，也不能证明模型质量或视觉能力。
4. 必要时调整结构化输出模式、构思提示词语言、超时与高级参数。较弱兼容服务可从 JSON Schema 改为 JSON Object 或提示词约束。参数必须与该角色相符，值还需符合供应商要求。
5. 保存后下一个网页创作、训练或实验使用新配置，无需重启。保存不会触发网络推理。正在运行、已排队的任务保留提交时的模型设置。
6. 「放弃修改并重新加载」恢复最近保存值；「恢复启动配置」恢复本次后端启动所用 TOML 配置。两者不会修改历史作品或实验。

不提供 `/models` 的服务可能返回 404/405：检查会注明无法验证列表，仍可保存，实际能力需要通过用户发起的创作验证。连接错误、认证错误不会被标记为成功。

## 密钥与持久化

Windows 可直接在密码框填入 API 密钥。后端使用当前 Windows 用户的 DPAPI 加密，密文保存在本地 SQLite `model_credentials`；不回显密钥，不把明文写入模型配置、任务或作品。密码框留空保留现有认证引用。更换服务地址且沿用已配置密钥时要求重新填写密钥或选择新的环境变量，防止将旧服务的密钥自动发往新服务。

也可以填写后端进程的环境变量名；需要在启动后端前设置。非 Windows 平台目前使用环境变量方式。切换 Windows 用户或迁移机器后，原密文可能无法解密，需要重新输入。

连接检查会使用上方填写的服务地址和密钥。直接输入的密钥只在保存后持久化；检查不会保存。密钥仅发送给配置的 API 根地址，不跟随重定向。历史密钥的加密版本会保留，以支持已排队任务的旧配置；此版本不提供密钥清理界面。

偏好存入 `model_preferences`，数据目录在 Git 忽略范围内。仅三个模型角色覆盖启动配置，数据目录、记忆上限等后端设置不变。网页不能指定新的可执行程序。CLI 的显式 `--config` / `--offline` 使用方式保持不变，网页偏好不覆盖 CLI 的显式配置。

## 接口

- `GET /api/v1/model-settings`：当前三个角色的配置、密钥已配置状态、版本号；不含密钥或本机可执行文件路径。
- `GET /api/v1/model-settings/presets/bailian-seedream`：不含密钥的三角色预设。
- `GET /api/v1/model-settings/presets/ark-all`：不含密钥的全方舟预设。
- `POST /api/v1/model-settings`：提交三个角色和 `revision`，一次性原子保存。版本冲突返回 409。
- `POST /api/v1/model-settings/{role}/check`：检查未保存的单角色配置。
- `POST /api/v1/model-settings/reset`：带 `revision` 恢复启动配置。

认证信息与角色配置校验失败时不回显输入正文；高级参数只接受角色允许的参数名及基本值类型。测试覆盖加密与重启恢复、任务设置冻结、版本冲突、连接检查无推理、恢复默认和错误响应脱敏。
