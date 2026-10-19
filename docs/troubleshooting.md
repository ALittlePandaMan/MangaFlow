# 常见问题

## 首次模型加载很慢

RF-DETR 权重需要先按[版面检测说明](text-classification.md)显式安装。MangaOCR、可选 PaddleOCR 识字和 LaMa 第一次使用时需要下载或加载权重。检查 `models/` 是否可写，并通过 `./scripts/docker.sh logs -f backend` 查看真实进度。后续启动会复用缓存。

## 云端翻译仍返回原文

确认当前翻译 Provider 不是 `passthrough`，并在设置页成功连接 API、获取模型和保存密钥。地址、协议和模型写入 `config.yaml`，密钥写入 `.env`。

## Docker 无法使用 GPU

确认宿主机安装了 NVIDIA 驱动、Docker 和 NVIDIA Container Toolkit，再检查容器内 PyTorch/Paddle 的 CUDA 状态。没有 GPU 时，本地开发可以使用 CPU 依赖；当前默认 Compose 面向 NVIDIA GPU 镜像。

## 页面任务已完成但界面仍在加载

画布右上角“活动”卡片展示当次处理进度，可在推理时切换阅读其他页。成功或停止提示默认 15 秒自动关闭，也可手动关闭；失败提示保留供检查。若任务已结束但结果仍在同步，先查看后端日志。稳定复现时请提交脱敏后的任务 ID、浏览器版本、后台状态响应和复现步骤，不要附带 API Key 或原始漫画内容。

## SQLite 锁冲突

可暂时降低页面并发，并检查是否有第二个服务或诊断进程同时写库。数据库写入已有短事务与有限重试；长时间持锁仍需定位原因。仓库已提供可选 PostgreSQL Compose、驱动与 Alembic，按[配置与迁移](configuration.md)先停写、备份和演练，不要只改 URL。任务调度与模型锁仍为进程内状态，换用 PostgreSQL 后也不支持多个后端实例并行接管同一队列。
