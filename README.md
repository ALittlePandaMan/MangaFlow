<h1 align="center">MangaFlow</h1>

<p align="center">本地优先的 AI 漫画翻译与嵌字工作台</p>

<p align="center">
  <a href="https://github.com/ALittlePandaMan/MangaFlow/actions/workflows/ci.yml"><img src="https://github.com/ALittlePandaMan/MangaFlow/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="VERSION"><img src="https://img.shields.io/badge/version-v0.1.2-blue" alt="版本 v0.1.2"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License"></a>
</p>

<p align="center">
  <a href="#快速开始">快速开始</a> ·
  <a href="#模型与来源">模型与来源</a> ·
  <a href="#mcp-自动翻译与复核">MCP 接入</a> ·
  <a href="docs/development.md">开发指南</a> ·
  <a href="https://github.com/ALittlePandaMan/MangaFlow/issues">问题反馈</a>
</p>

MangaFlow 将漫画导入、文字检测、OCR、翻译、去字修复、排版和导出整合在一个网页工作台中，主要面向日文漫画，也支持中英日混排及人工校对。你可以直接操作画布，也可以通过 MCP 让 Agent 执行批量翻译、看图复核和局部纠错。

原图保持只读，蒙版、净图、文字层和最终译图分别保存，支持撤销与继续编辑。检测、OCR 和修复可以在本机运行；选择云端 OCR 或翻译服务时，相应图片或文字会发送到配置的服务商。

**本项目使用 [Koharu](https://github.com/koharu-rs/koharu) 发布的 [Koharu Layout RF-DETR Seg 2XL](https://huggingface.co/mayocream/koharu-layout-rfdetr-seg-2xl-1152) 版面检测模型，并参考其漫画版面分析思路。** 其他模型的来源见下方说明。

## 核心功能

- **多格式导入**：支持 JPG、PNG、WebP，以及 ZIP、CBZ、RAR、CBR 和 PDF；漫画包按相对路径自然排序，PDF 默认以 300 DPI 转为图片。
- **批量识别与翻译**：检测文字、气泡和分镜，执行 OCR、上下文翻译及阅读顺序整理；可按项目、页面或区域处理，保留人工修改和锁定区域。
- **文字蒙版与背景修复**：围绕文字像素生成蒙版，用 LaMa 修复背景；支持画笔补蒙版、橡皮擦及局部重新修复。
- **画布精修**：原文框与译文框分别调整，支持移动、缩放、旋转、矩形、多边形、顶点编辑和透视，以及原图、净图、译图与对照视图。
- **中英日排版**：英文优先按单词换行，中日文标点禁则、竖排西文正立、字体回退、自动字号和自适应旋转；手动换行、字号与角度优先保留。
- **后台任务与历史**：任务查询、取消、失败重试及可恢复处理；已完成页面可以继续编辑，上传与导出进度统一显示在右上角“活动”列表。
- **多格式交付**：导出译图、净图、透明文字层、CBZ、分层 PSD，以及可重新导入继续编辑的工程 ZIP。
- **MCP 自动化**：官方工作流支持漫画导入、批量识别翻译、逐页视觉复核、局部纠错和导出；具备相应能力的客户端可使用子 Agent 并行复核。

PSD 普通区域提供可编辑文字层；透视、混合字体等特殊排版使用精确像素层并附隐藏文字备份。接收者需要安装对应字体；已验证文件结构与合成预览，实际 Photoshop 编辑兼容性仍需人工实测，详见[导出说明](docs/development.md#常用检查)。

## 快速开始

### 1. 准备运行环境

当前默认 Docker 配置使用 **NVIDIA GPU / CUDA 12.6**，需要兼容的显卡驱动，以及支持 Compose `gpus` 配置的 Docker Compose 版本。

- **Windows**：使用 WSL2 和 Docker Desktop，启用 WSL 集成；在 WSL 的 Bash 终端执行下方命令。
- **Linux**：安装 Docker、Docker Compose 和 NVIDIA Container Toolkit。
- **CPU / 其他环境**：按[本地模型依赖说明](docs/development.md#本地模型依赖)安装匹配硬件的依赖。当前默认 Compose 包含 `gpus: all`，需要 GPU 运行环境。

### 2. 拉取镜像并启动

**预构建镜像尚未就绪：首个正式镜像仍待发布并完成匿名拉取验证。** 发布后，从 [Releases](https://github.com/ALittlePandaMan/MangaFlow/releases) 下载对应版本的 `mangaflow-docker-版本号.zip`，解压后进入部署目录。该安装包只包含公开配置与启动脚本，无需克隆源码或本地构建。

以 `0.1.2` 部署包为例，在 Bash 中执行：

```bash
cd mangaflow-docker-0.1.2
bash scripts/docker.sh pull
bash scripts/docker.sh up -d
```

打开 [http://localhost:8080](http://localhost:8080)。首次构建和模型准备可能较慢，可查看状态与日志：

```bash
bash scripts/docker.sh ps
bash scripts/docker.sh logs -f backend
```

启动脚本自动创建缺失的 `config.yaml`、`.env` 和私有数据库密码。在 WSL 中会调用 Windows Docker 客户端，以正确挂载项目目录。PostgreSQL 由 Compose 启动；项目记录保存在数据库卷，图片和模型分别保存在 `data/`、`models/`。备份与换机需同时保存数据库、图片和私有配置，见[配置与迁移](docs/configuration.md)。

部署包固定前后端镜像版本。更新时先备份，再修改私有 `.env` 中的 `MANGAFLOW_IMAGE_TAG` 为已发布的新版本，重新执行 `pull` 和 `up -d`。源码构建见[开发指南](docs/development.md#快速开始)，镜像发布与升级见 [Docker 部署说明](docs/docker-release.md)。

默认仅监听本机 `127.0.0.1`；当前网页没有用户登录与公网权限隔离，远程部署请先阅读[部署与访问](docs/configuration.md#部署与访问)。

### 3. 安装模型并配置翻译

1. 在网页点击 **“安装推荐配置”** 并确认，准备 RF-DETR、PaddleOCR-VL 1.6、LaMa 和 Pillow。已完整安装时会提示“配置已安装”。首次下载需能访问对应模型仓库。
2. 在 **“设置 → 云端翻译”** 选择翻译服务。使用自定义接口时填写地址、模型和 API Key，再测试连接并保存；也可选择 Google 网页翻译，或配置本地兼容服务。
3. 在项目中选择目标语言与字体。镜像包含 Noto Sans CJK，也可上传需要的字体。

推荐安装负责本地检测、识字、修复和排版；**翻译服务需要单独配置**。模板中的 `example.com`、`example-model` 是占位值，`passthrough` 仅保留原文并标记待复核。

### 4. 开始翻译

1. 新建项目，通过“添加图片”选择或拖入图片、漫画包、PDF；MangaFlow 工程包使用独立的工程导入入口。
2. 批量检测与 OCR，核对误检、漏检和原文；必要时补框、拆框或修正文字。
3. 执行翻译、去字修复和排版，对照原图检查译文、残影、画面及文字溢出。
4. 调整文字和蒙版，重新处理受影响区域，确认后导出 CBZ、图片包、PSD 或工程包。

加密或分卷漫画包暂不支持。导入限制、PSD 字体和导出细节见[开发指南](docs/development.md#常用检查)，修复与排版的工作方式见[翻译质量说明](docs/translation-quality.md)。

## 模型与来源

检测、OCR、翻译和修复可以分别选择。以下为当前支持的主要本地模型及渲染组件：

| 用途 | 模型或组件 | 上游来源与接入方式 |
| --- | --- | --- |
| 版面检测 | Koharu Layout RF-DETR Seg 2XL | [mayocream / Koharu 权重](https://huggingface.co/mayocream/koharu-layout-rfdetr-seg-2xl-1152)，检测文字、拟声词、气泡与分镜 |
| OCR | PaddleOCR-VL 1.6 | [PaddlePaddle/PaddleOCR-VL-1.6](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6)，部署模板与推荐安装的默认项 |
| OCR | MangaOCR | [kha-white/manga-ocr-base](https://huggingface.co/kha-white/manga-ocr-base)，可选日文漫画识字模型 |
| OCR | Baberu OCR | [genshiai-daichi/baberu-ocr](https://huggingface.co/genshiai-daichi/baberu-ocr)，可选本地识字模型 |
| OCR | Hayai OCR v2 | [JustANormalTinkerer/hayai-ocr-v2](https://huggingface.co/JustANormalTinkerer/hayai-ocr-v2)，可选本地识字模型 |
| 字形分割 | Comic Text Detector | [comic-text-detector](https://github.com/dmMaze/comic-text-detector)，使用独立下载的 ONNX 权重提取文字像素 |
| 背景修复 | LaMa | [advimman/lama](https://github.com/advimman/lama)，通过 [simple-lama-inpainting](https://github.com/enesmsahin/simple-lama-inpainting) 接入 |
| 排版渲染 | Pillow | [python-pillow/Pillow](https://github.com/python-pillow/Pillow)，结合本项目的文字布局实现 |

PaddleOCR-VL、Baberu 和 Hayai 使用与 Koharu 对齐的上游检查点，在 MangaFlow 的 Python/PyTorch 环境中推理。**这些 OCR 模型分别由其上游作者提供。** 新安装统一推荐 PaddleOCR-VL 1.6；已有 MangaOCR 等模型选择继续保留，升级不会自动切换 OCR 或重新识别页面。

翻译支持自定义兼容接口，包括 Chat Completions、Responses 和 Anthropic Messages 协议，也可对接 Ollama、vLLM、LM Studio 等本地服务；Docker 访问宿主机服务时，地址需使用容器可达的主机名。另有 Google 网页翻译选项，需要可用网络，可能受到限流，且不使用项目上下文。OCR 还可选择普通 PaddleOCR、Tesseract 或云端识字服务。

模型权重按需单独下载并缓存，不随本仓库分发。RF-DETR 及新增本地 OCR 使用固定版本；来源、校验和安装方式见[版面检测](docs/text-classification.md)、[本地 OCR](docs/local-ocr.md)及[字形分割](docs/translation-quality.md#可选像素模型的安装与来源)。

## MCP 自动翻译与复核

在 **“设置 → MCP”** 查看安装指令与连接配置，默认启用。官方工作流采用：

**批量检测、OCR、翻译 → 同时复核已完成页 → 集中纠错 → 批量修复、排版 → 复核译图 → 局部修正与导出。**

| 连接方式 | 适用场景 | 本机文件导入 |
| --- | --- | --- |
| 本地连接程序（stdio） | Agent 处理本机文件，连接本机或远程 MangaFlow | `local_import` 接受 Agent 能访问的明确目录、图片、漫画包或 PDF，自动上传并按名称创建项目 |
| HTTP | 处理服务端已有项目或已上传资产 | 可提交导入资产；读取客户端磁盘需使用本地连接程序 |

默认 HTTP 地址为 `http://localhost:8080/mangaflow`，兼容地址 `/mcp` 保留。可将下面的请求交给 Agent：

```text
请读取 http://localhost:8080/mangaflow/setup，按官方说明安装 MangaFlow
本地 MCP 连接程序并配置当前客户端。重新连接后确认 local_import 可用，
将我指定的漫画目录按文件夹名导入新项目，读取 workflow_get，按官方工作流
完成翻译、逐页看图复核和必要纠错，最后导出 CBZ 及复核报告到指定目录。
```

使用远程服务时替换地址，并按接入文档配置认证。本地连接程序需要 Node.js 20.11+；图片查看和子 Agent 能力取决于客户端及所选模型。官方接口面向标准 MCP 客户端，**WorkBuddy 等客户端的实际多模态复核与子 Agent 能力仍需分别实测**，当前协议测试不代表视觉质量已经通过。

服务端提供 20 个翻译工具，本地程序增加 4 个传输工具。Agent 可以修正 OCR、译文、原文框、译文框和局部蒙版；模型安装与系统管理在网页完成。网页与 MCP 共用项目、模型和队列，网页仅展示网页发起的任务进度；MCP 任务通过工具查询。完整流程、版本保护及验收记录见 [MCP 接入文档](docs/mcp.md)。

可让 Agent 读取官方 [漫画翻译 Skill](.agents/skills/mangaflow-translate/SKILL.md)，按当前 MCP 工作流执行；Skill 的加载方式见 [Agent 资源说明](.agents/README.md)。

## 开发与文档

前端使用 React、TypeScript 和 Konva，后端使用 FastAPI、Python/PyTorch 与 PostgreSQL。开发环境需要 Python 3.11／3.12、Node.js 20.11+ 和可连接的 PostgreSQL。

```bash
./scripts/bootstrap.sh
# 按配置文档设置 MANGAFLOW_DATABASE_URL 和 PGPASSWORD 后启动
./scripts/dev.sh
```

前端地址为 `http://localhost:5173`，API 文档为 `http://localhost:8000/docs`。完整依赖、隔离模型环境和检查命令见[开发指南](docs/development.md)。

使用编程 Agent 开发时，从 [AGENTS.md](AGENTS.md) 和 [开发 Skill](.agents/skills/mangaflow-development/SKILL.md) 了解仓库规则与验证入口。

| 文档 | 内容 |
| --- | --- |
| [架构说明](docs/architecture.md) | 模块边界、流水线、数据与历史资产 |
| [配置与迁移](docs/configuration.md) | 模型配置、密钥、云端 OCR、并发、备份与部署 |
| [翻译质量](docs/translation-quality.md) | 蒙版、背景保护、上下文、排版及能力边界 |
| [版面检测](docs/text-classification.md) | Koharu RF-DETR、阅读顺序及安装 |
| [本地 OCR](docs/local-ocr.md) | 可选识字模型、固定检查点及验证 |
| [MCP 接入](docs/mcp.md) | 安装、工具、自动翻译和多 Agent 协作 |
| [常见问题](docs/troubleshooting.md) | 模型加载、GPU、翻译与数据库排查 |

## 常见问题

- **首次启动或模型加载较慢**：检查网络与模型缓存权限，通过 `./scripts/docker.sh logs -f backend` 查看准备进度；已下载权重会复用。
- **译文仍是原文**：检查翻译服务是否已配置，避免使用示例地址或 `passthrough` 占位配置。
- **缺少中文字形或文字溢出**：选择包含目标字符的字体，检查译文框、字号、间距和自动字号设置。
- **原文有残影或画面误擦**：对照原图修改蒙版；遗漏笔画可补画蒙版，再局部修复。生成修复仍需人工或 Agent 复核。
- **Agent 无法导入本机目录**：使用官方本地连接程序，确认 `mcp_status` 返回 `local_import` 能力；HTTP 连接可处理已有项目或资产。

其他问题请查阅[排查文档](docs/troubleshooting.md)，或提交包含复现步骤及脱敏日志的 [Issue](https://github.com/ALittlePandaMan/MangaFlow/issues)。

## 致谢与许可证

感谢 [Koharu](https://github.com/koharu-rs/koharu) 团队公开漫画版面检测模型和实现思路，以及 RF-DETR、PaddleOCR、MangaOCR、Baberu、Hayai、Comic Text Detector、LaMa 和相关依赖的作者。

MangaFlow 源码使用 [MIT License](LICENSE)。第三方代码、字体、模型权重及训练数据遵循各自条款；Koharu RF-DETR 模型涉及 Manga109 衍生训练数据，具体授权见[模型卡](https://huggingface.co/mayocream/koharu-layout-rfdetr-seg-2xl-1152#license-and-training-data-terms)。本项目的 MIT 许可证不替代这些上游条款。
