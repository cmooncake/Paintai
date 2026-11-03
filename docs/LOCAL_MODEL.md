# 本地小模型试运行（Windows CPU）

使用官方 **Qwen2.5-0.5B-Instruct Q4_K_M GGUF**，通过 llama.cpp CPU 服务接入 Paintai 的构思角色。权重约 491 MB（469 MiB），运行包约 25 MB；均保存在被 Git 忽略的 `data/local-model/` 中，不安装系统服务、不修改全局 PATH。

这是一次真实文本模型接入测试：主题分析、构图、配色和生成提示词由本机模型产生。**生图和视觉评价仍为 mock，输出图片与评分不代表本地模型的绘画能力，作品仍标记 `simulated=true`。** 0.5B 模型适合验证接口，构思质量需人工检查。

## 启动与运行

在仓库根目录执行（已下载过模型可跳过 setup）：

```powershell
.\scripts\setup-local-model.ps1
.\scripts\start-local-model.ps1
.\.venv\Scripts\paintai.exe --config configs/local.toml painter add local_demo --name "本地小画师" --style "水墨"
.\.venv\Scripts\paintai.exe --config configs/local.toml create "秋天的树林" --painter local_demo
.\.venv\Scripts\paintai.exe --config configs/local.toml gallery --painter local_demo
```

画师已存在时跳过 `painter add`。不要加 `--offline`，该参数会覆盖配置并把构思也改为模拟模式。

服务在后台运行，仅监听 `127.0.0.1:18080`；CPU 使用 4 个推理线程，上下文 4096，单并发。无需 API 密钥。退出时运行：

```powershell
.\scripts\stop-local-model.ps1
```

重启电脑后需重新运行 start 脚本，不必重新下载。日志位于 `data/local-model/server.stderr.log`。setup 下载文件后核对官方 SHA256；start 核对模型 SHA256，并在端口已占用时退出。脚本记录并核对进程路径和启动时间，stop 只终止这次项目启动的服务。

## 配置与边界

`configs/local.toml` 使用已有 `openai_compatible` 适配器，`auth="none"` 只允许 loopback 地址；远程 API 默认仍使用环境变量密钥。构思请求通过 JSON Schema 约束，结果仍经过 Pydantic 校验。`paintai-local` 是启动脚本设置的模型别名，实际模型文件由 start 脚本指定。

要改用其他模型，需要修改启动脚本中的文件及校验逻辑，再调整上下文和线程数。当前 0.5B 模型是纯文本模型，不能直接作为视觉 Critic 或图像生成器。接入这两个角色需分别配置具备相应能力的后端。

官方来源：

- [Qwen 模型与许可](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF)
- [llama.cpp b12064 运行包](https://github.com/ggml-org/llama.cpp/releases/tag/b12064)
- [llama.cpp 服务接口](https://github.com/ggml-org/llama.cpp/tree/master/tools/server)
