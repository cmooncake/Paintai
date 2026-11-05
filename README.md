# Paintai

> 一个会持续学习、持续进化的 AI 画师——模拟 AI 画师从零开始系统学画、持续成长的完整过程。

Paintai 不是又一个「输入 prompt 输出图片」的图像生成器。它是一个画画的 Agent：有自己的学习轨迹、成长曲线、风格偏好和创作记忆。

---

## 这个仓库现在有什么

目前已实现并实测 **M0 全本地创作闭环**：画师管理 → Qwen 构思 → SD-Turbo 生图 → Qwen3-VL 看图六维评价 → SQLite 作品库。三个角色均使用真实本地推理，云端 API 尚未实测。

## 快速运行画室

网页左侧「模型设置」可以更换构思、生图和视觉评价模型，保存后对新任务生效；支持本地服务、兼容 API 和 Windows 加密密钥。详见 [模型配置说明](docs/MODEL_SETTINGS.md)。

「学习」页支持上传参考作品、提取画风特点、编辑并启用风格档案，档案贯穿后续创作、评价和记忆记录。详见 [参考图学习](docs/STYLE_LEARNING.md)。

新增 **课程学习、创作记忆与自定义成长目标**：网页「学习」可查看默认 L1–L2 六关课程和进度，用自然语言设置长期目标与本轮练习要求，选择难度和 1–6 幅作品，预览题目后开始。标准考核为三幅作品；专项练习保存评价和记忆，但不计入关卡通过。创作反馈会在后续相关创作中引用。详见 [学习功能说明](docs/LEARNING.md)。

新增 **经验蒸馏与成长实验**：在「学习 → 成长实验」查看同题高低分来源，运行有经验 / 无经验配对对照，用并排作品、六维差异和探索性统计判断是否有提升迹象。详见 [成长实验说明](docs/GROWTH.md)。

现在可以从独立网页画室使用 M0：创建画师、输入灵感、查看创作进度、浏览作品与六维评价，以及重新看图评价。

已有本地模型的本机启动方式：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,web]"
npm.cmd --prefix web ci
.\.venv\Scripts\python.exe scripts/setup-local-anime.py
.\scripts\start-web.ps1 -StartModels
```

打开 **http://127.0.0.1:5173**。前端 React + TypeScript 位于 `web/`，后端 FastAPI 独立运行于 `127.0.0.1:8000`，通过 `/api/v1` 通信；模型配置留在后端。首次安装模型、开发方式和 API 约定见 [网页画室说明](docs/WEB.md)。

### CLI

Python 3.11+，在仓库根目录运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\paintai.exe --offline painter add demo --name "小画师" --style "水墨"
.\.venv\Scripts\paintai.exe --offline create "秋天的树林" --painter demo
.\.venv\Scripts\paintai.exe --offline gallery --painter demo
```

`--offline` 生成明确标注的测试图片和固定模拟评分，**不调用模型、不验证艺术质量**。输出包含 `simulated: true`、本地图片路径、构思、六维评分、建议和作品 ID。所有命令输出 JSON。

真实 API 配置、失败恢复和验收说明见 [M0 使用说明](docs/M0.md)。

无云端 API 时，可使用 [本地小模型试运行](docs/LOCAL_MODEL.md)：Qwen2.5 0.5B 在 CPU 上负责真实构思，生图与评价仍为模拟模式。

进一步可使用 [本地真实生图](docs/LOCAL_IMAGE.md)：Qwen 构思 + SD-Turbo CPU 生图，评价仍为模拟模式。

完整体验见 [全本地闭环](docs/LOCAL_FULL.md)：`configs/local-full.toml` 增加 Qwen3-VL 视觉评分，三个角色均为真实模型。

当前全本地配置的生图模型已改为 Counterfeit V3 二次元模型，安装和参数见 [本地二次元生图](docs/LOCAL_ANIME.md)。

另提供 Animagine XL 4.0 Opt 可选配置及 SD1.5/SDXL 小规模 CPU LoRA 训练、同题同种子对照工具，见 [LoRA 训练说明](docs/LORA.md)。当前实验已验证真实参数更新和适配器加载，但尚未证明整体目标画风明显改善。模型权重、参考作品、训练清单和运行记录保存在本机 `data/`，不随代码提交；复现实验需要先准备相应本地资源。

## 设计文档

| 文档 | 内容 | 状态 |
|---|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 架构方案 v1.1——核心概念、系统总览、五大模块、数据流、技术选型、仓库结构、MVP 路线、设计取舍、待确认项 | 已定稿 |
| [docs/RESEARCH_EXECUTION.md](docs/RESEARCH_EXECUTION.md) | 执行文档 v1.0——16 篇论文调研矩阵、与现有工作的差异化定位、Phase 0–2 细化路线、技术风险矩阵、完整依赖清单 | 已定稿 |

## 一句话定位

> 这个领域现在争的是「单次生图质量最优」。Paintai 不进这条跑道。
> 独占的象限是：**有记忆 + 有课程 + 纵向成长 + 画师实例可存档**。

已有工作没有任何一篇同时具备这四项。

## 核心机制（速览）

- **画师实例是一等公民**——身份 + 配置 + 自己的课程规格 + 自己的记忆 + 自己的作品库。实例间彼此隔离，可并排演进，也可各自打包带走
- **课程路径彻底可自定义**——内置 L1–L6 只是默认模板，与自定义课程规格平权；进度锚点改用稳定的 `stage_id` / `lesson_id`
- **存档粒度部署时自选**——`export --granularity full|pointer`。full 把作品与记忆全打包，导入即独立运行；pointer 只存配置与进度，作品留在原环境
- **成长可观测**——同一主题的多次重画自动串成进化链，六维 Critic 评分曲线看得见

## 阅读顺序

1. `docs/ARCHITECTURE.md` 第一节「核心概念」——建立整体认知
2. `docs/RESEARCH_EXECUTION.md` 第二节「论文矩阵」——理解「为什么这么设计」
3. `docs/RESEARCH_EXECUTION.md` 第五节「细化执行路线」——直接开工

## 最大的未验证假设

**「同一主题反复创作时，Critic 评分能否单调提升」——没有任何论文验证过这一点。**

这是整套成长叙事的根基假设。要求 Phase 2 早期先做 10 轮 A/B 实验验伪，不要等到 Phase 3 才发现成长曲线是平的。详见 `docs/RESEARCH_EXECUTION.md` 第 6.2 节。

## 当前状态

- 架构方案：v1.1，已定稿
- 执行文档：v1.0，已定稿
- 代码：M0 CLI、独立网页画室 / HTTP API、持久化任务队列、可替换模型接口、画师隔离、作品持久化及失败恢复已实现
- 已实测：Qwen 构思 + SD-Turbo CPU 512×512 生图 + Qwen3-VL 真实看图评价，完整入库
- 已验证：换图视觉描述、历史作品重评、评价记录与图片哈希；评分可靠性仍需校准
- 新增：Spec 驱动课程、三图标准考核、网页学习进度、创作记忆保存与检索注入；长期目标和本轮要求贯穿出题、构思、评价及历史快照
- 新增：抽取式 best-worst 蒸馏、冻结经验 A/B 实验与网页对照报告
- 后续：向量检索、完整 VED、跨题泛化与课程顺序消融、画师存档

## 参考资料

完整论文索引见 `docs/RESEARCH_EXECUTION.md` 附录；架构方案的资料清单见 `docs/ARCHITECTURE.md` 第十节。
