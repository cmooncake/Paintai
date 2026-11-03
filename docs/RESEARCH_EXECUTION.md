# Paintai 执行文档：调研 · 定位 · 路线

> 文档版本：v1.0 | 2026-11-03
> 基于 Paintai 架构方案 v1.1 的细化执行文档，覆盖论文调研、工程实践、相对定位、执行路线与技术风险。

---

## 一、调研概述

本章覆盖 2025–2026 年与 Paintai 最相关的两条研究主线：**Agentic Image Generation**（以 Agent 方式编排生图流程）和 **RL-based Neural Painting**（以强化学习模拟笔触级绘画）。每条主线的核心论文均从方法、架构、数据、训练策略四个维度展开，末尾标注与 Paintai 的可复用关系。

**检索范围**：GenEvolve 及其引用链（Gen-Searcher、ToolArtist、GEMS 等）、GenAgent 及其引用链（MemoGen、OctoT2I、VisionCreator-R1 等）、以及笔触级绘画（PaintBot、Emergence of Painting Ability、Stroke-GAN Painter、Compositional Neural Painter）。

---

## 二、Agentic Image Generation 论文矩阵

### 2.1 GenEvolve（2026.05 · Meituan · arXiv:2605.21605）★ 最核心

**目标**：构建一个能通过轨迹自我进化的通用图像生成 Agent——不只是 prompt-to-image，而是规划、工具编排、反馈驱动的完整 Agent 流程。

**核心方法**：

- **Tool-Orchestrated Visual Experience Distillation (VED)**：将每次生成建模为工具编排轨迹——Agent 依次调用 search（文本证据）、image_search（视觉参考）、query_knowledge（可调用生成技能），最终合成一个 Prompt-Reference Program $z = (g, R)$，交给下游生成器。
- **5 维经验蒸馏**：对比同一请求的 best-worst 轨迹对，将差异抽象为 5 个结构化经验槽位——Search Strategy（搜索策略）、Knowledge Activation（知识激活）、Reference Selection（参考选择）、Prompt Construction（提示构建）、Failure Avoidance（失败规避）。经验仅注入特权 Teacher 分支，Student 通过重要性加权的反向 KL 蒸馏学习。
- **自我进化闭环**：GRPO + VED 形成闭环——更强的策略 → 更好的轨迹 → 更丰富的经验 → 未来的进一步改进。

**训练管线**：
1. 自动数据构建：structured recipes → prompt → Teacher agent 多轮工具轨迹 → VLM 审计 → GT 图像 → 三路分片（SFT/self-evolution/benchmark）
2. SFT 冷启动：LLaMA-Factory，Qwen3-VL-8B-Instruct 骨干，32K cutoff
3. GRPO + VED 自我进化：每组 6 条轨迹采样，KScore + program-sufficiency 双评判，best-worst 对比蒸馏

**实验结论**：
- GenEvolve-Bench 上 KScore 0.5739（Nano Banana Pro），显著超越 Gen-Searcher 的 0.5481
- WISE 基准上 Overall 0.82，超越 Mind-Brush(0.78) 和 GenAgent(0.72)
- 跨生成器迁移：Qwen-Image-Edit 和 Nano Banana Pro 上均有效，策略具有生成器泛化性

**对 Paintai 的可复用性**：VED 的 5 维经验结构可直接映射到 Paintai 的 Memory 模块；Prompt-Reference Program 机制对应 Compose 模块的输出；GRPO 训练范式可引入到 Phase 3 的策略优化。但 GenEvolve 聚焦“单次生成质量最优”，不对成长曲线建模。

---

### 2.2 GenAgent（2026.01 · 复旦/通义 · arXiv:2601.18543）★ 核心

**目标**：通过 Agentic 多模态模型统一视觉理解与生成——理解由模型自身完成，生成通过将扩散模型作为可调用工具完成，实现多轮自主推理与反思。

**核心方法**：

- **两阶段训练**：
  1. SFT 冷启动：在高质量工具调用与反思轨迹上做监督微调，建立基础 Agent 行为
  2. Agentic RL（GRPO）：混合奖励——Pointwise Reward（MLLM 评判最终图像是否满足所有条件，严格 0/0.7 二元）+ Pairwise Reward（连续两轮图像质量是否单调提升，0/0.3 二元）
- **多轮交互**：Agent 生成多模态思维链（推理 → 工具调用 → 评判 → 反思），最多 3 轮交互
- **轨迹重采样**：对交互轮数做 oversampling + 均匀重采样，扩大探索空间

**实验结论**：
- GenEval++ 提升 +23.6%，WISE 提升 +14%（以 FLUX.1-dev 为基座）
- 跨工具泛化：换用 Sana1.5-1.6B / Qwen-Image 均显著提升
- 测试时扩展：交互轮数增加时性能持续提升
- 任务自适应推理：自动根据任务类型调整交互策略

**对 Paintai 的可复用性**：多轮反思机制（Reasoning → Generation → Judgment → Reflection）直接对应 Paintai 的 Execute → Critic → Reflect → Re-execute 循环；Pairwise Reward 的“单调改进”思路可用于 Critic 的跨版本对比评分。

---

### 2.3 Gen-Searcher（2026.03 · arXiv:2603.28767）

**目标**：训练一个多模态深度搜索 Agent，通过多跳推理与搜索为图像生成收集知识。首个在图像生成领域做 search-augmented agentic RL 的工作。

**核心方法**：
- 三工具：search（文本搜索） + image_search（图像搜索） + browse（网页浏览摘要）
- 数据构建：20+ 类别（名人、动漫、物理、化学、海报、艺术等）+ 深度研究数据集转换
- 双反馈奖励：text reward（GPT-4.1 评估搜索提示的充分性，0-1 五级）+ image reward（KScore）

**实验结论**：Qwen-Image 在 KnowGen 上提升约 16 分，WISE 上提升约 15 分。全开源（数据、模型、代码）。

**对 Paintai 的可复用性**：工具链设计（search + image_search + browse）可引入 Paintai 的 Reference Retriever 模块作为外部参考源之一。

---

### 2.4 ToolArtist（2026.08 · arXiv:2608.04436）

**目标**：将整个开放世界图像生成过程置于单一的 UMM Agent 策略下——推理、搜索、原生图像生成全部由一个统一策略编排。

**核心方法**：
- RAD-GRPO（Reason-Act-Draw GRPO）：双奖励——Intent Reward（生成描述是否准确充分地将请求与证据转化为可执行视觉意图）+ Quality Reward（最终图像是否忠实于意图和原始请求）
- 原生生成：图像由 UMM 自身产生（visual tokens），而非调用外部生成器

**对 Paintai 的可复用性**：Intent + Quality 双奖励设计可借鉴到 Critic 的评价维度分化；但“原生生成”路线强依赖 UMM 能力，与 Paintai 的多后端可插拔策略不同。

---

### 2.5 GEMS（2026 · Agent-Native Generation with Memory and Skills）

**核心发现**：通过 Agent harness（Memory + Skills），轻量级 6B 模型 Z-Image-Turbo 在 GenEval2 上超越 SOTA Nano Banana 2。

**对 Paintai 的可复用性**：Memory + Skills 架构与 Paintai 的 Memory & Evolution Loop 高度一致；6B 模型 + Agent 超越大模型的范式验证了 Paintai “Agent 编排优于模型规模”的基本假设。

---

### 2.6 Qwen-Image-Agent（2026）

**架构**：Plan → Reason → Search → Memory → Feedback 的 context-centric Agent。

**对 Paintai 的可复用性**：五阶段流水线与 Paintai 的 Perceive → Compose → Execute → Critic → Learn 高度对应。Memory 的显式建模可作为 Painter Instance 记忆系统的工程参考。

---

### 2.7 其他交叉验证论文

| 论文 | 时间 | 核心贡献 | 与 Paintai 关系 |
|------|------|---------|---------------|
| **RSIgen** | 2026.10 | 互惠自我改进：learning allocation + supervision refresh + recursive curriculum | 递归课程构造与课程引擎设计理念一致 |
| **MemoGen** | 2026.06 | 验证“过去经验能否改进未来生成” | 直接验证 Paintai 经验记忆的核心假设 |
| **OctoT2I** | 2026.06 | 自我进化的 Agentic T2I 路由器 | 路由器概念可用于后端选择优化 |
| **VisionCreator-R1** | 2026 | 反思增强的原生视觉生成 Agent 模型 | 反思机制可参考 |
| **CRAFT** | 2026 | 连续推理与 Agentic 反馈调优 | 连续改进循环 |
| **APT** | 2026.10 | 反馈驱动的 Agentic 后训练 | 自适应后训练反馈环 |
| **Unify-Agent** | 2026 | world-grounded 图像合成的统一多模态 Agent | 证据搜索 + grounded recaptioning |

---

## 三、RL-based Neural Painting（笔触级）

### 3.1 PaintBot（2019 · arXiv:1904.02201）

基于 PPO 的笔触级绘画：action = 连续 6D 向量（角度、长度、宽度、颜色），self-centered observation patch，课程学习 + difficulty-based sampling。

### 3.2 Learning to Paint with Model-based DDPG（2019 · arXiv:1903.04411）

DDPG + 可微分神经渲染器：stroke = 二次 Bezier 曲线参数，WGAN reward。核心创新是显式建模绘画环境使 Agent 高效探索。

### 3.3 Compositional Neural Painter（2023 · arXiv:2309.03504）

动态预测绘画区域（compositor）+ 笔画参数预测（painter）+ phasic RL + WGAN discriminator。扩展了基于笔画的风格迁移。

### 3.4 Emergence of Painting Ability via Recognition-Driven Evolution（2025.01 · NTU/RIKEN/东大/Sakana AI · arXiv:2501.04966）★ 重要

**核心洞察**：绘画能力可以在“视觉通信效率”的进化压力下**自主涌现**——不需要模仿人类绘画数据。

- Stroke Branch（Bezier 曲线参数化笔画）+ Palette Branch（学习有限调色板）
- 高层识别模块（CLIP）评估识别准确率 → 最小化语义损失
- 约束：最少笔画 + 最少颜色 → 最大化识别准确率

**哲学对齐**：这与 Paintai 的核心理念高度一致——“绘画能力不是被训练出来的，而是在课程压力下逐渐涌现的”。该论文提供了一个可计算版本的“涌现”范式，Paintai 的课程引擎（L1→L6 的难度递增）本质上是为这种涌现提供结构化的进化压力。

### 3.5 Stroke-GAN Painter（2022 · 清华）

三玩家 GAN 生成纯色笔画；支持多种艺术风格（水彩、油画、粉彩）。端到端训练，通过选择不同预训练模型切换风格。

---

## 四、Paintai 的相对定位：差异矩阵

### 4.1 核心定位

当前 Agentic Image Generation 领域的争夺焦点是**单次生成质量的最优**——GenEvolve 的 KScore、GenAgent 的 GenEval++ 提升、GEMS 的基准超越，都在同一条跑道上。Paintai 不参与这场竞争。

Paintai 的差异化落在三个维度上，这些维度在已有文献中**几乎没有被系统探索**：

| 维度 | 已有工作 | Paintai |
|------|---------|---------|
| **时间轴** | 横向（单次质量最优） | **纵向**（同一主题反复画，每次更好） |
| **学习结构** | 无 / 隐式课程 | **显式可自定义课程**（从基础元素到自由创作的分阶段系统学习） |
| **实体性** | 一次运行 / 临时 Agent | **一等画师实例**（身份 + 课程 + 记忆 + 作品，可存档、可迁移、可派生） |

### 4.2 四象限定位图

```
                    生图质量最优
                         ▲
          GenEvolve ●    │    ● ToolArtist
          GenAgent   ●    │    ● GEMS
          Gen-Searcher●    │
                         │
    ── 无记忆/无课程 ─────┼────── 有记忆/有课程 ──→
                         │
                         │    ★ Paintai
          PaintBot    ●   │    （纵向成长 + 课程系统
                         │      + 画师实例存档）
          Emergence   ●   │
          of Painting     │
                         ▼
                    笔触级绘画 / 涌现
```

Paintai 的独特象限：**有记忆 + 有课程 + 纵向成长 + 画师实例化**。

### 4.3 差异化落点清单

1. **纵向成长曲线**：已有工作不关心“同一主题画第二次时是否比第一次好”。Paintai 的 Gallery 进化链和 Critic 跨版本对比评分专为此设计。
2. **自定义课程路径**：GenEvolve/GenAgent 等没有任何课程概念。Paintai 的 Curriculum Spec 允许用户定义任意阶段/关卡组合，画师的“出身”可以完全不同。
3. **画师实例存档**：已有工作的 Agent 是一次性运行的——没有持久化身份、无法导出导入、不支持派生。Paintai 的 Painter Instance 是一等公民，full/pointer 双粒度存档支持迁移、分享、派生。
4. **可观测成长**：用户能看到画师在 L1 和 L5 画同一主题的水平差异——这是产品级差异化，不是纯技术差异。
5. **结构化多维评价**：已有工作的 reward 是标量（KScore / pairwise 0/1）。Paintai 的 Critic 输出 6 维评分 + 文字反馈 + 改进建议，反馈粒度更高。

---

## 五、细化执行路线

### 5.1 Phase 0：基础框架（1–2 周）

**目标**：跑通“给主题 → 构思 → 生图 → 评价 → 入库”的最小闭环。

**里程碑 M0**：CLI 命令 `paintai create "秋天的树林"` 能返回一幅画 + 评分 + 改进建议。

**接口定义**：

```python
# painter/agent.py
class PainterAgent:
    def create(self, topic: str, painter_id: str) -> CreationResult:
        """
        输入：主题文本 + 画师 ID
        输出：CreationResult（image_path, critic_score, feedback, gallery_entry_id）
        """
        # 1. Perceive: 解构主题
        perception = self.perceive.analyze(topic)
        # 2. Compose: 生成 prompt
        program = self.compose.plan(topic=topic, perception=perception)
        # 3. Execute: 调用后端生图
        image = self.execute.generate(program)
        # 4. Critic: 评分
        result = self.critic.evaluate(image, topic)
        # 5. Gallery: 入库
        self.gallery.add(image, context=topic, score=result.score)
        return CreationResult(image, result.score, result.feedback)
```

**实现清单**：

| 模块 | 内容 | 验证标准 |
|------|------|---------|
| `painter/perceive.py` | 文本解构（提取主体/场景/情绪/约束） | 给定 10 个主题，人工检查解构完整性 ≥80% |
| `painter/compose.py` | Prompt 模板 + 基础 prompt 生成 | 生成的 prompt 能喂给 DALL·E 3 正常产生图像 |
| `painter/execute.py` | DALL·E 3 API 适配 | 调用返回有效图像 URL |
| `painter/profile.py` | 画师实例模型（painter_id, 配置 JSON） | 能创建/读取/列表画师 |
| `critic/evaluator.py` | VLM 评价（调 GPT-4o/Claude API 做 6 维评分） | 返回结构化 JSON：6 维度评分 + 文字反馈 |
| `critic/vlm_critic.py` | VLM 调用封装 | 能解析 VLM 返回的结构化输出 |
| `memory/gallery.py` | SQLite 作品库（图片路径 + 元数据存储） | 作品能入库/查询/按 painter_id 隔离 |
| `backend/dalle.py` | DALL·E 3 API 适配 | end-to-end 生图成功 |
| `config/settings.py` | API key、生成参数等配置 | 配置文件能被正确加载 |

**依赖的第三方模型/工具**：
- OpenAI API（GPT-4o 做 VLM Critic + DALL·E 3 做生成）
- SQLite（作品库）
- Python 3.11+ + httpx + Pillow + pydantic

---

### 5.2 Phase 1：课程系统（2–3 周）

**实施进度（2026-11-03）**：M1 的默认 L1–L2、Spec 校验、三次练习过关、CLI 有界自动推进及网页学习进度已实现，使用说明见 [LEARNING.md](LEARNING.md)。Phase 2a 当前先提供本地文本 / 技能匹配和 Critic 建议注入基线，向量检索、best-worst 蒸馏及 A/B 实验仍待实现。

**目标**：画师开始“系统学习”。从默认模板（L1–L6 简化版）出发，跑通关卡推进逻辑。

**里程碑 M1**：`paintai train --painter painter_01 --spec default_curriculum` 启动训练后，画师按关卡顺序逐一完成训练任务，Critic 评估通过后自动推进。

**新增接口**：

```python
# curriculum/engine.py
class CurriculumEngine:
    def load_spec(self, painter_id: str) -> CurriculumSpec:
        """加载画师自己的课程规格（自定义或默认模板）"""
    
    def get_current_lesson(self, painter_id: str) -> Lesson:
        """获取当前应执行的训练关卡"""
    
    def submit_result(self, painter_id: str, lesson_id: str, 
                      results: list[CreationResult]) -> LessonResult:
        """
        提交关卡训练结果 → 评估是否通过 → 更新进度
        返回：LessonResult（passed, avg_score, next_lesson）
        """

# curriculum/spec.py
class CurriculumSpec:
    """课程规格 schema"""
    painter_id: str
    title: str
    stages: list[Stage]  # 每个 Stage 内含 lessons
    
class Stage:
    id: str           # 稳定 ID，如 "ink_brush"
    name: str         # 名称，如 "笔墨基础"
    goals: list[str]  # 阶段目标
    lessons: list[Lesson]

class Lesson:
    id: str           # 稳定 ID，如 "ink_brush_l1"
    task: str         # 训练任务描述
    pass_score: float # 通过阈值（如 6.5）
    skills: list[str] # 目标技能标签

# curriculum/task_generator.py
class TaskGenerator:
    def generate_tasks(self, lesson: Lesson, count: int = 3) -> list[Task]:
        """为给定关卡生成 K 个具体训练任务（主题 + 约束）"""
```

**实现清单**：

| 模块 | 内容 | 验证标准 |
|------|------|---------|
| `curriculum/spec.py` | Spec schema 定义 + YAML 解析 + 校验 | 非法 Spec（ID 重复/依赖断裂/阈值出界）被拒绝并给出诊断 |
| `curriculum/engine.py` | 加载 Spec → 推进关卡 → 评估通过 → 解锁下一关 | 端到端：默认 L1 的两个关卡全部通过后，自动进入 L2 |
| `curriculum/templates/default_curriculum.yaml` | L1–L2 的关卡定义（每个阶段 3–4 关） | YAML 通过 spec 校验 |
| `curriculum/task_generator.py` | 按关卡 skill 目标生成训练任务 | 生成的 3 个任务覆盖当前关卡的全部 skill |
| `painter/profile.py` (扩展) | 增加课程进度字段（current_stage, current_lesson, completed） | 进度按 stage_id/lesson_id 记录，改版不乱 |

**依赖**：Phase 0 全部就绪 + PyYAML + cerberus/jsonschema（Spec 校验）

---

### 5.3 Phase 2：经验记忆 + 画师存档（3–4 周）

**目标**：画师开始从错误中学习；画师可以被导出、导入、派生。

**Phase 2a：经验记忆**

**里程碑 M2a**：画师在持续训练中，Memory 自动提取教训，后续创作时检索相似场景的经验并注入上下文。

**新增接口**：

```python
# memory/store.py
class ExperienceStore:
    def add_experience(self, painter_id: str, entry: ExperienceEntry):
        """创作后存入结构化经验"""
    
    def search_similar(self, painter_id: str, topic: str, 
                       skills: list[str], k: int = 5) -> list[ExperienceEntry]:
        """创作前检索相似场景的历史经验（按语义 + 技能标签）"""

# memory/evolution.py
class EvolutionLoop:
    def run_cycle(self, painter_id: str):
        """
        单次进化循环：
        1. 从 Gallery 取最近一批作品
        2. 对每一件：提取 Critic 反馈 + 成功/失败轨迹
        3. 蒸馏经验 → 写入 Memory
        4. 触发经验注入策略更新（当前版本用 RAG 注入）
        """
```

**经验条目结构**（来自架构方案 v1.1）：

```python
class ExperienceEntry:
    id: str                    # exp_042
    type: str                  # success | failure | insight
    context: dict              # stage, topic, target_style
    trajectory: dict           # composition_plan, prompt, references, generations[]
    distilled_lesson: dict     # what_worked, what_failed, rule
```

**实现清单**：

| 模块 | 内容 | 验证标准 |
|------|------|---------|
| `memory/store.py` | SQLite + embeddings 混合存储（经验条目 + 向量索引） | 写入 100 条经验后检索 top-5，相关性人工评估 |
| `memory/evolution.py` | 创作后触发经验提取：对比 best-worst → 蒸馏 rule | 连续 10 轮创作后，可检索到新增的 distilled_lesson |
| `painter/compose.py` (扩展) | 创作时从 Memory RAG 检索，注入到 Compose 上下文 | A/B 测试：有经验注入 vs 无经验注入，Critic 评分提升 |
| `retrieval/embeddings.py` | text-embedding-3-small 生成嵌入 | 向量维度 1536，检索耗时 < 100ms |
| `retrieval/search.py` | ChromaDB 向量搜索 + 技能标签过滤 | 混合检索返回正确排序 |

**依赖**：Phase 1 全部就绪 + ChromaDB + OpenAI Embeddings API

**Phase 2b：画师存档**

**里程碑 M2b**：`paintai export` 和 `paintai import` 正常工作，full/pointer 两种粒度。

**新增接口**：

```python
# archive/export.py
def export_painter(painter_id: str, granularity: str, output_path: str):
    """
    granularity = "full" | "pointer"
    full: profile.json + spec + progress + memory/ + gallery/ → ZIP
    pointer: profile.json + spec + progress + refs[] → JSON
    """

# archive/import.py
def import_painter(archive_path: str, as_id: str) -> PainterInstance:
    """导入存档，含 schema 版本迁移"""

# archive/fork.py
def fork_painter(source_id: str, new_id: str, 
                 inherit_progress: bool, inherit_memory: bool,
                 new_spec_path: str = None):
    """派生新画师：可选继承/重置进度和记忆，可选换课程"""
```

**实现清单**：

| 模块 | 内容 | 验证标准 |
|------|------|---------|
| `archive/export.py` | full/pointer 导出 | full 导出后，在新环境 import 能直接开始创作；pointer 导出后，在同环境 import 能看到原作品 |
| `archive/import.py` | 导入 + schema 版本迁移 | 旧版存档（如有）导入后正常使用 |
| `archive/fork.py` | fork 派生 | 派生后新画师有自己的 profile/spec/progress，不与源画师冲突 |
| 断链处理 | pointer import 时源不可达 → 标 missing | 作品区显式标记 missing，不静默丢图 |

**依赖**：Phase 1 全部就绪 + Phase 2a 基本就绪 + zipfile（Python 标准库）

---

### 5.4 完整依赖清单

| 类别 | 工具/模型 | Phase | 用途 |
|------|----------|-------|------|
| **LLM/VLM** | GPT-4o 或 Claude | P0–P2 | Critic 评价、Perceive 解构、Compose 构思 |
| **生图后端** | DALL·E 3 | P0–P2 | MVP 主力生成器 |
| **生图后端** | SDXL / ComfyUI | P2+ | 风格化实验、LoRA 微调 |
| **嵌入模型** | text-embedding-3-small 或 OpenCLIP | P2 | 经验记忆的向量检索 |
| **向量数据库** | ChromaDB | P2 | 经验/参考的矢量搜索 |
| **存储** | SQLite | P0–P2 | 作品库 + 经验记忆 |
| **配置** | PyYAML | P1 | 课程 Spec 解析 |
| **HTTP** | httpx | P0 | API 调用 |
| **图像** | Pillow | P0 | 图像处理 |
| **数据校验** | pydantic | P0 | 数据模型 |
| **Web** | Next.js + Tailwind | P2+ | Web 界面（Phase 2+ 选做） |
| **版本管理** | Python json 库内置 | P2b | schema_version 写入与校验 |

---

## 六、技术风险矩阵

### 6.1 已有论文验证过的（可复用，风险低）

| 风险点 | 验证来源 | 验证内容 | Paintai 复用方式 |
|--------|---------|---------|----------------|
| Agent 编排优于裸生成器 | GenEvolve (KScore +0.27), GenAgent (+23.6%), GEMS (6B > 大模型) | 多篇论文独立验证 Agent 编排带来显著提升 | 直接复用 Agent 架构范式 |
| VLM 评判与人类评判一致 | GenEvolve (KScore judge), GenAgent (MLLM reward model) | VLM 可替代人类做审美评判 | Critic 的 VLM 评价方式可靠 |
| 经验蒸馏有效 | GenEvolve (VED 提升 0.02 KScore over baseline) | best-worst 轨迹对比蒸馏提供 token 级监督 | Memory 的经验蒸馏机制 |
| 多轮反思提升质量 | GenAgent (测试时扩展持续提升), VisionCreator-R1 | 多轮反思 + 修正确实让图像更好 | Execute → Reflect → Re-execute 循环 |
| 搜索增强有效 | Gen-Searcher (+16 KnowGen), Qwen-Image-Agent | 外部知识搜索显著提升事实准确性 | Reference Retriever 搜索引入 |
| 课程学习加速收敛 | PaintBot (课程学习), RSIgen (recursive curriculum) | 从简单到困难的课程设计提升学习效率 | Curriculum Engine 的阶段性设计 |
| 双奖励稳定训练 | Gen-Searcher (text+image), ToolArtist (intent+quality), GenAgent (pointwise+pairwise) | 单一图像 reward 噪声大，双奖励更稳定 | Critic 的多维度评分 + 训练时的复合 reward |
| 笔触可微渲染 | Emergence of Painting (differentiable rasterizer), Learning to Paint (neural renderer) | 笔触参数可通过梯度优化 | Phase 3+ 的笔触级控制基础 |

### 6.2 Paintai 独有的（需从零探索，风险中高）

| 风险点 | 风险等级 | 说明 | 缓解策略 |
|--------|---------|------|---------|
| **纵向成长曲线** | 🔴 高 | 没有论文验证“同一主题反复画时，经验蒸馏能否让 Critic 评分单调提升”。现有工作只看跨主题的聚合指标 | Phase 2 早期做 A/B 实验：同一主题连续创作 10 轮，看 Critic 评分趋势；若 5 轮后趋于平坦，需调整经验蒸馏权重或引入 curriculum 压力 |
| **课程结构的效用** | 🟡 中 | 从“基础元素”到“自由创作”的分阶段课程对 VLM-based Agent 的创作能力提升是否显著——未见消融实验 | Phase 1 设计消融：随机顺序 vs 课程顺序，对比 Critic 评分差异；若差异不显著，重新审视课程定义粒度 |
| **Critic 的“风格一致性”维度** | 🟡 中 | 已有论文的 reward 是通用美学评判，没有对“风格一致性”的专项评估 | 从 GenEvolve 的 5 维经验中提取风格相关 slot，针对性设计风格一致性 prompt |
| **画师实例隔离的存储成本** | 🟡 中 | 多个画师实例共享同一 Agent 实现和执行层，但数据层严格隔离；full 存档体积随作品数线性增长 | Phase 2b 先做 pointer 模式验证隔离正确性；full 模式做压缩 + 差分存储优化 |
| **自定义课程的校验复杂度** | 🟢 低 | Spec 校验（ID 唯一/依赖闭环/阈值合法）是标准 schema 校验问题 | Phase 1 用 pydantic + 自定义 validator 解决 |
| **pointer 存档的断链语义** | 🟡 中 | 跨环境 pointer 不可达时，用户体验如何设计——是静默 missing 还是主动提醒 | Phase 2b 定义断链状态机：missing → 尝试重连（N 次） → 永久 missing → 通知用户；可降级为 full 重导出 |
| **经验检索的相关性衰减** | 🟡 中 | 画师风格变化后，早期经验可能不再相关；已有论文不处理经验生命周期 | Phase 2 的经验条目带时间戳 + 技能标签；检索时衰减旧经验的权重；定期清理“已内化的基础经验” |

### 6.3 关键依赖风险

| 依赖 | 风险 | 缓解 |
|------|------|------|
| GPT-4o / Claude API 可用性与成本 | 中等——API 可能变更、成本随规模上升 | 设计 Critic 的缓存层（相同配置的评估结果可复用）；Phase 2+ 评估开源 VLM 替代（Qwen-VL 系列） |
| DALL·E 3 的风格控制能力有限 | 中等——DALL·E 在风格一致性和精细控制上不如 SDXL | Phase 2+ 引入 SDXL/ComfyUI 作为风格化后端；MVP 阶段接受 DALL·E 的风格限制 |
| ChromaDB 在大规模经验上的检索延迟 | 低——MVP 阶段经验量级不大（<1000 条） | 延迟不可接受时切换到 Qdrant 或 pgvector |

---

## 七、后续工作

本文档基于 2026 年 11 月 3 日的最新论文调研编写。以下事项需要在执行中持续跟踪：

1. **GenEvolve 代码与模型权重**（GitHub: MeiGen-AI/GenEvolve, HuggingFace 已开源）——Phase 0 结束后评估是否直接复用其 VED 实现
2. **MemoGen 完整论文**——当前仅有 arXiv 摘要，发表后评估经验记忆假设的验证强度
3. **GPT-4o vs Claude 作为 Critic 的对比评测**——Phase 0 期间做小样本对比，选择评分稳定性更好的一方
4. **默认课程模板的关卡设计**——用户决策后填充 L1–L6 的具体关卡

---

## 附录：论文索引

| 论文 | arXiv | 时间 | 开源 |
|------|-------|------|------|
| GenEvolve | 2605.21605 | 2026.05 | GitHub + HuggingFace |
| GenAgent | 2601.18543 | 2026.01 | 代码即将开源 |
| Gen-Searcher | 2603.28767 | 2026.03 | 全开源 |
| ToolArtist | 2608.04436 | 2026.08 | 数据 + 基础设施开源 |
| GEMS | — | 2026 | — |
| Qwen-Image-Agent | — | 2026 | — |
| RSIgen | — | 2026.10 | — |
| MemoGen | 2606.03243 | 2026.06 | — |
| OctoT2I | 2606.01803 | 2026.06 | — |
| VisionCreator-R1 | — | 2026 | — |
| CRAFT | — | 2026 | — |
| APT | 2610.18097 | 2026.10 | — |
| Emergence of Painting Ability | 2501.04966 | 2025.01 | — |
| PaintBot | 1904.02201 | 2019 | — |
| Learning to Paint (DDPG) | 1903.04411 | 2019 | — |
| Compositional Neural Painter | 2309.03504 | 2023 | GitHub |
| Stroke-GAN Painter | — | 2022 | — |
