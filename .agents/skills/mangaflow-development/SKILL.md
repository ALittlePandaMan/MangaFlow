---
name: mangaflow-development
description: 开发、排查或维护 MangaFlow 的后端、编辑器、模型接入、MCP 和部署代码，按现有架构保护数据并完成针对性验证；处理漫画内容使用 mangaflow-translate。
---

# MangaFlow 项目开发

先读取仓库 [AGENTS.md](../../../AGENTS.md)，检查工作区已有改动，再按任务阅读[架构说明](../../../docs/architecture.md)和[开发指南](../../../docs/development.md)。按现有模块边界定位修改，不默认重构无关代码。运行环境、依赖和命令以当前仓库文件为准。

## 业务与数据约束

- HTTP／MCP 入口复用业务服务和应用命令，处理复用现有流水线、队列和文件任务。涉及页面或区域写入，保留版本校验、撤销历史、操作来源及异步结果发布保护。
- 原图保持只读，蒙版、净图和译图是派生资产。区分原文框与译文框，不能为了排版扩大擦除范围；删除误检需恢复误擦画面。历史、导出快照和任务引用的资产不得被提前清理。
- 保护人工 OCR、译文、蒙版、译文几何、方向、明确设为零的旋转、透视、手动字号和锁定状态。原文变化须使相关结果失效；单纯渲染或导出不应突然触发模型分析并改写人工布局。
- 模型计算不持有页面写事务。事务提交及迟到结果发布时再次核验版本，冲突不能覆盖用户或其他 Agent 的新内容。
- 当前单后端进程承载进程内模型锁、缓存和页面任务管理。沿用配置并发，不通过增加 Uvicorn workers、服务副本或放开本地模型并发规避等待。
- 私有 `.env`、`config.yaml`、数据库、漫画和权重保留原样；配置迁移不覆盖既有人工选择。检查日志和测试输出时避免泄露凭据。

## MCP 与前端

涉及 MCP 时按需读取 [MCP 接入文档](../../../docs/mcp.md)、`backend/app/services/mcp_catalog.py`、当前工作流定义及 `connectors/mangaflow-mcp/`。

- 服务端使用固定的 20 个工具，本地连接程序额外提供 4 个传输工具；不能自动公开整组路由或增加通用绕过入口。参数、工具说明、工作流和连接程序一起核对。
- 保持 `operation_id` 去重、预期版本、预览证据失效、有限纠错和并行领取校验。查询状态不修改页面、不自动领取或提交任务。
- 网页与 MCP 共享项目、模型和队列；网页活动框和 toast 只展示网页发起的任务。修改任务来源过滤时覆盖处理队列和文件任务。
- UI 使用现有表单、Dialog、全局 toast 与右上角活动框；新增文案覆盖简中、繁中、英语和日语。前端预览、后端渲染、MCP 图片及导出保持几何和角度解释一致。

## 验证与交付

从以下入口选择与修改相关的检查，依赖已就绪时运行。Bash 脚本在 Windows 使用 WSL2；后端要求 Python 3.11／3.12，Node.js 要求见各包的 `package.json`。

| 改动 | 检查入口（仓库根目录） |
| --- | --- |
| 后端服务、模型或队列 | `.venv/bin/ruff check backend/app backend/tests scripts`；`bash scripts/test-backend.sh -q backend/tests/<相关测试文件>.py` |
| 前端交互 | `npm run lint --prefix frontend`；`npm test --prefix frontend -- <相关测试文件>` |
| UI 文案与类型 | `npm run i18n:check --prefix frontend`；`npm run visual:typecheck --prefix frontend`；`npm run build --prefix frontend` |
| 本地 MCP 连接程序 | `npm test --prefix connectors/mangaflow-mcp`，同时检查对应后端协议测试 |
| 需要全量检查 | `bash scripts/check.sh`；连接程序单独运行上面的测试命令 |

- 后端测试使用 `scripts/test-backend.sh` 自动创建、清理独立 PostgreSQL 测试容器；显式提供测试数据库时，仅使用专用 `mangaflow_test_` 数据库，禁止指向真实项目数据库。测试数据、文件和模型替代物保持隔离。
- 处理流程测试优先用确定性样本及替代模型；真实模型、付费服务、Photoshop、WorkBuddy 等客户端的实测结果另行说明，不能用协议测试代替视觉质量结论。
- 文档或 Skill 修改检查相对链接、Markdown、参数和实际命令，无需为文字改动启动漫画处理。同步更新受影响文档及官方 Skill，避免维护多份不同的工作流。
- 交付时说明行为变化、完成的验证和实际限制。提交、推送、发布及部署按用户请求执行，不由本 Skill 自动触发。
