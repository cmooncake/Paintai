# 拉取仓库并恢复当前画室

仓库 `snapshots/studio/` 保存 2026-11-11 导出的画室快照：7 位画师、85 条创作记录、82 张生成图（含实验报告图片）。它包含小笑和琪光的学习进度、创作记忆、文字风格档案、作品评价、前后对照与已发布 LoRA 实验报告。快照约 67 MiB。

## 新电脑运行（Windows PowerShell）

先安装 Python 3.11+、Node.js 22.12+ 和 Git。无需先安装本地绘图模型。

```powershell
git clone https://github.com/cmooncake/Paintai.git
cd Paintai
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[web]"
npm.cmd --prefix web ci
.\scripts\start-web.ps1 -Offline
```

打开 http://127.0.0.1:5173 。首次启动会校验快照并恢复缺失画师；选择小笑或琪光，在作品库、学习和 LoRA 实验中查看内容。历史图片及评价保持原样，`-Offline` 只决定新任务使用模拟模型，不会把历史真实作品改成模拟结果。

已有代码则先 `git pull`，再安装更新后的依赖并运行启动脚本。停止服务使用 `scripts/stop-web.ps1`。

## 恢复规则

- 启动脚本运行 `scripts/repository-data.py`，恢复到本仓库 `data/`。
- 已存在相同 ID 的画师会跳过，绝不覆盖本机新增作品；重复启动安全。
- 这是首次恢复快照，不是双向同步。后续拉取新快照不会自动替换已存在的画师。
- 校验失败会停止启动并显示原因；每位画师的导入采用已有迁移事务，不执行 ZIP 中代码。
- 可手动执行 `.\.venv\Scripts\python.exe scripts/repository-data.py`。使用 `--data-dir <空目录>` 可在独立目录验证恢复，不动当前画室。

## 不包含什么

不上传密钥、模型服务配置、底模和 LoRA 权重、参考原作、运行日志、训练缓存和未发布训练目录。生成图及文字评价会恢复，但参考图缩略图需重新上传；继续本地生图或训练仍须下载模型，使用云端则重新填写密钥。严格蒙版的最终作品、前后对照和验证文字包含在快照中，编辑候选、蒙版附件不在 v1 迁移包中。

因此可以直接恢复当前网站的已保存内容，但不能仅靠 Git 恢复本机全部推理环境。网页之外、尚未发布入库的实验中间文件也不属于快照。

## 在旧电脑更新快照

等待任务完成后执行：

```powershell
.\.venv\Scripts\python.exe scripts/repository-data.py --export
git add snapshots/studio
git commit -m "Refresh studio data snapshot"
git push
```

导出沿用迁移模块的字段清理和文件白名单，不直接提交 SQLite 或整个 `data/`。提交前仍应检查快照内是否包含自己不想公开的提示词或作品。每个 ZIP 当前低于 50 MiB；脚本拒绝达到 90 MiB 的单个包，避免超大文件直接进入 Git。
