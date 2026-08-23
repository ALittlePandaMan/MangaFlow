# 常见问题

## 首次模型加载很慢

PaddleOCR、MangaOCR 和 LaMa 第一次使用时需要下载或加载权重。检查 `models/` 是否可写，并通过 `./scripts/docker.sh logs -f backend` 查看真实进度。后续启动会复用缓存。

## 云端翻译仍返回原文

确认当前翻译 Provider 不是 `passthrough`，并在设置页成功连接 API、获取模型和保存密钥。地址、协议和模型写入 `config.yaml`，密钥写入 `.env`。

## Docker 无法使用 GPU

确认宿主机安装了 NVIDIA 驱动、Docker 和 NVIDIA Container Toolkit，再检查容器内 PyTorch/Paddle 的 CUDA 状态。没有 GPU 时，本地开发可以使用 CPU 依赖；当前默认 Compose 面向 NVIDIA GPU 镜像。

## 页面任务已完成但界面仍在加载

先刷新任务状态并查看后端日志。若能稳定复现，请提交脱敏后的任务 ID、浏览器版本、后台状态响应和复现步骤，不要附带 API Key 或原始漫画内容。

## SQLite 锁冲突

本地环境保持任务并发数为 1。当前仓库没有完整的 PostgreSQL 部署 profile，不要只修改 URL 就视为生产就绪；多 worker 部署前还需要数据库驱动、迁移和队列适配。
