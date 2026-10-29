# MangaFlow Agent 资源

此目录提供 MangaFlow 官方 Skills，帮助 Agent 翻译漫画、部署服务或开发维护项目。仓库通用指引位于 [AGENTS.md](../AGENTS.md)。

| Skill | 使用场景 |
| --- | --- |
| [mangaflow-translate](skills/mangaflow-translate/SKILL.md) | 导入漫画、批量翻译、逐页看图复核、局部纠错和导出 |
| [mangaflow-development](skills/mangaflow-development/SKILL.md) | 修改代码、排查任务与排版问题、维护模型接入和 MCP |
| [mangaflow-deploy](skills/mangaflow-deploy/SKILL.md) | 检查部署主机硬件，选择 NVIDIA GPU 或用户明确接受的 CPU 部署 |

## 使用方式

支持仓库 Skill 发现的客户端可以按名称加载；支持 `$skill-name` 的客户端可使用以下请求：

```text
请使用 $mangaflow-translate，通过已连接的 MangaFlow MCP 翻译我指定的漫画目录，
完成逐页看图复核和必要纠错，将 CBZ 与复核报告保存到我指定的目录。
```

```text
请使用 $mangaflow-development，检查并修复我描述的问题，按改动范围完成验证。
```

```text
请使用 $mangaflow-deploy，先检查实际部署主机和 Docker 环境，再部署 MangaFlow。
没有可用 NVIDIA GPU 时先说明性能影响，等我明确接受后再使用 CPU 部署。
```

自动发现、调用语法和加载位置取决于客户端。无法自动发现时，让 Agent 显式读取对应 `SKILL.md`；如将 Skill 单独复制到客户端支持的技能目录，应同时保留仓库文档链接的访问方式。

翻译 Skill 需要已连接的 MangaFlow MCP。连接程序安装、HTTP 与 stdio 的区别见 [MCP 接入文档](../docs/mcp.md)。Skill 本身不启动服务或子 Agent；多 Agent 执行由客户端原生能力提供。

## 维护约定

- MCP 返回的 `workflow_get`、`workflow_status` 和实际工具 Schema 是运行时依据；Skill 只保留入口与关键约束。
- 修改业务行为时同步核对 Skill，避免复制整套参数和工作流而产生多份不同的说明。
- Git 仅收录本目录说明及上述三个官方 `SKILL.md`。本机安装的第三方 Skills 和其他本地资源继续忽略。
