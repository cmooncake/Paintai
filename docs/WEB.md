# M0 网页画室

课程与记忆扩展已加入「学习」入口，使用方式、API 和数据兼容说明见 [LEARNING.md](LEARNING.md)。原有画室与作品接口继续可用。

## 使用者入口

浏览器打开 `http://127.0.0.1:5173`。左侧选择或创建画师，填写灵感后点击「开始创作」。页面展示构思、绘制、看图评价的进度；作品自动保存。点击作品可查看原图、六维评分、反馈和建议，保存图片，或重新评价。重新评价保留历次结果，不重新生图。作品库支持分页和按完成 / 失败状态筛选。

当前小模型更适合简短英文。三个中文灵感按钮会填入可直接使用的英文主题。模拟环节会明确标注，评分是模型主观看法，不代表已经证明画师在成长。

## 安装和启动（Windows PowerShell）

需要 Python 3.11+、Node.js 22.12+，在仓库根目录：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,web]"
npm.cmd --prefix web ci
```

真实本地模式先按 [LOCAL_FULL.md](LOCAL_FULL.md) 安装模型，然后：

```powershell
.\scripts\start-web.ps1 -StartModels
```

启动脚本在隐藏窗口运行两个独立进程，并检查端口和 HTTP 就绪状态；不会重复启动已登记的服务。前端 `5173`，API `8000`，接口文档 `http://127.0.0.1:8000/docs`。服务日志与进程身份记录位于 `data/web/`。模型未就绪时仍可浏览已有作品。

仅演示界面、无需下载模型：

```powershell
.\scripts\start-web.ps1 -Offline
```

演示模式生成测试图片和固定模拟评分。切换模式先停止网页服务：

```powershell
.\scripts\stop-web.ps1
```

此命令仅结束登记且 PID、启动时间、可执行文件均匹配的网页 / API 进程树。尽量等创作完成后再停止；强制停止中的任务会在下次启动标记为中断。模型服务由各自的 `stop-local-model.ps1` 和 `stop-local-vision.ps1` 管理，不随网页关闭。

## 前后端边界

```text
web/ React + TypeScript + Vite (5173)
       │ JSON /api/v1 + image URL
       ▼
paintai.api / FastAPI (8000)
       │ 写入 SQLite jobs，立即返回 202
       ▼
单个后台 Worker → PainterAgent → planner / generator / critic
       │
       └─ SQLite 元数据 + 本地图片
```

前端不读取 Python 文件、SQLite、模型配置和本地文件路径。API 不托管前端页面，CLI 与 API 复用现有业务层。HTTP 响应使用白名单，只返回画师、作品、进度和评价，不返回凭据、完整配置、磁盘路径或内部异常。

独立开发，分别在两个终端运行：

```powershell
.\.venv\Scripts\python.exe -m paintai.api --config configs/local-full.toml --port 8000
npm.cmd --prefix web run dev
```

Vite 开发服务器将 `/api` 代理到 `http://127.0.0.1:8000`。前端生产构建：

```powershell
npm.cmd --prefix web run build
```

产物为 `web/dist/`，可独立交给静态服务器。部署时将同源 `/api` 转发到后端，或在构建前设置 `VITE_API_BASE_URL`（只含后端 origin，例如 `http://127.0.0.1:8000`），并在后端设置 `PAINTAI_WEB_ORIGINS` 为允许的前端 origin，逗号分隔。不要把密钥放在 `VITE_*` 中；所有模型选择和密钥环境变量仍由后端 TOML 配置控制。

当前为单机单使用者的 M0，API 监听回环地址，默认允许两个本地前端 origin；没有账户、权限或公网部署。画师隔离是作品组织方式，不是多用户鉴权。对外服务前需要补齐身份认证、授权、限流和部署配置。

## API 约定

基础路径 `/api/v1`，自动生成的 OpenAPI 文档见后端 `/docs`。

| 方法 / 路径 | 作用 |
| --- | --- |
| `GET /health`、`GET /system` | API 存活、三个模型服务就绪状态和模拟标记 |
| `GET /painters`、`POST /painters` | 列表、创建画师（`name`, `style`） |
| `POST /creations` | 提交创作（`painter_id`, `topic`），返回 202 和任务 |
| `GET /jobs?painter_id=...`、`GET /jobs/{id}` | 最近任务列表、单个任务状态和阶段 |
| `GET /painters/{id}/creations` | 作品列表；`limit`, `offset`, `status=all/complete/failed` |
| `GET /painters/{id}/creations/{entry}` | 作品与历史评价 |
| `GET /painters/{id}/creations/{entry}/image` | 图片字节流 |
| `POST /painters/{id}/creations/{entry}/evaluate` | 重新看图评价，返回 202 |

两个提交任务的 POST 要求 `Idempotency-Key`（8–100 字符）。同一个 key 重试返回同一任务；不同内容复用 key 返回 409。每位画师最多一个活动任务，全局最多 8 个排队 / 运行任务；同一数据目录只有一个 API worker，由操作系统文件锁保证。CLI 不经过网页队列，避免与网页同时操作同一画师。

任务状态：`queued → running → succeeded / failed`。进程意外退出后，已运行的任务标记 `interrupted`，不自动重复生图；未开始的任务继续排队。已有图片可重新评价，尚无图片则重新发起创作。队列保存提交时配置快照，后续配置变化不改变已排队任务。浏览器每 2.5 秒读取作品与任务进度，离开网页不会取消后台任务。

`/system` 对兼容 API 使用 `/models` 检测可达性，本地生图检查程序和模型文件是否存在；这不替代真实推理验证。某些兼容服务不支持 `/models` 时需调整健康检查适配。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests
npm.cmd --prefix web run build
```

HTTP 集成测试覆盖离线完整闭环、图片读取与画师隔离、重复提交、重新评分历史、输入校验、跨源写入拒绝、队列恢复和单 worker 锁。真实模型仍需按本机环境验收。

2026-11-03 本机验收：51 项测试通过，Ruff 与 TypeScript / Vite 构建通过。从网页新建水彩画师「拾光」，提交「海边日落」灵感，刷新后任务继续，生成真实图片并获得 Qwen3-VL 评价 8.83 / 10；再次从网页重评成功，保留两次记录。作品 ID `3450da6f96f4428ca8cac52e52511dda`，无模拟环节。已检查桌面与 390px 窄屏详情，重启网页 / API 后作品仍可读取。这是流程验收，不是评分可靠性结论。

学习页现支持长期成长目标、本轮自然语言要求、难度与 1–6 幅作品数量。预览题目后开始，作品详情和记忆可查看原始要求；完整规则见 [LEARNING.md](LEARNING.md)。

M2a 新增「学习 → 成长实验」：同题成对蒸馏与冻结经验有/无对照。实验作品不会写回记忆或推进课程；详见 [GROWTH.md](GROWTH.md)。

左侧新增「模型设置」：独立切换构思、生图和视觉评价服务，支持连接检查、Windows 加密保存密钥、持久化偏好及恢复启动配置。详见 [MODEL_SETTINGS.md](MODEL_SETTINGS.md)。
