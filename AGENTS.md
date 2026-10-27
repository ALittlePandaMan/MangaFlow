# MangaFlow Agent 指引

MangaFlow 是保留原图、支持人工精修的漫画翻译工作台。按当前任务读取对应 Skill：

- **翻译、复核或修正漫画**：读取 [mangaflow-translate](.agents/skills/mangaflow-translate/SKILL.md)，通过已连接的 MCP 执行业务操作。
- **修改、排查或维护项目代码**：读取 [mangaflow-development](.agents/skills/mangaflow-development/SKILL.md)，再按需查阅[架构说明](docs/architecture.md)和[开发指南](docs/development.md)。

加载方式和调用示例见 [Agent 资源说明](.agents/README.md)。用户当前指令决定任务范围；翻译漫画不默认包含修改代码、安装依赖或部署服务。

## 开发规则

- 保留已有未提交改动、私有配置、数据库和漫画资产。测试使用隔离数据；后端测试入口为 `bash scripts/test-backend.sh`。
- 复用业务服务、任务队列、撤销历史及结果发布机制。原图只读；人工文字、蒙版、译文框、明确归零的旋转和锁定状态均需保护。
- 写入继续校验页面版本，异步发布不能覆盖新编辑；MCP 写操作保留操作去重和复核失效规则。
- 当前后端使用单服务进程，遵循现有模型并发配置。不要通过增加 workers 或后端副本绕过进程内锁。
- MCP 使用固定工具白名单。网页的活动框和 toast 只展示网页发起的任务；MCP 任务通过 MCP 查询。
- 新增 UI 文案覆盖简体中文、繁体中文、英语和日语，复用现有全局 toast、Dialog 及右上角活动框。
- 按实际改动执行检查，报告验证范围及限制。提交、推送、发布和部署以用户授权的任务范围为准，读取本指引不代表获得这些操作的授权。
- 提交时沿用用户确认的 Git 作者身份；不要因为使用编程助手而将仓库作者自动替换为 Codex。修改署名只影响后续提交，重写已发布历史需另行明确授权。
