# MangaFlow 漫画翻译 MCP

MCP 2.0 只开放漫画导入、检测与 OCR、翻译去字、排版、逐页看图纠错和导出。
模型安装、字体、系统设置、项目删除及历史管理在网页使用。MCP 编辑仍写入网页的撤销历史。

官方 [mangaflow-translate Skill](../.agents/skills/mangaflow-translate/SKILL.md) 提供 Agent 执行入口和关键约束，运行时以 `workflow_get`、`workflow_status.next_steps` 及实际工具 Schema 为准。不同客户端的加载方式见 [Agent 资源说明](../.agents/README.md)。

## 接入与升级

设置 → MCP 复制安装指令，或让 Agent 读取 `http://localhost:8080/mangaflow/setup`。
服务器部署时，将地址替换成实际 HTTPS 地址。官方安装程序会下载连接程序、安装固定依赖、
验证 stdio，并输出当前客户端需要的启动命令。Agent 应保留其他 MCP 配置，再按客户端格式添加。

连接程序 2.0.0 起只增加四个本地工具。旧版本请重新运行官方安装程序并重新连接；
地址 `/mangaflow` 和兼容地址 `/mcp` 不变，旧项目、运行 ID、复核记录和修正次数均可接续。
已移除的工具不能继续调用，也没有通用 HTTP/脚本执行工具。

- **本地 stdio**：`local_import` 读取 Agent 能访问的明确目录或文件，自动上传并创建项目；支持远程后端。
- **直接 HTTP**：处理已有项目或上传资产，不能读取客户端磁盘；导出返回下载链接。
- 本机 `http://localhost:8080/mangaflow` 默认无需密钥。远程 HTTPS 与旧 `/mcp` 使用
  `MANGAFLOW_MCP_TOKEN`；配置、安装说明和工具结果不应包含真实凭据。

通用 stdio 配置使用安装程序输出的绝对路径：

```json
{
  "mcpServers": {
    "mangaflow": {
      "type": "stdio",
      "command": "<安装结果 server.command>",
      "args": ["<安装结果 server.args[0]>"],
      "env": {"MANGAFLOW_MCP_URL": "http://localhost:8080/mangaflow"}
    }
  }
}
```

Codex 使用 TOML，WorkBuddy、OpenCode、Claude Code 各自使用其 MCP 配置格式；
安装说明要求 Agent 查询当前客户端支持的配置或原生命令，不覆盖已有服务器。
安装程序的独立探测成功后，当前 Agent 仍需重连，并检查 `mcp_status.connection_capabilities.local_import`。

## 公开工具

服务端固定 **20 个**，不会根据业务路由自动增加：

| 用途 | 工具 |
|---|---|
| 了解能力、查找项目 | `mcp_status`、`workflow_get`、`project_list` |
| 导入及恢复 | `project_import`、`project_import_status` |
| 翻译执行 | `workflow_start`、`workflow_status`、`workflow_advance` |
| 读取页面和图像 | `page_state`、`page_review_plan`、`page_preview` |
| 复核及修正轮次 | `workflow_review_page`、`workflow_begin_correction` |
| 局部编辑 | `page_edit`、`mask_paint` |
| 任务及请求恢复 | `task_wait`、`task_cancel`、`task_retry`、`operation_status` |
| 交付 | `workflow_export` |

连接程序额外提供 **4 个**：`local_import`、`local_download`、`local_transfer_status`、`local_transfer_cancel`。
工具的实际参数以 `tools/list` 为准。耗时操作返回任务 ID；`task_wait(seconds=0)` 查询，最大等待 20 秒。

## 官方执行流程

1. 读取一次 `workflow_get`。用户提供路径后直接调用 `local_import`，默认以目录或文件名创建项目。
   支持 JPG、PNG、WebP、ZIP/CBZ、RAR/CBR 和 PDF。后端负责解包、自然排序和完整校验。
   先等待本地传输，再等待返回的文件任务完成；传输完成仅表示导入任务已被接受。
2. 已有项目通过 `project_list` 查找；返回最近的运行 ID。`workflow_start` 固定页面范围与顺序，
   默认使用项目翻译服务，也支持 `translation_mode=agent`。已有人工内容和锁定区域会保留。
3. 新运行使用 2.1 批量工作流。`workflow_status` 提供批量计数、已就绪页和带版本的下一步调用。
   先按建议用 `workflow_advance(image_ids=...)` 分批入队，每次最多 20 页；第一轮停在检测、OCR、翻译之后。
   已有人工内容不覆盖，也不提前生成蒙版或修复。继续按建议调用，不逐页询问用户是否继续。
4. 调用 `page_review_plan`，按其请求获取并查看 `page_preview` 图片。默认只看一张原文框编号图，
   搭配 `page_state` 的 OCR 和译文；若标注遮住文字，按需获取原图局部裁图。整页发现漏检，分区裁图核对细节。
   已完成页可以与其他页处理同时复核，检查 OCR、翻译、误检及漏检；先保存问题，等整批结束再编辑。
   `workflow_review_page(phase=preflight,outcome=issues,disposition=defer)` 可省略 selection，仅记录问题，不授权擦除。
5. 整批结束后，使用 `workflow_begin_correction` 集中开启有问题页面的纠错。`page_edit` 删除误检、补框、
   修正文字或调整框；原文框用于擦除，译文框用于排版。仅重做受影响的 OCR／翻译，修改后用新图片复检。
   调整原文框会触发局部重新 OCR；显式提交非空 `source_text` 可确认人工校对，清空则请求一次重新识别。
   去字前通过记录须覆盖 geometry、false_detection、missing_text、ocr、translation，显式填写 `selection`。支持：

   ```json
   {"mode": "all_eligible", "exclude": ["R003"]}
   ```

   或 `{"mode":"regions","region_ids":["R001","R002"]}`。显式空列表表示跳过擦除；漏填会报错。
   保留艺术字需记录 `preserved` 的区域或裁切位置及原因。不能仅隐藏译文却继续擦除原画。
6. 再次批量 `workflow_advance`，后端仅对已批准页面执行蒙版、修复和排版。Agent 翻译模式先通过 `page_edit` 写入译文。
   修改原文、译文、框或蒙版后，后端选择受影响阶段，不让 Agent 手动拼流水线参数。
7. 最终默认查看一张原图与译图对照图（`kind=comparison`）；竖幅左右对照，横幅上下对照。
   需要核查残影、译文遮住的原文字迹、画面损坏或框位置时，追加净图、蒙版或编号局部图。
   长页按计划分区查看，不能只检查已有框。每侧分图分别计算可读比例，额外裁图只补足未清晰覆盖的部分。
   提交所有检查项和真实发现的问题。无法看图时记录 `unverified`，不能称为视觉通过。
8. 其他译图仍在生成时，最终问题先用 `disposition=defer` 记录；本批结束后集中纠错。
   `workflow_begin_correction` 的去字前和最终纠错共用每页两轮预算，断线或重连不会重置。
   `page_edit` 支持一次新增、修改、删除多框，通过删除旧框并添加新框实现拆分或合并。
   `mask_paint` 处理笔画或多边形。删除后若返回重建任务，应先等待。
   修正后获取新版本、新图片再次复核。达到上限、无法确定或没有改善时，选择 `deliver_with_issues`，继续下一页。
9. 所有冻结页面都有当前版本的最终记录后，`workflow_export` 导出 CBZ 或 `kind=translated` 的译图 PNG ZIP。
   JSON、Markdown 报告单独下载，记录保留艺术字、修正轮次、未解决问题和未验证页面。

`translate_and_review`、`review_and_fix`、`repair_regions` 三个 Prompt 名称保留，均读取同一份工作流。
已有译本按实际状态跳过完成阶段；页面、区域范围通过 `workflow_start` 指定。
旧运行保留原有复核规则和进度；新运行默认批量模式。文本或译图问题延期记录不等于已批准处理或全部通过。

2.1.1 的默认图片证据从普通页的六张减至两张返回图片，最终对照图包含两侧画面。
这减少重复看图和调用，不保证固定的视觉 token 降幅。长页、高清页及不确定问题仍需增加图片。
旧版分别提交的图片证据继续有效；版本校验、可读区域至少覆盖 98% 和每页两轮纠错限制不变。
对照图的 `panels` 提供各侧的 `display_bounds`、`crop` 和缩放关系；所有编辑仍用原图坐标。

## 数据量、并发与恢复

- `page_state` 默认只返回完整 OCR、译文、区域编号和必要几何信息。`include_details=true` 增加样式和问题提示，
  不返回内部排版缓存。正文按区域分页，不截断文字。后续分页携带首批的 `expected_version`。
- `workflow_status` 默认一页。显式 `offset/limit` 查看其他页，详情按需读取。质量提示本身不修改页面。
- `R001` 等编号和 UUID 均可作为当前页面的区域引用；编号可能因删除重排，因此编辑必须带最新 `expected_versions`。
- 原文、译文分别编辑；角度使用译文 `rotation`，原文旋转可提交旋转后的多边形顶点。
  支持有效凹多边形；退化、自交或非法透视形状会报错，不扩大成矩形擦除。
- 写请求使用 1–80 字符 `operation_id`，可以直接填写唯一字符串。相同请求重试复用；修改参数必须换新 ID。
  超时先查 `operation_status`；`RUNNING/UNKNOWN` 不应使用新 ID 盲目重发。
- `operation_status` 会依据入队时保存的操作标识找回已接受的处理/文件任务，不重新执行。
  仍为 `UNKNOWN` 时，先读取本次运行的全部页面与任务，再用同一工具提交
  `accept_current_state=true`、全部页面的 `expected_versions` 和检查说明 `note`。
  后端只登记接受当前状态，保留“原操作结果未知”；有活动任务或结果不明的付费请求时拒绝接续。
- `task_retry` 仅接受可安全重试的失败处理任务，最多两次；取消、暂停或结果不明的付费请求不会自动重启。
- 导出失败后先查看错误并处理原因，再用新的 `operation_id` 调用 `workflow_export`；取消的导出仅在明确要求后重新提交。
- 外部编辑使旧复核失效。导出固定复核版本，已通过页面复用被检查的图像；未解决或未验证内容明确写入报告。
  导出快照封存前再次校验复核结果、问题记录和修正轮次；封存后继续编辑不会改变已固定的译图与报告。
- 网页与 MCP 共享模型、项目及页面锁。网页只显示网页发起的进度和 toast，MCP 通过工具查询任务。
- 断线后后台任务继续。上下文压缩后使用运行 ID 接续；MCP 不能替客户端启动已经结束的 Agent 推理。

协议测试使用替代模型验证调用闭环；这不代表真实视觉质量已通过。
WorkBuddy、Codex、OpenCode、Claude Code 的多模态判断需分别实测并记录客户端和模型版本。

## 2.2 多 Agent 协作

使用客户端原生子 Agent；先确认子 Agent 能调用 MCP 并查看图片，再由主 Agent 创建一次
`workflow_start(coordination_mode=parallel)`。并发人数由客户端选择，后端模型并发配置照常生效。
不具备这些能力时，继续默认 `single` 模式。不同客户端的子 Agent 启动方式不写成 MCP 工具。

主 Agent 保留创建结果中的 `coordinator_token`，用于 `workflow_advance(action=process)`、任务控制及导出。
断线后通过原创建操作的 `operation_status` 恢复；运行状态也提供 `coordinator_operation_id`。
这是同一 MCP 授权下的协作防冲突凭据，不是独立用户鉴权或安全沙箱。

子 Agent 只需运行 ID、不同的 `worker_id` 和 `workflow_get(role=worker)`，动态领取一页：

```json
{"run_id":"<run_id>","action":"claim","worker_id":"reviewer-1","operation_id":"reviewer-1-claim-1"}
```

返回 `assignment.token`、有效期、页面版本及下一步。每位工作者一次持有一页；存活领取默认 15 分钟，
以 `action=renew` 续期，或 `action=release` 交回主 Agent 等待模型处理。续期和释放携带
`image_id`、`worker_id`、`assignment_token`。领取、续期、释放本身不改变页面版本。

编辑使用 `workflow_run_id`，复核和开启纠错使用 `run_id`；两者都携带 `worker_id`、
`assignment_token` 和最新 `expected_versions`。不能通过省略运行 ID 绕过正在执行的协作。
提交阶段复核后自动释放领取，尚未结束的纠错除外。过期或被重新领取的凭据不能写入。
`workflow_status(worker_id,assignment_token)` 只显示该工作者的页面，不自动续期。

首轮批量任务期间，子 Agent 只复核已发布页面并记录问题。整批结束后可在不同页面并行修改；
子 Agent 释放需要 OCR、翻译、修复或渲染的页面，由主 Agent 提交处理。
某页的局部处理不挡其他页纠错；所有人仍须等待自己页面的实际任务完成后获取新证据。
最终译图的第一轮批量生成期间也只记录问题，完成后进入并行局部纠错。

完整发现、工作者标识和领取至提交的时间写入原有复核记录。子 Agent 向主 Agent 返回精简摘要，
主 Agent 据持久化记录汇总，避免重复看整本。工作者不导出、不取消整本任务、不继续派生工作者。
普通页两张默认图片、长页覆盖、证据版本、人工编辑保护和每页两轮纠错要求继续适用。

项目语言、上下文或设置变化后，工作者仍可释放领取。主 Agent 读取 `workflow_status.configuration_change`，
检查变化并等待已有任务结束、领取释放，再提交返回的 `workflow_advance(action=accept_configuration)`：
携带当前 `configuration_version`、全部冻结页面的 `expected_versions`，并在并行运行中携带 `coordinator_token`。
该操作保留运行 ID、页面内容、问题记录、编辑历史和纠错次数，将旧复核存档并要求重新复核；
它不会自动重翻已发布译文。主 Agent 必须按新的目标语言检查已有文字，并在必要时使用现有纠错流程。
未解决的最终问题继续保持运行保护；明确选择带问题交付后才视为可交付。

### 验收记录

2026-10-27：135 项 MCP 回归通过，覆盖未解决复核的领取保护、其他页面局部处理时继续分派纠错、
配置变化后的领取释放、原运行接续、复核失效、人工文字与纠错次数保留，以及重复／中断请求恢复。
HTTP 和 stdio 均实测了接受配置后重新提交复核；50 页确定性处理、复核和导出场景继续通过。
服务端仍为 20 个工具，定义约 27 KiB。本轮没有进行 WorkBuddy 真实多模态验收或耗时对比。

2026-10-26：2.2 协作实现通过 151 项既有回归和 35 项协作验收，另外验证了凭据异常输入。
HTTP 与 stdio 使用官方 SDK 验证了独立会话领取、图片返回、结果提交和协调者权限。
50 页确定性样本以替代模型完成批量处理、四名工作者提交复核及 CBZ／报告导出，
检查了页数、顺序和重复任务；这些工作者是协议测试客户端，不是真实多模态子 Agent。
还覆盖领取竞争、过期凭据、断线恢复、旧运行接续、网页并发编辑、两轮纠错限制和迁移数据保留。
工具定义约 24 KiB，11 页默认状态响应不超过 6 KiB。

Docker 后端已应用 `20261025_10` 迁移，健康检查通过；升级前后抽查的 32 页内容校验一致。
WorkBuddy 位于另一台电脑，尚未实测其子 Agent 的 MCP 继承、图片查看及自主复核能力。
10 页真实漫画的单 Agent／并行耗时对比也尚未进行；不以替代模型或协议测试耗时推断提速比例。

此前精简图片复核的 119 项回归已覆盖 HTTP/stdio、11 页批量流程、长页覆盖、
PNG 大小限制后的分图比例、并发版本变化及旧证据接续；也已查看实际漫画返回的编号原图和对照图。
这些结果不替代 WorkBuddy 的端到端多模态验收，也不代表其他客户端具有相同的判断质量。
