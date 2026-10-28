# 配置与迁移

MangaFlow 把可公开的模板、可迁移设置和本机密钥分开保存。

| 文件 | 用途 | 是否提交 Git |
| --- | --- | --- |
| `config.example.yaml` | 脱敏的默认结构和示例值 | 是 |
| `.env.example` | 环境变量名和空值示例 | 是 |
| `config.yaml` | 当前六项生效配置 | 否 |
| `.env` | API Key、密钥和本机覆盖项 | 否 |

`config.example.yaml` 只能使用 `example.com`、`example-model` 等公开占位符，不能出现真实 API 地址、账号或密钥。

## 六项生效配置

`config.yaml` 记录文字检测、图像修复、OCR、排版渲染、云端翻译和字体资源。设置页面安装、切换或编辑 Provider 后，后端会原子更新该文件。API Key 不会写入 YAML，而是写入 `.env` 中对应的环境变量。

启动时会幂等读取配置清单：默认只补齐缺失阶段，不覆盖数据库中已有的生效配置。开启 `preload` 后会准备所选本地 Provider；RF-DETR 权重需通过推荐安装或[独立安装命令](text-classification.md#模型与运行环境)显式准备，其他支持自动下载的模型会复用 `models/` 中的缓存。

`config.example.yaml` 与正式部署包默认使用 PaddleOCR-VL 1.6；网页“安装推荐配置”会下载并选择 RF-DETR、PaddleOCR-VL 1.6、LaMa 和 Pillow。MangaOCR 和普通 PaddleOCR 等仍可选择，已有本机配置和数据库中的 OCR 不因升级模板而改变。已完整安装时只显示“配置已安装”。Pillow 是渲染组件，不需要模型权重。安装本地组件不会配置翻译服务，也不会重新识别已有页面；翻译需另行选择 Google 网页翻译或填写自定义兼容接口。示例翻译地址与模型名是占位值，不能直接用于翻译。

支持 GPU 的本地模型默认使用 `device: auto`：加载时优先选择可用 GPU，不可用则使用 CPU；遇到明确的 GPU 初始化或推理故障也会回退 CPU。该策略会原样保存，迁移或重启后重新选择设备，不会把某台机器的 CPU 探测结果固化到配置中。旧的 `device: recommended` 等同于 `auto`。也可以在设置中明确指定 CPU 或 GPU；仅支持 CPU 的 OpenCV、Tesseract、Pillow 不受影响，云端模型不设置本机设备。

修复使用的字形分割由 `MANGAFLOW_SEGMENTATION_BACKEND=auto` 控制：优先使用可用的 ONNX CUDA 运行时，显存余量不足、运行时缺失或推理异常时回退 OpenCV CPU。可显式设为 `opencv` 保留 CPU；设备切换不改变分割输入规格、mask 膨胀规则，GPU 与 CPU 的概率缓存保持隔离。

## 首次创建本地配置

```bash
cp .env.example .env
cp config.example.yaml config.yaml
```

正式部署包执行 `bash scripts/docker.sh pull` 和 `bash scripts/docker.sh up -d`，即可自动创建缺失文件并使用预构建镜像。源码开发可运行 `./scripts/bootstrap.sh` 或 `./scripts/docker.sh up --build`。脚本不会覆盖已存在的非空密码或本地设置。发布状态、版本选择和更新方式见 [Docker 部署](docker-release.md)。

## 迁移到另一台设备

1. 获取与原环境相同版本的正式部署包，或克隆仓库使用源码部署。
2. 单独安全复制原设备的 `config.yaml` 和 `.env`。
3. 如需保留项目，备份并恢复 PostgreSQL 数据库，同时复制 `data/` 和其中的 `.secret_key`；如需避免重新下载模型，也复制 `models/`。仅复制 `data/` 不会迁移 PostgreSQL 持久卷中的项目记录。
4. 在新设备启动 PostgreSQL、恢复数据库，再启动应用。部署包使用 `bash scripts/docker.sh up -d`；源码方式使用 `./scripts/docker.sh up --build -d`。备份方式见下文。

只复制 `config.yaml` 而不复制 `.env` 时，模型和运行参数仍可恢复，但云端翻译需要重新填写 API Key。

## Docker 挂载

Compose 将 `config.yaml` 和 `.env` 以文件形式挂载到后端容器，网页保存设置后会同步回宿主机。`data/` 与 `models/` 也使用 bind mount，重建容器不会主动删除它们。

真实配置已同时加入 `.gitignore` 与 `.dockerignore`，既不会进入 Git，也不会发送到 Docker build context。运行时挂载不受 `.dockerignore` 影响。

## 部署与访问

当前网页面向可信本机或受控局域网，没有用户登录和公网级权限隔离。Compose 默认通过 `MANGAFLOW_BIND_HOST=127.0.0.1` 绑定本机；可信局域网访问需显式修改绑定地址，并配置防火墙和反向代理鉴权。不要直接将未鉴权的业务接口暴露到互联网。

宿主机网页端口通过 `MANGAFLOW_WEB_PORT` 配置，默认 `8080`；后端对外端口通过 `MANGAFLOW_API_PORT` 配置，默认 `8000`。源码开发也使用 `MANGAFLOW_API_PORT`，前端另用 `MANGAFLOW_DEV_PORT`（默认 `5173`）。修改私有 `.env` 后重新启动对应服务。Docker 内部端口、数据库连接和健康检查不跟随宿主机端口变化。

网页、媒体、下载和 MCP 使用同一部署入口。MCP 设置页自动生成当前访问地址；远程 HTTPS 地址明确配置后优先使用，自动推导地址不写入数据库。旧 Agent 客户端需要更新保存的配置并重连。`MANGAFLOW_CORS_ORIGINS` 可显式覆盖，正常同源页面无需额外配置跨域来源。

远程 Agent 接入可以使用受保护的 MCP 与文件传输接口，按 [MCP 接入说明](mcp.md#接入与升级)设置 HTTPS 和密钥。网页与 MCP 共用后端、数据库、模型和任务队列；进度按任务来源区分，不代表存在两套处理服务。

`/media` 仅提供允许访问的漫画图片、历史图片及字体资产，数据库、配置和工程元数据不通过静态资源地址公开。

## 维护启动模式

迁移验证时可以通过环境变量设置 `MANGAFLOW_MAINTENANCE_MODE=true` 后启动新进程。该模式不执行数据库初始化或模型清单同步，不恢复任务和历史，不启动工作线程或资产清理。它保留已有排队、运行和暂停任务的数据库状态，不会提前调用云端服务。

维护模式下只有 `/health` 可访问，其 `status` 为 `maintenance`；其他 HTTP 请求返回 503，任务 WebSocket 关闭码为 1013。因此健康端点可达只表示维护进程存活，不表示业务已经就绪。数据库与文件校验需通过离线工具完成。

此开关不是热切换或自动迁移工具。先停止接收业务并安全排空旧进程，再启动维护实例；不能同时让旧实例继续写入同一数据库。验证通过后撤销开关并重新启动正常服务，才允许恢复队列。维护模式本身不备份、不迁移或回滚数据。

## PostgreSQL 数据库

项目只使用 PostgreSQL（psycopg 3）。默认 Docker Compose 已包含 PostgreSQL 18、内部网络及 `postgres-data` 持久卷，不再需要数据库覆盖文件或额外启用标记。数据库端口不对外发布，原有同名数据卷会继续使用。

`./scripts/docker.sh` 和 `./scripts/bootstrap.sh` 会为首次安装生成私有数据库密码；已配置的密码不会被覆盖。直接使用 Compose 时，需要先在私有 `.env` 中填写强随机的 `MANGAFLOW_POSTGRES_PASSWORD`。密码通过 `PGPASSWORD` 单独传给驱动，不拼进 URL。不要输出含密钥的 Compose 完整配置，也不要使用 `down -v` 删除业务数据库卷。

本地开发连接自行准备的 PostgreSQL：设置 `MANGAFLOW_DATABASE_URL=postgresql+psycopg://mangaflow@127.0.0.1:5432/mangaflow`，并通过进程环境提供 `PGPASSWORD`。不配置 URL 时也只会尝试此 PostgreSQL 地址，不会创建本地数据库文件。

数据库账号需要业务 schema 的读写/升级权限以及数据库的 `TEMPORARY` 权限；阶段工作副本使用连接私有临时表，不需要创建额外的持久化 schema。默认 Compose 创建的账号已满足这些条件。

正常启动使用 Alembic 自动升级 schema，维护模式不会启动模型、升级或恢复任务。已部署的数据库版本链保留；不支持破坏性 schema downgrade。

备份应同时保存数据库、项目图片、私有配置及原始 `data/.secret_key`。以下方式不经过宿主机的文本重定向，适用于 PowerShell 和 Bash：

```bash
./scripts/docker.sh exec postgres pg_dump -U mangaflow -d mangaflow -Fc -f /tmp/mangaflow-backup.dump
./scripts/docker.sh cp postgres:/tmp/mangaflow-backup.dump data/backups/<新的备份名>.dump
```

先创建宿主机备份目录，替换示例中的文件名，不覆盖已有备份。恢复使用 PostgreSQL 的 `pg_restore`，先在独立空库演练并核对项目数量、原图、文本、撤销历史及密钥可解密性，再安排正式维护窗口。备份不包含 Docker 外部目录中的图片和模型文件。

## 执行策略与推理诊断

“设置 → 性能与并发”可选择自动调节、固定上限或串行。默认页面任务上限和 CPU 工作线程数均为 4，实际放行数根据内存、CPU 负载和耗时调整。同页任务保持有序，云端请求遵守服务商限流；降低上限不会中断正在执行的任务。设置保存于 `data/execution-policy.json`。

页面任务池与 CPU 计算池独立：工作线程只计算并返回普通数据，页面任务负责数据库与撤销历史。模型加载及推理使用共享门禁，图片通过临时文件原子替换。提高页面并发不等于提高本地模型并发，也不保证所有任务都更快。

性能设置保存 GPU 并行上限（1–2）和显存预留（至少 2048 MB）。旧策略文件自动采用一路、2048 MB。上限不是绕过模型安全限制的开关：只有独立实例通过吞吐、P95 和显存门禁后，实际并行才可增加；当前本地模型共用单路门禁。CTD 与 MangaOCR 的批大小采用至少 `max(预留设置, 总显存的 15%)` 的安全余量。

执行状态只读取已有诊断，不为了显示设备而加载模型。它显示 CTD、MangaOCR、RF-DETR 和 LaMa 实际使用的后端与设备，CPU 回退原因不会被“配置为 CUDA”掩盖。

## 多模态大模型 OCR

在“设置 → OCR 识别 → 编辑”中，将 Provider 切换为“多模态大模型识字”。可以选择“使用当前翻译模型”，保存时由后端复制默认翻译模型的地址、协议、模型名和密钥；不会把密钥返回浏览器，也不会复制翻译 Prompt。复制后 OCR 与翻译配置独立，后续改动不自动同步。也可以独立填写 API Base URL、API Key，获取并选择模型；服务商没有模型列表时，可以手动输入完整模型 ID。所选模型必须支持图片输入。

支持 OpenAI Chat Completions、OpenAI Responses 和 Anthropic Messages 兼容协议；图片请求格式分别参考 [OpenAI 图片输入](https://developers.openai.com/api/docs/guides/images-vision)与 [Anthropic Vision](https://platform.claude.com/docs/en/build-with-claude/vision)。完整端点 URL 可以自动判断协议，也可以手动指定；Base URL 通常需要包含服务商要求的 `/v1` 前缀。

识别 Prompt、超时、失败重试和最大输出 Token 数都可配置，Temperature 可以留空使用模型默认值。系统会自动附加 `{"text":"原文"}` 输出约定。拒绝、截断或格式错误不会作为原文写入；识别结果没有可用置信度时，不伪造分数。

此模式仍使用本地文字检测和气泡关联结果，逐框上传裁剪图片（最长边最多 2048 px）并将识别结果写入原文，不直接翻译或重新划分气泡。**图片会发送给配置的服务商，并可能按区域请求产生费用。** 保存配置和启动检查不会上传图片；点击 OCR 后才会请求识别。OCR 与翻译的密钥独立，OCR 使用 `.env` 中的 `MANGAFLOW_OCR_API_KEY`，不会把密钥写入 `config.yaml`。安装此功能不会自动切换现有本地 OCR。

## 云端 OCR（通用接口）

此入口用于专用 OCR 服务，和上面的通用多模态大模型分开配置，但都只上传裁剪区域。切换 Provider 时会保留各自已保存的配置和密钥；保存后所选 Provider 成为当前 OCR，原有本地 OCR 配置也保留。

- **OCR 专用模型（Chat Completions）**：填完整识别地址（例如 `https://api.siliconflow.cn/v1/chat/completions`）、独立密钥和 OCR 模型名。通过标准 `image_url` 消息传入 PNG Data URL，使用 `max_tokens`，直接读取 `choices[0].message.content` 的纯文字，不要求模型输出 JSON。截断或拒绝不会写入原文。
- **JSON + Base64**：自定义图片字段名、纯 Base64 / Data URL 编码、非敏感附加参数和返回文字路径。
- **Multipart 文件上传**：自定义上传字段名和标量附加参数。图片文件名固定为 `region.png`。
- **鉴权与返回值**：支持 Bearer Token、自定义 API Key 请求头或无鉴权；`data.words.*.text` 可提取多个文本字段并按返回顺序换行，`result.0.text` 支持数组下标。密钥不能放入 URL 或附加参数；不会跟随重定向。

硅基流动的调用格式见[官方 Chat Completions 文档](https://api-docs.siliconflow.cn/docs/api/chat-completions-post)。`PaddlePaddle/PaddleOCR-VL-1.5` 的识别指令为 `OCR:`，见[官方模型卡](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.5)。`deepseek-ai/DeepSeek-OCR` 的纯文本识别指令为 `<image>`、换行、`Free OCR.`，见[官方多模态输入文档](https://docs.siliconflow.cn/docs/userguide/capabilities/multimodal-vision)。具体模型可用性以服务端模型列表为准。

“通用接口”并不意味着自动兼容所有厂商。需要签名算法、OAuth、查询参数鉴权、异步任务轮询或专有二进制协议的服务，需新增适配器或在前面提供兼容网关。专用 OCR 模型也可能采用视觉语言架构；这里按用途和输出协议区分，而不是按 HTTP 端点名称区分。

## 云端 OCR 并发调度

两种云端识别均可在配置窗口选择串行、固定或自适应并发。已有云端配置未设置这些字段时，默认使用自适应：从 2 路开始（不超过设定上限），默认上限 8，允许设置 1–16；每分钟最多发起 60 次请求，可按服务商配额调整为 1–6000。每个区域仍是独立请求，不额外上传图片探测上限。

- 连续成功后逐步增加；增加并发未提高估算吞吐时回退。HTTP 429、超时或服务端错误会降低自适应并发并统一退避，优先遵守 `Retry-After`。固定模式保持设定数量，但仍遵守速率和退避。
- 失败区域按“失败重试”次数重试，成功结果不重复请求；401/403 等不可重试错误停止后续派发。进度显示完成数、当前在途/允许并发数及退避倒计时。
- 同一个后端进程内，相同接口域名/端口、密钥及模型的 OCR 和翻译共享速率、并发额度与退避。翻译仍将一页待翻译文本合成一次请求。不同密钥的账号总额度、TPM/图片额度不能由此推断，应按服务商限制设置；多进程部署需外部共享限流器。
- 暂停或取消后不再派发新请求，等待已发请求结束后释放任务；已发送请求仍可能计费，取消后返回的文字不会写入。每个结果由主任务统一写库，并检查区域版本，手工修改、锁定或删除后不会被旧结果覆盖。
- 本地 OCR 保持串行，页面队列并发配置不变。学习值只保留在进程内，空闲 10 分钟重新从低并发开始，重启后重新学习。

## 环境变量

所有变量以 `MANGAFLOW_` 开头。常用变量见 `.env.example`：数据目录、模型目录、模型清单路径、云端翻译 API Key、自动配置开关、数据库 URL、CORS、上传大小、OCR 复核阈值、队列并发数与 Fernet 密钥。不要把真实 `.env` 或密钥提交到版本库。
