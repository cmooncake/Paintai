# 小笑的 CPU LoRA 试训

这条流程会对现有 Counterfeit V3（SD1.5）执行真实的反向传播，只更新 U-Net 注意力层的 LoRA 参数；底模、VAE 和文本编码器冻结。它与提示词学习、IP-Adapter 参考图引导是不同的机制。

## 范围

首轮仅验证训练、导出、重载和本地推理兼容性：3 张真实参考头像、裁去白边、逐图内容标注、256 分辨率、rank/alpha 4、学习率 0.0001、batch 1、12 步、CPU float32、随机种子 42。文本编码使用 clip_skip=2。每张参考图循环使用 4 次，重复不算新增样本。生成作品不进入参考训练集。

3 张相似构图的头像没有独立验证集，不能证明泛化的画风学习。低分辨率和极少步数尤其不能证明已经学会眼睛、线条和上色细节。损失随随机时间步波动，不当作风格相似度。正式训练需要扩充内容和构图多样性，并留出未参与训练的参考作品及固定测试题。

首轮 12 步通过后，另起一次 96 步试训（从同一底模开始，不是接续 12 步）：约 149 秒完成训练，797,184 个参数参与更新；保存后重载预测的最大误差为 0。产物位于 `data/lora/xiaoxiao-cpu-96/`。这些是运行验收数据，不是画风质量评分。

实际本地推理已确认 384/384 个 LoRA 张量应用成功，同题同种子的对照图保存在 `data/lora/comparison-96/`。非盲人工观察：眼形、脸部轮廓、刘海和高光发生变化，但头发的圆块状高光、明显腮红与目标参考仍有差距；还新增了题目未要求的青色领结。本轮结论为训练与推理链路通过，尚未证明目标画风学习成功。逐项验收见该目录的 `acceptance.json`。试训权重未自动启用为画师默认设置。

## 运行

在仓库根目录 PowerShell 中：

```powershell
.\.venv\Scripts\python.exe -m venv data/lora-env
data/lora-env/Scripts/python.exe -m pip install -r scripts/requirements-lora.txt
data/lora-env/Scripts/python.exe scripts/train-style-lora.py --manifest data/lora/xiaoxiao-manifest.json --output data/lora/xiaoxiao-cpu-smoke --steps 12
```

96 步对照复现（使用新的输出目录避免覆盖）：

```powershell
data/lora-env/Scripts/python.exe scripts/train-style-lora.py --manifest data/lora/xiaoxiao-manifest.json --output data/lora/xiaoxiao-cpu-96-rerun --steps 96
.\.venv\Scripts\python.exe scripts/compare-style-lora.py --training data/lora/xiaoxiao-cpu-96-rerun --output data/lora/comparison-96-rerun
```

对照使用同一个人物头像题目、seed 42、同一提示词及人物属性保护、512×512、20 步。两边都含 `xiaoxiao_style` 触发词，唯一差异是是否加载强度 1.0 的 LoRA；没有 IP-Adapter。输出保留实际命令、适配器哈希和图片。不写入普通学习记忆，避免把验收作品当成目标画师的训练素材。

manifest 指定底模 SHA256、每张图的路径/SHA256、裁切矩形和英文内容标注。首次运行仅从官方 SD1.5 仓库下载配置及 tokenizer，不下载第二份底模，不上传图片。实际配置仓库提交号记录在报告中。输出目录必须不存在，防止覆盖已训练权重。CPU 线程默认限制为 6。

## 输出与验收

- `report.json`：来源、参数、逐步损失/梯度范数、参数变化量、重载误差、产物 SHA256；异常保留失败状态。
- `adapter-peft.safetensors`：PEFT 原始参数。
- `pytorch_lora_weights.safetensors`：Diffusers 可加载的 LoRA。
- `xiaoxiao-smoke.safetensors`：包含 alpha 的 Kohya 命名权重，供 stable-diffusion.cpp 兼容性验收。

重载验收会重新创建适配器、从磁盘读取权重，比较同一输入的输出；另记录启用/禁用适配器的预测差异。原模型文件不改动，也不会自动将试训权重设成画师默认模型。

参考：[Diffusers LoRA 训练](https://huggingface.co/docs/diffusers/en/training/lora)、[单文件底模加载](https://huggingface.co/docs/diffusers/v0.37.1/en/api/loaders/single_file)。

## 扩充参考作品后的续训

从官网链接的 [Pixiv 作者账号](https://www.pixiv.net/users/795196) 取得 18 张公开作品，保存作者 ID、原帖、原图地址和 SHA256。人工排除 4 张多人构图、宣传排版或主体过小的图；新增 10 张进入训练，4 张预先留出验证。加上原来的 3 张头像，共 13 张训练图、4 张验证图。完整清单及排除理由位于 `data/lora/expanded/manifest.json`，重复筛查记录位于 `split-audit.json`。

同角色的近似作品归入同一组；验证图不出现在当前训练图及祖先 LoRA 的训练历史中。SHA256 交叉检查、感知差异筛查和人工复核相互补充；不把不同裁切算成独立作品。底模自身的原始训练资料不可审计，因此这里只保证这次 LoRA 训练流程的留出，不声称底模从未见过公开作品。

本轮预先固定：从 96 步适配器继续更新 208 步（总计 304 步，AdamW 状态重新初始化），rank 4、学习率 0.0001、batch 1、CPU 6 线程。采用 384 长边的方形/横向/竖向分桶，避免强行把所有原作裁成正方形。13 张训练图每轮打乱顺序，16 轮共 208 步；验证集不决定步数和超参数。

```powershell
data/lora-env/Scripts/python.exe scripts/train-style-lora.py --manifest data/lora/expanded/manifest.json --resume-training data/lora/xiaoxiao-cpu-96 --output data/lora/xiaoxiao-expanded-208 --steps 208 --resolution 384
.\.venv\Scripts\python.exe scripts/compare-style-lora.py --training data/lora/xiaoxiao-expanded-208 --output data/lora/comparison-expanded-208 --reuse-baseline data/lora/comparison-96
.\.venv\Scripts\python.exe scripts/review-lora-holdout.py --manifest data/lora/expanded/manifest.json --image data/lora/comparison-expanded-208/lora.png --output data/lora/expanded/review-expanded.json
```

以上命令的输出目录/文件必须尚不存在，重跑请换新名字。对照沿用同一题目、seed 42 和相同生图设置；复用基线时检查原图哈希、完整模型配置、提示词和人物约束。三版对照为底模、旧 96 步 LoRA、新 304 步 LoRA。

验证分两层：

- 4 张留出图，固定噪声与时间步，测底模、旧 LoRA、新 LoRA 的去噪 MSE；这不是画风相似度，也不作为自动选优依据。
- 视觉模型同时看 3 张预先选定的女性角色验证图和待评成图，分项比较线条、上色、光影、眼睛、脸部。输入不含作者名、版本标签或旧评分。保留原始证据与无效理由，并人工检查；0–4 分是主观等级，不能写成相似度百分比。

新旧图片仍然只有一个题目、一个种子；更接近的个例不代表已经稳定掌握画风。试训适配器不会自动替换网页默认模型。

### 本轮实际结果

208 步续训耗时 410 秒，权重重载误差为 0，本地推理再次确认所有 LoRA 张量应用成功。4 张留出图的平均去噪 MSE：底模 0.19002、旧版 0.18573、新版 0.18299。相较旧版下降约 1.47%，四张图各自都有小幅下降，但这不是画风相似度提升的百分比。手工内容标注仍有粗略之处（例如竹林作品的服装描述），也限制了绝对误差的解释。

视觉结论不随损失下降而自动变好：新版线条更粗且均匀，头发与皮肤色块更平，高光更简化；相比留出的原作，细腻过渡、反射色和虹膜层次依然不足。人物转为正面构图，但栗棕发色变浅、青色领结仍违背简洁衬衫要求。本轮未证明画风更接近，未启用为默认模型。

本地视觉模型对新版五项均返回低置信度，全部保留原始评价并标为不计入有效均分，不能给出可靠的新旧相似度增幅。验收记录为 `data/lora/expanded/acceptance.json`；旧版图片为 `data/lora/comparison-96/lora.png`，新版为 `data/lora/comparison-expanded-208/lora.png`。不得将这个单题结果解释为稳定泛化能力，也不据此宣称真实画风学习已经成功。

## 退化原因排查与针对性调整

已核实的事实：3 张平涂头像各经历 96/3 + 208/13 = 48 次训练，新加入的 10 张插画各经历 16 次；前者占累计 304 步的 47.4%。头像脸部占图很大，而全幅插画缩成 256×384 后，许多脸部细节很小。训练目标混合了简化角色头像与细腻插画，二者并非一致的渲染风格。

实现检查：Kohya 导出与 PEFT 参数经官方转换后逐张量完全一致，alpha 均为 4；训练标注长度为 35–46 个 CLIP token，没有截断。推理长提示词的触发词位于后段，但当前 stable-diffusion.cpp 版本 `3be2378` 的 `get_learned_condition` 使用 `allow_overflow_expand=true` 并拼接分块，不应把超过 77 token 直接认定为触发词丢失。源码证据保存在 `data/lora/diagnosis/conditioner.hpp`，上游为 [当前运行时源码](https://github.com/leejet/stable-diffusion.cpp/blob/3be2378/src/conditioning/conditioner.hpp)。

两组预先固定的实验分别检验：

- A：相同 13 张训练图、208 步、384 分辨率、rank 4、学习率 1e-4，仅取消旧头像 LoRA 初始化。它用于检验初始化/历史训练的影响。
- B：从底模新建 LoRA，移除 3 张简化头像；使用同一批 10 张插画经人工检查的人物局部，重写与裁切可见内容相符的标注，512 分辨率、200 步、rank 4、学习率 5e-5。它是组合修正，不能单独归因于分辨率、裁切或学习率。

所有成图均使用原题、原提示词、seed 42、512×512、20 步、LoRA 强度 1.0。不得通过降低 LoRA 强度后恢复底模外观，声称训练改善。此处的 4 张旧留出图仍不参与梯度更新，但它们已影响后续设计，因此应称为开发验证集，不能再作为完全未见的最终测试集。不同训练分辨率的去噪 MSE 也不能直接横向比较。

```powershell
data/lora-env/Scripts/python.exe scripts/train-style-lora.py --manifest data/lora/expanded/manifest.json --output data/lora/diagnostic-fresh-208 --steps 208 --resolution 384
data/lora-env/Scripts/python.exe scripts/train-style-lora.py --manifest data/lora/diagnosis/portrait-manifest.json --output data/lora/portrait-fresh-200 --steps 200 --resolution 512 --learning-rate 0.00005
```

运行参数以顶层训练报告及命令为准，输入清单中的旧 `fixed_training_plan` 是历史计划。训练脚本保存当次源码及 SHA256、拒绝超长标注，并限制学习率为有限正数。完整排查计划为 `data/lora/diagnosis/analysis-plan.json`。

### A/B 排查结果

A 取消旧 LoRA 初始化后恢复了一些明暗层次，且额外领结消失；但仍有较粗轮廓与简化上色。这支持旧头像训练对本例偏移有部分影响，不足以证明它是唯一原因。A 的开发验证 MSE 为 0.19002 → 0.18325，与旧续训版接近；画面差异不能由这个误差数值排序。

B 从底模训练 200 步，约 1091 秒；同样加载全部 384 个 LoRA 张量，重载误差为 0。相较旧 304 步退化版，头发高光过渡和面部明暗恢复，线条不再那么生硬。但是与原底模相比，差异仍有限，未证明目标画风有稳定提升；参考作品的细腻色彩过渡和虹膜层次仍然不足，腮红划线偏重。棕色短发、琥珀眼睛和浅青背景保留，白衬衫仍有额外橙色细节与青色布料，人物要求没有全部通过。

B 内部的同条件开发验证 MSE 为 0.17694 → 0.17248，因输入分辨率和部分标注不同，不与 A 横向比较。相同参考图下的本地视觉模型把 A、B 五项全部评为 3/4，理由泛化，因此没有用这一均分宣布风格改善。B 是实验权重，不替换默认模型。

四版同题同种子记录与 17 项验收检查在 `data/lora/diagnosis/acceptance.json`；调整后的图片为 `data/lora/comparison-portrait-fresh/lora.png`，权重为 `data/lora/portrait-fresh-200/xiaoxiao-smoke.safetensors`。本轮结论是修复明显退化，尚未建立真正掌握目标画风的证据。

## 虹膜、面颊与服装专项实验

从 `portrait-fresh-200` 继续 80 步，512 分辨率、学习率 2.5e-5、rank 4。10 张原作的人物裁切保持不变，人工标注眼睛带状区域与面颊矩形：对应去噪误差权重为 4、2，其他区域为 1；重叠区域取最大值，并按权重均值归一化，避免把整体损失增大误当成更强的细节学习。没有修饰原作像素。矩形是近似区域，不是精确虹膜分割；空间加权是待验证的工程假设，不是已证实的画风相似度目标。

训练清单及坐标为 `data/lora/face-focus/manifest.json`，人工检查图为 `region-review-final.jpg`。训练报告同时记录加权与未加权损失、每步使用的图片 ID、权重覆盖面积和实际源码哈希。验证图仍不参与参数更新。

生成采用独立的 `face-clothing-v2` 提示词配置，将风格触发词前置、去掉重复的色彩锚点和额外的强金色虹膜要求，添加虹膜层次、细小高光及轻微面部阴影要求。服装明确为普通日常衬衫、翻领、纯色主体与同色纽扣；负向约束抑制腮红划线、过重腮红、领结、拼色、高领中式服装等。原底模、旧 200 步 LoRA、新增区域训练 LoRA 使用完全相同的提示词、负向约束、seed 42、20 步、512×512，适配器强度均为 1.0。

先前 `face-clothing-v1` 原底模出现中式拼色高领，因此保留为未通过的提示词试验，并在观察新版区域适配器之前统一改成 v2。v1 下旧 LoRA 也有可见改善，不能隐藏这部分旧模型/提示词效果。v2 原底模本身已消除腮红划线并改善衣服主体，这些不是新增训练收益。最终必须对照同配置原底模和旧 LoRA，不能只与历史失败图片比较。

```powershell
data/lora-env/Scripts/python.exe scripts/train-style-lora.py --manifest data/lora/face-focus/manifest.json --resume-training data/lora/portrait-fresh-200 --output data/lora/face-focus-80 --steps 80 --resolution 512 --learning-rate 0.000025
.\.venv\Scripts\python.exe scripts/compare-style-lora.py --training data/lora/portrait-fresh-200 --output data/lora/face-focus-control-v2 --profile face-clothing-v2
.\.venv\Scripts\python.exe scripts/compare-style-lora.py --training data/lora/face-focus-80 --output data/lora/face-focus-result --profile face-clothing-v2 --reuse-baseline data/lora/face-focus-control-v2
```

重跑必须使用新的输出目录。此配置目前用于受控实验，不会悄悄改变网页所有创作的默认提示词，也没有把实验权重自动启用。

### 专项实验结果

区域训练完成 80 步，总计 280 步，每张训练图新增 8 次更新；参数真实改变，重载误差为 0，推理加载全部 LoRA 张量。19 项运行验收通过，但整体目标画风验收未通过，详见 `data/lora/face-focus/acceptance.json`。

在相同 v2 提示词、种子和生成参数下：旧、新 LoRA 的虹膜比原底模有更多可见色调与高光，但新增 80 步的变化很小，瞳色仍偏橄榄金而非明确的暖琥珀。原底模已没有腮红划线，两版 LoRA 却仍有划线，说明区域加权没有解决该问题。v2 原底模本身改善了衬衫主体，旧 LoRA 仍有金色纽扣，新 LoRA 纽扣更中性、领口更朴素，这是有限的服装改善，不能解释为整体学会目标画风。

同参考图的本地视觉复核中，原底模的线条/上色/光影/眼睛/脸部分别为 3/3/3/2/3，新版为 2/3/2/2/3；虹膜等级没有提升，其他分项也不支持整体胜出。评级粗略且证据泛化，不换算为相似度百分比。区域损失强调眼睛区域的全部像素，无法保证只学层次而不学颜色；这是本轮仍未解决的内容与画法耦合问题。实验权重未设为默认。

## Animagine SDXL 小规模试训

`scripts/train-style-lora.py --architecture sdxl` 支持独立的 SDXL UNet LoRA。
双 CLIP 编码器取倒数第二层并拼接，第二编码器提供 pooled embedding，额外传入原图/裁切/目标尺寸条件。
实现依据本机 Diffusers 0.37.1 的[官方 SDXL LoRA 示例](https://github.com/huggingface/diffusers/blob/v0.37.1/examples/text_to_image/train_text_to_image_lora_sdxl.py)。
训练冻结底模、VAE 和两个文本编码器，缓存特征后释放编码器与 VAE，使用梯度检查点控制内存。
默认架构仍为 SD1.5；清单架构或续训适配器架构不匹配时在加载前拒绝。

本轮清单 `data/lora/animagine-pilot/manifest.json` 固定 10 张既有精细插画肖像裁图、4 张开发验证作品。
从已校验的 Animagine XL 4.0 Opt 重新初始化 rank 4 LoRA，不继承旧 SD1.5 权重。
40 步、学习率 5e-5、训练种子 42、512 裁图，每张训练图 4 次更新；眼部带状区域权重 2，其余 1。
全局画面仍参与损失，以保留线条和光影信息。此权重不是直接的画风距离损失，眼部强调也可能学习颜色等内容。
本轮不使用独立触发词，风格适配通过启用 LoRA 生效；人物内容用描述性标签说明。

```powershell
data/lora-env/Scripts/python.exe scripts/train-style-lora.py --architecture sdxl --manifest data/lora/animagine-pilot/manifest.json --output data/lora/animagine-pilot-40 --steps 40 --resolution 512 --threads 6 --learning-rate 0.00005
.venv/Scripts/python.exe scripts/compare-sdxl-lora.py --training data/lora/animagine-pilot-40 --baseline data/base-comparison/animagine-recommended --output data/lora/animagine-comparison-40
```

重跑需新的输出目录。对照脚本验证底模、运行程序、基线图片哈希以及实际调用；复用已验证的无适配器基线，
以相同正负提示词、种子 42、1024、28 步、CFG 5、Euler a、CLIP skip 2 生成 LoRA 强度 1 的图片。
必须从原生日志确认全部适配器张量应用。训练导出 PEFT、Diffusers 和原生运行时兼容权重，并保存实际执行源码及重载验证。
训练分辨率低于 SDXL 原生分辨率，单次 CPU 试训与单种子对照不能证明稳定学会画风；开发验证集不是未使用过的最终测试集。

### 本轮结果（2026-11-05）

40 步完成，训练更新耗时 407.99 秒，训练参数 5,806,080 个，最大参数变化 0.001765；
重载最大误差为 0，开启/关闭适配器的探针输出平均差异为 0.06310。
4 张开发验证作品的固定噪声去噪 MSE 从 0.26033 降到 0.21662，此指标不表示画风相似度。
原生运行时确认全部 1680/1680 个 LoRA 张量应用；1024 对照图生成约 1136 秒。

实际看图结论：虹膜增加了少量反光细节，但仍以橙金色块为主；头发、脸部与衣服的深色粗轮廓基本保留，
皮肤/颈部的平涂阴影、块状头发高光也没有明显变柔和。棕色短发、暖琥珀眼、象牙白衬衫和纯青色背景保住；
两图都没有清楚露出纽扣，不能判定纽扣颜色。整体目标画风验收未通过，权重保留为实验产物，不默认启用。

`data/lora/animagine-pilot/acceptance.json` 包含 12 项训练核验、22 项对照核验与独立的画风未通过结论。
实际图片在 `data/lora/animagine-comparison-40/baseline.png` 和 `lora.png`，模型权重在
`data/lora/animagine-pilot-40/xiaoxiao-smoke.safetensors`（沿用导出文件名，但本文件是 SDXL 适配器）。
比较脚本已修复 CFG `5` 与 `5.0` 字符串误判；本轮原错误报告保存在 `comparison-original-failure.json`，
修正后通过 `--verify-existing` 校验原图和原调用，没有重新生图或挑选样本。
相关架构、训练数据隔离与 CFG 比较回归测试共 13 项通过。

## 莉莉艾固定人物：768 多视图诊断实验

上一轮权重确实更新、重载且在原生推理中生效，因此不能把提升弱归因于“没有加载 LoRA”。
更直接的限制是：40 次更新、10 张原作，512 肖像中的虹膜只占少量像素；眼部矩形加权会同时学习颜色和轮廓，
不是虹膜层次或画风相似度损失。rank 4、注意力层适配与内容标签也可能限制表达能力，但本轮没有单独验证这些假设。

新清单 `data/lora/animagine-detail/manifest.json` 保持同 10 张训练原作和 4 张开发验证作品，
每张训练图提供原肖像和脸部近景两个视图，改为 768 分辨率；20 个视图不等于 20 张独立作品。
从底模重新初始化，仍为 40 步、rank 4、学习率 5e-5、种子 42、眼部权重 2。
每视图更新两次，即每原作四次，与旧方案相同；近景标注只描述可见内容。
训练没有使用莉莉艾图片。原肖像保留整体光影与衣服线条，近景提高虹膜与面部线条的空间占比。
部分近景原始边长只有 256–350 像素，放大不能恢复不存在的细节；这仍是数据质量上限。

本轮同时改变分辨率与视图组成，是组合方案试验，不能单独归因于其中一项。
预处理记录实际缩放尺寸和裁切偏移，并把真实偏移传给 SDXL 尺寸条件。
不同分辨率的去噪 MSE 不直接横比，更不能换算成画风相似度。

`configs/lillie-benchmark.json` 在看新结果前固定人物、正负提示词、生成参数和验收项。
固定莉莉艾初始造型：长浅金发、绿眼睛、白宽檐帽、白色无袖高领连衣裙、纯浅青背景。
以同一底模、原生运行程序、1024×1024、28 步、CFG 5、Euler a、CLIP skip 2、seed 42，生成三组：
无适配器、旧 512/40 步适配器、新 768/40 步适配器。两组 LoRA 强度均为 1，没有参考图适配器。
新旧 LoRA 都与这张新基线比较，不拿历史棕发人物的图片做莉莉艾的基线。
人物属性先验收，再分项看细彩线、虹膜层次、柔和光影；亮度提高或低对比度不自动算画风改善。
单人物单种子只提供诊断证据，四张开发验证图也不是从未参与决策的最终测试集。

```powershell
data/lora-env/Scripts/python.exe scripts/train-style-lora.py --architecture sdxl --manifest data/lora/animagine-detail/manifest.json --output data/lora/animagine-detail-40 --steps 40 --resolution 768 --threads 6 --learning-rate 0.00005
.venv/Scripts/python.exe scripts/render-sdxl-control.py --spec configs/lillie-benchmark.json --output data/lora/lillie-base
.venv/Scripts/python.exe scripts/compare-sdxl-lora.py --training data/lora/animagine-pilot-40 --baseline data/lora/lillie-base --output data/lora/lillie-prior-40
.venv/Scripts/python.exe scripts/compare-sdxl-lora.py --training data/lora/animagine-detail-40 --baseline data/lora/lillie-base --output data/lora/lillie-detail-40
```

重跑必须指定新输出目录。训练与对照完成后另行记录验收，不能以训练损失下降提前宣称学会画风。

### 莉莉艾对照结果（2026-11-05）

768 训练完成 40 步，更新阶段耗时 714.44 秒，20 个视图各更新两次；最大参数变化 0.0016253，
重载最大误差 0，适配器开关的探针预测平均差异 0.0048035（仅验证生效，不是质量指标）。
本轮同分辨率开发验证 MSE 从 0.1848487 小幅降到 0.1835196，不与旧 512 实验的 MSE 横比。
固定提示词在两个本地 CLIP tokenizer 下均为 75 tokens，没有超过 77 上限。

三组图片均完成，基线约 1056.5 秒；旧、新 LoRA 在训练结束后并行生成，分别约 1404.97、1411.68 秒。
两组均实际应用 1680/1680 个 UNet LoRA 张量，24 项最终技术核验通过；后端回归测试 121 项通过。
顺序队列原先计划的新版生成在并行结果已存在后被目录保护阻止，没有重复生图或覆盖已验证结果。

**画风验收未通过。** 新版眼睛下部色阶和反光有小变化，仍是偏动画的简单分层；
没有出现参考作品那种细碎、多层的通透结构。帽子、发丝、脸和衣服仍以黑色轮廓及大块硬边阴影为主，
局部渐变和鼻部亮块不足以构成细彩线、柔和光影的整体改善。新版头发阴影比底模和旧版更偏灰绿。
三组都能辨认莉莉艾，绿眼、白帽、无袖白裙主体保留，但预设的高领、完整帽檐、浅色纯背景没有全部满足。
因此不能认定新方案明显胜过底模或旧 LoRA，也没有将其设为默认。

这轮排除了“没加载权重”和“本轮提示词被截断”，但没有证明分辨率/近景裁图是原先提升弱的唯一原因。
训练预算仍很小，原图局部清晰度有限，眼部去噪加权也不是画风评价目标。
角色标签与底模可能带来动画平涂先验，这是观察后的假设，未做移除标签的独立对照，不能当作已证实因果。
后续更有判别力的试验是固定本轮其他条件，比较连续训练的 40/80/160 步检查点，再单独检验数据质量或 rank。
单人物、单种子的结果不能代表稳定泛化效果。

完整本地对照报告：`data/lora/lillie-review/index.html`；整图 `comparison.png`、同坐标眼部放大图 `eye-detail.png`，
技术核验 `technical-acceptance.json`、分项观察 `assessment.json`。
原图分别在 `data/lora/lillie-base/baseline.png`、`data/lora/lillie-prior-40/lora.png`、`data/lora/lillie-detail-40/lora.png`。
新版权重在 `data/lora/animagine-detail-40/xiaoxiao-smoke.safetensors`。

## 固定方案的连续 40 / 80 / 160 步检查点

`--checkpoints 40 80 160 --steps 160` 在一次连续训练中导出检查点，始终使用同一个 AdamW 实例。
导出时不删除或重建适配器参数，保存前后检查参数对象身份、优化器步数和随机状态；验证/探针运行于隔离的 RNG 上下文，
结束后恢复训练模式。每个检查点保存 PEFT、Diffusers、原生推理权重、独立报告，以及含 AdamW 动量、随机状态、
当前样本顺序的 `training-state.pt`；共享缓存保存在训练目录的 `cached-features.pt`。
现有 `--resume-training` 仍只是重启优化器的权重热启动，不能和连续检查点实验混用。
这些状态文件用于留存与审计，当前 CLI 尚未提供完整训练状态的中断恢复入口。

本轮从第 0 步重跑，因为上一轮没有保存优化器；保持同一数据清单、768、rank 4、学习率 5e-5、seed 42、6 线程。
源数据清单中的原 40 步试训预算由本次明确的 `--steps 160` 覆盖，图片、裁图、标注、区域权重与验证集不变。
检查点分别覆盖每个视图 2 / 4 / 8 次更新，即每张独立原作 4 / 8 / 16 次。
固定实验与验收条件在 `data/lora/lillie-continuous-review/plan.json`，仍使用 `configs/lillie-benchmark.json`。
发色、绿眼、白裙主体、领口、帽檐和背景逐项记录，区分基线已有缺陷与继续训练新引入的退步。
明显的发色或服装漂移不能以“风格更强”抵消。

```powershell
data/lora-env/Scripts/python.exe scripts/train-style-lora.py --architecture sdxl --manifest data/lora/animagine-detail/manifest.json --output data/lora/animagine-continuous-160-r1 --steps 160 --resolution 768 --threads 6 --learning-rate 0.00005 --checkpoints 40 80 160
.venv/Scripts/python.exe scripts/compare-sdxl-lora.py --training data/lora/animagine-continuous-160-r1/checkpoint-0080 --baseline data/lora/lillie-base --output data/lora/lillie-continuous-80
.venv/Scripts/python.exe scripts/compare-sdxl-lora.py --training data/lora/animagine-continuous-160-r1/checkpoint-0160 --baseline data/lora/lillie-base --output data/lora/lillie-continuous-160
data/lora-env/Scripts/python.exe tests/test_lora_checkpoint_state.py
```

第 40 步与上一轮权重逐张量核对；只有原生权重数值完全相同且原图的运行条件验证通过，才允许复用上一轮同题同种子图片，
否则必须重新生成并保留新旧报告。复用时明确记录原始推理来源，不把已有图伪装成新生成。
首次启动在模型加载阶段发生 `c10.dll` 原生访问异常，没有参数更新；失败报告保留在
`data/lora/animagine-continuous-160/report.json`，相同配置重试目录为 `animagine-continuous-160-r1`。

### 连续检查点结果（2026-11-05）

160 次更新全部完成，最后一条更新记录耗时 3868.36 秒（包含 40、80 步中途验证/导出的时间）。
40 / 80 / 160 步状态文件中的 AdamW 步数分别为 40 / 80 / 160，每个视图分别更新 2 / 4 / 8 次，
每原作分别更新 4 / 8 / 16 次；三次重载误差均为 0，保存检查点未改变后续随机状态。
最大参数变化分别为 0.0016253 / 0.0035811 / 0.0072556，验证去噪 MSE 为
0.18351957 / 0.18284769 / 0.18211796；未训练底模为 0.18484868。
这些是训练与去噪指标，不是画风相似度。

第 40 步与上轮原生权重逐张量字节一致，原图的实际提示词、负向约束、采样参数、底模及运行程序哈希校验通过，
因此复用原图；文件元数据不同不影响已核对的数值，复用证明记录两份文件哈希和原始生成来源。
80、160 步重新生成，各自原生日志确认加载了相应检查点全部 1680/1680 个 UNet LoRA 张量。
最终 63 项技术核验通过；125 项应用回归测试通过，应用环境跳过的 2 项 PyTorch 状态测试已在训练环境单独通过。

**画风与属性联合验收仍未通过。** 80、160 步仍保留黑色轮廓、大块硬边平涂和简单虹膜分区，
没有随步数增加出现参考作品的细彩线、多层通透虹膜及柔和光影。
40、80、160 步发色均存在灰绿/灰橄榄倾向，增加训练没有恢复明确浅金发；不主张偏色严格单调加重。
白裙、蓝色领口主体总体保持，没有完全换装；80、160 步袖窿与侧边轮廓有所变化。
高领、完整帽檐和浅色纯背景的偏差在基线中已经存在，不能全部归因于新增训练。
没有把 160 步设为默认升级，也没有挑选种子、修改提示词或修饰图片来改善展示结果。

本轮支持的结论仅是：在当前方案、莉莉艾题目与 seed 42 下，增加到 160 步没有带来合格的画风改善。
不能由此断言更多步数永远无效，也不能单独归因于 rank、底模或角色标签。
训练样本中的冷色头发内容与风格耦合是后续可独立检验的假设；去噪 MSE 的小幅下降不足以证实视觉进步。

本地报告：`data/lora/lillie-continuous-review/index.html`；整图 `comparison.png`、同坐标眼部放大 `eye-detail.png`，
评价记录 `assessment.json`、核验记录 `technical-acceptance.json`、40 步复用证明 `40-reuse-proof.json`。
三个检查点位于 `data/lora/animagine-continuous-160-r1/checkpoint-0040`、`checkpoint-0080`、`checkpoint-0160`。
对应对照图在 `data/lora/lillie-continuous-40`、`lillie-continuous-80`、`lillie-continuous-160`。
