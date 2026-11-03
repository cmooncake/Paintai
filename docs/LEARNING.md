# M1 课程学习与创作记忆

本轮实现文档 Phase 1 的课程闭环，并提前接入 Phase 2a 的本地记忆基线。这里的“学习”指课程推进、经验保存和创作上下文更新，不训练或修改模型权重，也不保证评分持续上升。

## 网页使用

启动方式仍见 [WEB.md](WEB.md)。选择画师后进入「学习」：

1. **课程学习**：默认两阶段六关，涵盖基本形、光影、色彩、焦点、空间和节奏。点击「预览本关练习」，核对题目后提交；默认标准考核为 3 幅作品，后台串行完成构思、生图和看图评价。
2. **自动推进**：标准考核三幅均完成且无模拟环节时，六维综合分的平均值达到课程阈值即通过，解锁下一关。网页每次只提交一关，不会未经操作继续生成整套课程。
3. **练习与复习**：每轮成绩和作品可查看；未通过可以再练，已通过也可复习。复习分数降低不抹掉历史通过记录。模拟练习有记录，但不会过关。
4. **创作记忆**：完成评价后自动保存反馈、改进建议及来源作品。点击「整理历史作品」可纳入 M0 时期的作品；同一作品只保留一条最新记忆，完整重评历史仍在作品内。
5. **经验引用**：下一次自由创作或课程练习会检索本画师的相关经验，给构思模型参考。作品详情的「本次参考了…条创作记忆」保留当时注入的建议快照。

进度按每位画师独立保存。模拟记忆只展示、不参与检索。课程成绩保存的是本轮当时的评价；后来对单张作品重评不会追改已经结束的课程成绩，需要再练一轮才能产生新的课程结果。

## 课程规格

默认模板：`src/paintai/curriculum/default_curriculum.yaml`，作为 Python 包资源随安装提供。课程不要求使用 L1–L6，允许任意阶段名称和关卡内容。

```yaml
schema_version: 1
id: ink_studies
title: 水墨小练习
stages:
  - id: ink_basics
    name: 笔墨基础
    goals: [疏密对比]
    lessons:
      - id: bamboo
        name: 一枝竹
        task: A bamboo branch with varied ink tones on white paper
        skills: [ink_contrast, negative_space]
        pass_score: 6.5
        depends_on: []
```

阶段 ID 与关卡 ID 分别唯一，关卡 ID 在整份课程中唯一；阈值为有限数值 1–10；依赖必须指向之前定义的关卡，因此缺失依赖、向前依赖和循环依赖都会拒绝。引擎按定义顺序推进。每关的三个任务使用不同构图变体，每个任务都包含该关全部技能标签。当前不是额外调用模型自动出题。

网页的「自定义课程」接受同样结构的 JSON；CLI 接受 YAML。当前任务未结束时禁止换课程。课程 ID、阶段 ID、目标与关卡定义组成成绩匹配指纹；仅改课程标题或调整不影响依赖的顺序不会错位，改任务、技能、阈值、依赖等要求需要重新练习。旧记录始终保留，更换课程不删作品。

## CLI

CLI 训练与 API 共享单 worker 文件锁。使用 CLI 训练前停止网页 / API 服务；本地模型服务保持运行。请勿同时用旧的 `create/evaluate` CLI 操作正在网页训练的画师。

```powershell
.\scripts\stop-web.ps1
.\.venv\Scripts\paintai.exe --config configs/local-full.toml train --painter local_art --spec default_curriculum
.\.venv\Scripts\paintai.exe --config configs/local-full.toml progress --painter local_art
.\.venv\Scripts\paintai.exe --config configs/local-full.toml memory --painter local_art --sync
```

`--spec path/to/course.yaml` 绑定自定义课程；不传 `--spec` 则沿用当前课程。`--lessons N` 最多自动运行 N 关（1–12），通过后继续下一关，未通过、模拟结果或错误会停止，不无限重试。默认只运行一关。`--lesson bamboo` 可复习已解锁的关卡。

离线验收：

```powershell
.\.venv\Scripts\paintai.exe --offline --data-dir data/learning-demo painter add demo --name "课程演示"
.\.venv\Scripts\paintai.exe --offline --data-dir data/learning-demo train --painter demo --spec default_curriculum
```

## 独立 API

| 路径（前缀 `/api/v1`） | 行为 |
| --- | --- |
| `GET /curricula/default` | 默认课程规格 |
| `GET /painters/{id}/learning` | 当前规格、阶段 / 关卡、百分比和最近 30 轮记录 |
| `POST /painters/{id}/curriculum` | 校验并绑定 JSON 课程 |
| `POST /painters/{id}/train` | body `{}` 或 `{"lesson_id":"..."}`，返回 202 任务 |
| `GET /painters/{id}/memory` | 最近 100 条记忆与检索实现标识 |
| `POST /painters/{id}/memory/sync` | 从该画师已完成的历史作品整理记忆 |

训练提交必须携带 `Idempotency-Key`，重复请求返回原任务。课程快照在入队时固定，沿用现有任务并发限制。`jobs.training` 返回关卡名和已完成 / 总练习数。

SQLite 增量添加 `curricula`、`learning_rounds`、`experiences` 表及 `jobs.payload` 字段，已有 M0 作品和任务不重建。课程和记忆分别位于 `learning.py`、`memory.py`，前端独立组件为 `LearningPanel.tsx`，继续通过 API 交互。

任务中断时保留已生成图片和已完成练习，重启后整轮标记为中断，不误判过关、不自动重新生图。新的一轮按本轮数量重新生成作品。已经生成图片但评价失败时仍可在作品详情重评；重评不会将原先未完成的一轮自动补为通过。

## 记忆基线与后续范围

当前使用 SQLite + 本地英文词 / 中文双字片段匹配，并优先匹配技能标签。从最近 1000 条记忆中检索，只有非模拟且相关的记忆会进入构思上下文。后端 `memory_top_k` 默认 3，可设 0–5；设 0 关闭注入以便对照实验。`local-full.toml` 针对 0.5B 小模型设为 1，减少上下文负担。每条最多注入两条建议，文字有长度限制，历史分数不注入。视觉评价仍只看当前图片与要求，不接收历史评价或记忆建议。

经验直接来自 Critic 反馈，保留来源而不把建议包装成已验证规律。`Retriever` 协议可替换为后续 Embedding 实现。文档 Phase 2a 的 ChromaDB / 向量检索、best-worst 轨迹对比蒸馏、10 轮记忆开关 A/B 实验仍未实现；画师存档、迁移和派生也不在本轮范围内。生成 / 服务故障保留为失败记录，不从无视觉反馈的异常中虚构艺术经验。

## 自动化验收

`pytest` 覆盖规格重复 ID / 依赖 / 阈值校验、任务技能覆盖、三图平均分、模拟不通过、失败与中断保留、L1→L2 推进、画师隔离、课程修改对齐、记忆去重与检索、真实上下文注入路径、HTTP 幂等与课程冲突、CLI 有界训练。引擎评分测试使用合成证据，不能视为实际模型质量验收。

2026-11-03 本机真实验收：画师「拾光」完成「看见基本形」一轮，三幅分别为 8.667、8.5、8.5，均分 8.556；无模拟环节，进度变为 1/6（17%），解锁「光与影的方向」。任务 ID 为 `b74cf7e7cfd14f10bed3150952cc59f9`。作品详情可核对引用的经验快照；课程运行新增 5 条真实记忆（含先前未完成轮次中已成功评价的 2 幅）。前两轮失败记录保留，不计为过关。

验收中发现 0.5B 构思模型偶发反复扩写，现对推理输出使用有长度限制的 `CompactPlan` schema，历史 `Plan` 数据格式不变。此措施限制输出长度，不代表小模型理解和艺术质量已经可靠。最终 62 项测试、Ruff、前端生产构建通过。


## 自定义学习方向

新建画师可以填写「长期成长目标」（最多 400 字），已有画师在学习页修改。目标参与自由创作和课程练习的构思与评价。修改后按目标内容重新计算课程进度，旧作品、成绩、记忆保留；切回原目标可恢复该方向对应进度。运行中禁止修改目标或课程。

「本轮练习要求」包含自然语言要求（400 字）、重点能力（100 字）、难度（入门/标准/挑战）和数量（1–6 幅）。先选择关卡预览具体题目，再点击开始。默认课程的示例题材会结合目标改写，用户自定义课程的题目要求保留。具体要求优先于宽泛偏好。

只有 3 幅、标准难度、无附加要求的标准考核参与关卡通过；其他选项为专项练习，照常记录作品、分数和经验。评分仍为固定六维平均分，评价模型需要针对图片中的可见证据说明目标和要求的完成情况。界面得分不代表模型能力已被严格验证。

目标和练习要求在提交任务时形成不可变快照，贯穿题目、构思、视觉评价、作品详情、轮次历史和创作记忆。重新评价使用作品原快照。视觉评价不会收到历史评分或检索的经验；经验仅提供给构思阶段。这里的学习是课程与上下文记忆，不修改模型权重。

「按已保存目标准备课程草案」使用固定六关模板生成可编辑草案，不调用模型，也不自动覆盖课程。审阅 JSON 后点击保存才生效。

新增接口（保持前后端通过 HTTP 分离）：

- `POST /api/v1/painters`：可携带 `growth_goal`。
- `POST /api/v1/painters/{id}/goal`：`{"growth_goal":"学习清淡水彩山水"}`。
- `POST /api/v1/painters/{id}/curriculum/preview`：按已保存目标返回草案，不保存。
- `POST /api/v1/painters/{id}/train/preview`：返回本轮题目和要求，不创建任务。
- `POST /api/v1/painters/{id}/train`：与预览使用相同请求体，并携带 `Idempotency-Key`；不同练习参数不能复用同一键。

练习请求示例：

```json
{"lesson_id":"simple_shapes","instructions":"远山用冷色，近景用暖色，保持构图简单。","focus":"冷暖对比","difficulty":"gentle","count":1}
```

旧画师默认目标为空，旧作品和旧轮次不需要数据迁移。

2026-11-03 自定义方向真实验收：网页新建「山水习作」，目标为清淡水彩山水；本轮冷暖对比、入门、1 幅。任务 `78c81382f5a1444fbd3127feb1f2e44d` 完成，作品 `dee6e658f14145d9ad3f06480a032c11` 的真实视觉评分 8.667，记忆保留相同目标和练习要求。专项练习未推进关卡。模型虽能生成山景并给出针对要求的反馈，画面风格与冷暖表现仍未完全满足预期，评分存在宽松倾向；验收证明数据与模型链路贯通，不代表质量标准已达成。66 项自动测试、Ruff 和网页构建通过。
