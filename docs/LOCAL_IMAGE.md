# 真正的本地生图（Windows CPU）

`configs/local-image.toml` 使用本机 Qwen2.5 0.5B 构思、SD-Turbo Q8_0 生图。**图片是真实扩散模型输出，评价仍为 mock 固定分数。** 作品中的 `simulation` 分别标记每个角色；`simulated` 表示流程是否包含任何模拟角色，因此这套配置的总标记仍为 `true`。

## 首次准备

先按 README 安装 Python 包。在仓库根目录运行：

```powershell
.\scripts\setup-local-model.ps1
.\scripts\setup-local-image.ps1
.\scripts\start-local-model.ps1
```

已经准备过文本模型可跳过第一步。生图运行器固定为 `master-1043-3be2378`，压缩包约 18 MB；生图权重约 2.02 GB，首次下载需要等待。setup 核对下载文件 SHA256，文件放在不进入 Git 的 `data/local-image/` 下。不需要安装 PyTorch、不依赖云端 API，也不需要独立显卡。

模型来源是 Stability AI 的 SD-Turbo，使用 [Green-Sky 发布的 GGUF 量化版本](https://huggingface.co/Green-Sky/SD-Turbo-GGUF)，不是 Stability AI 官方发布的 GGUF 文件。模型说明与许可见 [原始模型卡](https://huggingface.co/stabilityai/sd-turbo)；运行器来自 [stable-diffusion.cpp](https://github.com/leejet/stable-diffusion.cpp/releases/tag/master-1043-3be2378)。

## 生成一张图

```powershell
# 已存在 local_art 时，跳过创建画师
.\.venv\Scripts\paintai.exe --config configs/local-image.toml painter add local_art --name "本地画师" --style "watercolor painting"
.\.venv\Scripts\paintai.exe --config configs/local-image.toml create "An autumn forest with golden leaves, a winding path and soft morning sunlight" --painter local_art
.\.venv\Scripts\paintai.exe --config configs/local-image.toml gallery --painter local_art
```

输出的 `image_absolute_path` 是真实生成并入库的图片。不要加 `--offline`，否则三个角色都会被替换成模拟实现。

`prompt_language="en"` 要求构思模型输出英文提示词。可以尝试中文主题，但本机 0.5B 模型在一次中文长主题测试中输出超长、未完成合法 JSON，程序已正确记录失败并未启动生图。当前建议先使用上面的英文主题；小模型的理解和结构化输出稳定性仍有限。作品记录保存实际提示词以便检查。

## 参数与运行方式

修改配置的 `[generator.local_image]`：

- 默认 512×512，4 步，CFG=1，Euler，discrete 调度，seed=42，8 个 CPU 线程。
- `seed` 可更改以生成不同版本；模型、种子和参数均随作品配置快照保存。
- `steps` 越多通常耗时越长；本配置用于快速接入验证，不保证更多步数一定改善质量。
- `executable`、`model_path`、`work_dir` 相对于配置文件目录解析。
- `timeout_seconds=1200` 限制单次生成时间，超时结束本次进程并记录失败，不自动重跑。

每次生图启动一个隐藏本地进程，完成后自动退出、释放模型内存，不启动常驻生图服务器。文本模型仍由 start/stop-local-model 脚本管理。

`data/local-image/runs/<运行ID>/` 保留 UTF-8 提示词、原始生成图片和 `generation.log`。成功图片另行写入画师作品库；失败时 CLI 返回日志路径。调用使用参数数组及提示词文件，不通过 shell 执行提示词。

这一步验证“本地构思 → 本地生成真实图片 → 持久化”。真实视觉评价和画师成长仍待后续接入与实验验证。

## 本机实测（2026-11-03）

- 作品 ID：`4b0376aa7ff94d1d9c2d2ef587ba9d50`，画师 `local_art`。
- 图片：`data/painters/local_art/gallery/4b0376aa7ff94d1d9c2d2ef587ba9d50.png`，512×512。
- 原生日志：采样 7.66 秒，解码 9.07 秒，生成合计 17.04 秒；整条创作流程约 26 秒。这是单次测量，不是性能承诺。
- 已打开图片核查：可见金黄色树叶、林间小路和日照；水彩风格表现较弱，不能据此认定模型严格遵循风格。
- 构思、生图角色均为真实本地推理；评价仍为模拟，固定分数不能用作质量结论。
