# 全本地闭环：构思、生成、视觉评价

`configs/local-full.toml` 配置三个真实本地角色：

| 角色 | 模型与运行方式 | 本机地址 |
|---|---|---|
| 构思 | Qwen2.5 0.5B / llama.cpp CPU | 127.0.0.1:18080 |
| 生图 | SD-Turbo Q8_0 / stable-diffusion.cpp CPU | 每次生成启动本地进程 |
| 评价 | Qwen3-VL 2B Instruct Q4_K_M + F16 视觉组件 / llama.cpp CPU | 127.0.0.1:18081 |

评价器接收实际 PNG 的 base64 数据，直接查看像素，不使用生成提示词代替图片。请求包含主题与目标风格，不包含此前评分、创作轮次或 Compose 生成的提示词。评分仍是模型主观评价，不是经过校准的艺术质量标准。

## 准备与启动

已有文本模型和生图权重时，只需增加视觉模型：

```powershell
.\.venv\Scripts\python.exe scripts/setup-local-vision.py
.\scripts\start-local-model.ps1
.\scripts\start-local-vision.ps1
```

新环境还需按 [文本模型说明](LOCAL_MODEL.md) 和 [生图说明](LOCAL_IMAGE.md) 先准备运行器与权重。

视觉权重来自 [Unsloth 的 Qwen3-VL-2B-Instruct GGUF](https://huggingface.co/unsloth/Qwen3-VL-2B-Instruct-GGUF)，底模为 [Qwen3-VL-2B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct)。模型约 1.11 GB，视觉组件约 819 MB；脚本支持断点续传，校验大小及 SHA256。文件存放在 `data/local-vision/`，不进入 Git。下载脚本只下载权重，不执行远端代码。

复用 llama.cpp b12064；视觉服务为 CPU、8 线程、8192 上下文、单并发，图像 token 上限 1024。只监听 loopback，无需云端密钥。启动、重复启动检测和停止独立于文本服务。

## 创建与评价

```powershell
# 若已存在 local_art，跳过此行
.\.venv\Scripts\paintai.exe --config configs/local-full.toml painter add local_art --name "本地画师" --style "watercolor painting"
.\.venv\Scripts\paintai.exe --config configs/local-full.toml create "An autumn forest with golden leaves, a winding path and soft morning sunlight" --painter local_art
```

成功记录应为 `status=complete`、`simulated=false`，`simulation` 三个角色均为 `false`。`critique` 包括六维 1–10 分、等权平均总分、文字反馈及建议。总分在本地计算，不由模型直接决定。

替换历史作品的模拟评分，不必重新生图：

```powershell
.\.venv\Scripts\paintai.exe --config configs/local-full.toml evaluate <作品ID> --painter local_art --force
```

不带 `--force` 时，已完成作品仍幂等返回原结果。显式重评保留原评分到 `evaluation_attempts`，每次记录图片 SHA256、评价配置、评分、时间和状态。顶层 `config` 保留创作时配置，当前评分配置以最新成功的评价尝试为准。重评失败时不将旧分数冒充新结果，旧分数仍在历史中。

## 验证图片输入

```powershell
.\.venv\Scripts\python.exe scripts/verify-local-vision.py --image <生成图片路径>
```

对输入图片和白底红方块/蓝圆形测试图提出完全相同的中性问题，不发送文件名、主题或答案。`data/local-vision/verification.json` 保存两张图的哈希、问题和真实回答。需人工检查描述是否符合画面；不同回答本身不等于评价准确。这是视觉输入验证，不是审美可靠性基准。

## 停止与排错

```powershell
.\scripts\stop-local-vision.ps1
.\scripts\stop-local-model.ps1
```

视觉日志位于 `data/local-vision/server.stderr.log`。评价超时 600 秒，输出上限 2048 token；截断输出显式失败，不存入伪造分数。评价失败后图片会保留，修复服务后用 `evaluate` 重试。

构思模型仅 0.5B，复杂中文曾出现结构化输出失败，首次验证建议使用上述英文主题。全本地链路跑通不代表课程学习或持续成长已得到验证。

## 本机验收记录（2026-11-03）

- 新作品：`7355c47a7415446e9306816a830cb9a8`，画师 `local_art`；`status=complete`、`simulated=false`，三个 `simulation` 标记均为 false。
- 图片：`data/painters/local_art/gallery/7355c47a7415446e9306816a830cb9a8.png`。其 SHA256 与评价记录一致。
- 流程耗时约 35.37 秒，评价约 12.99 秒（单次观测）。
- 模型评分：主题 9、技法 8、构图 9、色彩 9、风格 9、创意 9；本地计算均分 8.833。
- 反馈提及金黄树叶、深色树干、小径引导线和斑驳光影，建议增强笔触质感、细化光影及加入冷色调。
- 同一个中性描述问题下，模型把真实图片描述为秋日树林与道路，把测试图正确描述为白底右上红方块、左下蓝圆形。原始回答见 `data/local-vision/verification.json`。
- 旧作品 `4b0376aa7ff94d1d9c2d2ef587ba9d50` 已用 `evaluate --force` 重评，原模拟 5 分保留在历史，新模型给出 9 分。

这些检查证明本地像素输入、模型评价和保存链路实际运行。模型对水彩风格仍给出偏高评分，尽管画面的明确水彩笔触较弱；后续需用人工样本、重复评分和对照实验校准，不能将本次高分当成客观质量或成长证据。
