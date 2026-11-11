# 当前画室数据快照

由 `scripts/repository-data.py --export` 生成。`index.json` 记录各包的画师 ID、记录数、大小和 SHA-256；启动脚本会自动恢复缺失画师。

包含生成作品、学习与记忆、评价及发布的实验报告；排除密钥、模型权重和参考原作。部署步骤与更新规则见 [仓库快照部署指南](../../docs/REPOSITORY_DATA.md)。不要把本机 SQLite 数据库或密钥复制进此目录。
