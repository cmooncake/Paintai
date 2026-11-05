# 本地二次元生图

## SDXL 候选底模对照

新增可独立选择的 `configs/local-animagine.toml`，使用作者发布的
[Animagine XL 4.0 Opt](https://huggingface.co/cagliostrolab/animagine-xl-4.0)。
它是动漫 SDXL 底模，许可证 Open RAIL++-M；不是已经学习小笑参考集的 LoRA。
下载脚本 `scripts/setup-local-animagine.py` 固定版本
`2b7c1b397761bf5bd3cc42e5b39ec99314a75a96`，校验 6,938,350,040 字节文件的 SHA256
`6327eca98bfb6538dd7a4edce22484a1bbc57a8cff6b11d075d40da1afb847ac`。
分段下载可续传，校验通过后才安装正式文件。

```powershell
.venv/Scripts/python.exe scripts/setup-local-animagine.py
.venv/Scripts/python.exe scripts/compare-anime-bases.py --control data/lora/face-focus-control-v2 --output data/base-comparison/animagine-512 --resolution 512
.venv/Scripts/python.exe scripts/compare-anime-bases.py --control data/lora/face-focus-control-v2 --output data/base-comparison/animagine-1024 --resolution 1024
.venv/Scripts/python.exe scripts/compare-anime-bases.py --control data/lora/face-focus-control-v2 --output data/base-comparison/animagine-recommended --resolution 1024 --recommended
.venv/Scripts/python.exe scripts/compare-anime-bases.py --control data/lora/face-focus-control-v2 --output data/base-comparison/counterfeit-recommended --resolution 512 --recommended --counterfeit-control
```

512 对照复用哈希验证过的无 LoRA 旧底模图，保持正负提示词、种子 42、20 步、CFG 7、
Euler a、discrete、CLIP skip 2 一致；1024 补充组仅再改变分辨率，不能作为纯底模消融。
每组保存实际调用、运行日志、图像哈希和耗时。失败也写入报告，不覆盖旧实验。
跨 SD1.5/SDXL 架构的相同种子不是等价噪声或相同构图的保证，单种子不能证明稳定优势。

`--recommended` 是另外一组配置适配测试：保留题目、属性约束和种子，改用明确的单人标签、
精简正向提示词及质量标签，并采用 CFG 5、28 步、1024 分辨率。
该组改变了多个变量，报告明确标记为自适应后续测试，不能作为纯底模收益。

独立配置则采用作者推荐的 1024、28 步、CFG 5，与上述严格对照参数不同。
该配置的 `prompt_suffix` 会把质量标签加入最终计划提示词，并保存在作品记录中；
默认配置的后缀为空。它不会自动修正主体数量或将所有自然语言转为模型训练标签。
可用 `scripts/start-web.ps1 -Animagine -StartModels` 启动网页和本地服务
（先用 `scripts/stop-web.ps1` 停止占用端口的旧服务）。网页若存有偏好，需要恢复启动配置才能使用此配置。
该配置未加载原来的 SD1.5 IP-Adapter；已有 SD1.5 LoRA 也不能直接沿用。
原来的 `local-full.toml` 保留，便于回退和继续复现历史实验。

### 本机换底模实测（2026-11-05）

完整权重已通过 SHA256 校验，并用本机 CPU 实际生成四张图，均无 LoRA 或 IP-Adapter。

| 组别 | 参数 | 耗时 | 观察 |
| --- | --- | --- | --- |
| Animagine 直接沿用旧参数 | 512、20 步、CFG 7、原提示词 | 204 秒 | 眼睛畸形、背景色块，失败 |
| Animagine 仅提升分辨率 | 1024、20 步、CFG 7、原提示词 | 806 秒 | 生成六个头像、眼睛畸形，失败 |
| Animagine 适配配置 | 1024、28 步、CFG 5、单人标签及质量标签 | 1235 秒 | 单人恢复正常、暖琥珀色眼睛、无腮红排线，但仍粗线和平涂 |
| Counterfeit 对应提示词控制组 | 512、28 步、CFG 5、与上一行完全相同的正负提示词 | 149 秒 | 单人正常，眼睛偏黄绿、存在腮红排线与金色纽扣 |

最终两组题目、提示词、人物属性约束、种子 42、采样器、CFG、步数、CLIP skip、运行程序一致，
底模和原生分辨率不同。不能把全部差异归为模型本身，也不能据单种子断言稳定改善。
两者都没有体现参考作品中的细彩线、透明虹膜层次和柔和环境光融合。新图未清楚露出纽扣，
不能据此宣称白色纽扣约束通过；头顶也被裁切。

结论：部分属性服从度改善，整体目标画风验收未通过；新模型保留为可选配置，默认仍用 Counterfeit。
没有为 SDXL 训练新的 LoRA。检查方法为助手直接看图的非盲复核，不提供相似度百分比。
`data/base-comparison/acceptance.json` 保存 22 项通过的运行/对照校验和独立的画风失败结论；
所有失败图、最终图及实际调用日志均保留。相关 Python 回归测试 20 项通过，启动脚本语法检查通过。

## 默认 SD1.5 配置

网页的本地启动配置 `configs/local-full.toml` 使用 Counterfeit V3.0 fp16，512×512、20 步、Euler ancestral、CFG 7、CLIP skip 2，附带通用负面提示词。构思和评价继续支持本机文本与视觉服务。原 SD-Turbo 快速配置保留在 `configs/local-image.toml`。

模型来源：[作者 gsdf 的 Counterfeit V3.0](https://huggingface.co/gsdf/Counterfeit-V3.0)，许可证 CreativeML Open RAIL-M。下载约 2.13 GB；安装脚本锁定源版本并验证 SHA256，不加载 pickle 权重或远程 Python 代码。

```powershell
.\.venv\Scripts\python.exe scripts/setup-local-anime.py
.\scripts\start-web.ps1 -StartModels
```

首次使用须已有本地生图运行时（`scripts/setup-local-image.ps1` 安装）。若网页保存过云端偏好，请在模型设置点击「恢复启动配置」，使三个角色回到本地。无需 API 密钥。

CPU 生图比 SD-Turbo 慢，先使用 512 分辨率测试；偏二次元画风不保证人体与手部正确。画师风格和题目仍会影响画面，不会自动改写已有画师的成长目标。

## 参考图直接参与生成

运行 `.venv/Scripts/python.exe scripts/setup-reference-adapter.py` 安装官方 h94/IP-Adapter 固定版本的 Plus SD1.5 适配器和 ViT-H 图像编码器（合计约 2.63 GB，safetensors，逐文件 SHA256 校验）。本地配置新增可选 `ip_adapter_path`、`clip_vision_path`；旧文本生图不受影响。

网页「成长实验」选择「参考图生图：仅文字 / 文字 + 图片」，选择当前档案中的一张参考与图片强度，再预览实际图片。当前引擎单次只接收一张 IP-Adapter 参考；其他档案图片仍可用于事后的多图对照评价。默认强度 0.6，不代表纯风格强度，可能同时迁移人物内容。

基线和学习组保留同一风格档案的文字引导，关闭两组旧记忆及长期目标，每对只构思一次并复用最终提示词、人物约束和种子。学习组另外传入 `--ip-adapter`、`--clip_vision`、`--ip-adapter-image` 与 `--ip-adapter-strength`。这是图像条件消融实验，不是模型权重训练。实验记录冻结参考图与权重哈希，作品记录 `reference_conditioning`，每次运行目录保存 `invocation.json` 与日志，便于核对真实图片输入。

首次需确认运行时支持 IP-Adapter Plus；本项目已安装的版本 `3be2378` 暴露相应参数。依据：[stable-diffusion.cpp IP-Adapter 文档](https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/ip_adapter.md)。适配器可能复制发色、服装或构图，因此原题服从度与参考风格接近度必须分开检查。

### 本机图片条件对照（2026-11-04）

实验 `e511289d0c644377802df644affc5de5`，小笑，原棕发琥珀眼象牙白衬衫头像题目，种子 42，第一张冻结参考，IP-Adapter Plus 强度 0.6。基线作品 `85e21c79e82648b29595ed46a15c28d7` 与图片组 `3449ccaf4a9f404e9117ff29e49933c1` 的最终提示词、模型配置、人物约束均一致；日志确认 `16 image tokens, strength 0.60`，运行时图片像素与保存作品一致。基线评价第一次截断，原图按原配置重评后继续；失败历史保留。

原题评价总分 8.667 → 5.667。图片组迁移了参考的发饰、浅色头发、颈饰和明亮黄绿背景，未保住棕发、简洁白衬衫及青色背景。两张作品的十个多图评价分项全部完成，但有内容混淆被标记，不能给出可信相似度均分。本轮证明图片条件链路有效，不证明风格迁移成功；单对不用于判断稳定进步。实验输出未进入训练记忆。验收保存在 `data/image-reference-acceptance.json`，网页实验记录含独立看图复核。

## 本机实测（2026-11-03）

- 画师「小笑」，作品 `22096d91e8234e0288a06ec9f1ad59ca`：蓝发人物与樱花背景，512×512。
- 本地 Qwen 构思 → Counterfeit V3 生图 → 本地 Qwen3-VL 视觉评价 → 一条创作记忆，状态 `complete`，三个角色均非模拟。
- 完整流程约 116 秒；实际图片 SHA256 与评价记录一致。
- 样图具有明确二次元线稿和上色；夕阳边缘光表现较弱。本地评价器给出全维度 9 分，偏乐观，不能当作客观质量或学习进步证明。
