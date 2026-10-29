---
name: mangaflow-deploy
description: 部署或切换 MangaFlow 的 Docker CPU／GPU 运行模式，先检查实际目标主机硬件，无可用 NVIDIA GPU 时说明性能影响，用户接受后再执行 CPU 部署；代码开发使用 mangaflow-development。
---

# MangaFlow 部署

按 [Agent 部署指南](../../../docs/agent-deployment.md)检查和执行；仓库规则见 [AGENTS.md](../../../AGENTS.md)。只在用户要求部署、升级或切换运行模式时使用，不把漫画翻译任务扩展为安装服务。

1. 确认实际部署目标和 Docker context，检查目标的系统、架构、CPU、内存、磁盘、NVIDIA 显卡、驱动及容器 GPU 访问。远程部署检查远程主机。
2. GPU 可用则推荐 GPU。有显卡但驱动／透传失败时先报告具体缺项；无法确认硬件时标记未知，不能当作无显卡。
3. 确认无可用 NVIDIA GPU 时不推荐本地批量部署，说明 CPU 检测、蒙版和修复会慢，默认本地 OCR 另增耗时。等待用户接受后再下载与启动；已有知情 CPU 授权时直接继续，不重复询问。
4. 只使用实际存在的发布标签或明确的源码构建方式。私有 `.env` 的 `MANGAFLOW_RUNTIME=gpu|cpu` 选择模式，默认 GPU、不自动降级。CPU 标签从 0.1.4 起提供，以对应 Release 完成为准；0.1.3 不包含预构建 CPU 镜像。
5. 保留原配置、资产、数据库卷、Compose 项目名和端口；CPU／GPU 切换不重置 OCR 和翻译服务。当前支持 Linux amd64 容器，ARM64 不自动模拟。
6. 验证网页、实际设备和 MCP 地址，再如实报告。没有模型样本验证时不称整条漫画流程已通过；不擅自调用付费服务。

提醒可引用指南中的 Xeon／V100 10 页实测，必须说明不含 OCR／翻译、硬件及样本边界，不承诺固定速度。安装规则保留在指南，不复制完整部署手册。
