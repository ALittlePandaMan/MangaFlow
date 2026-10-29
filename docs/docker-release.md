# Docker 部署与正式发布

## 用户安装

预构建镜像与部署包已正式发布，并通过匿名拉取、启动及持久化验证。从 [Releases](https://github.com/ALittlePandaMan/MangaFlow/releases) 下载 `mangaflow-docker-版本号.zip`，解压并在其目录的 Bash 中执行：

```bash
bash scripts/docker.sh pull
bash scripts/docker.sh up -d
```

无需 Git、Python、Node.js 或编译工具。部署包使用 Linux amd64 镜像，需要 Docker Compose 2.30+；Windows 通过 WSL2 与 Docker Desktop 运行。默认 GPU 模式需要兼容的 NVIDIA 驱动及容器 GPU 访问，Linux 使用 NVIDIA Container Toolkit；明确选择 CPU 模式无需 NVIDIA 依赖。模型权重另行下载到 `models/`，镜像内只有应用和模型运行依赖。

正式镜像地址：

| 服务 | 镜像 |
| --- | --- |
| 后端 | `ghcr.io/alittlepandaman/mangaflow-backend` |
| 前端 | `ghcr.io/alittlepandaman/mangaflow-frontend` |

每个部署包默认固定同一个正式版本。`latest` 供希望跟随最新稳定版的用户使用；默认安装保持版本固定，避免拉取时混入其他版本。

Agent 先按[部署指南](agent-deployment.md)检查实际目标主机；无可用 NVIDIA GPU 时不推荐本地批量部署，用户接受 CPU 性能后才继续。CPU 预构建镜像从 **0.1.4** 起提供，以对应 Release 完成为准；0.1.3 没有 `0.1.3-cpu` 标签。

私有 `.env` 的 `MANGAFLOW_RUNTIME` 只接受 `gpu`（默认）或 `cpu`，进程环境优先，不自动降级。CPU 模式加载 `docker-compose.cpu.yml` 覆盖文件，后端使用同一 GHCR 包的 `<版本>-cpu`，前端仍用 `<版本>`；稳定 CPU 别名为 `latest-cpu`。CPU 镜像不含 CUDA 运行依赖，但保留本地模型功能与独立运行环境，权重另行下载。源码构建目录使用对应 CPU Dockerfile，正式部署包仅引用镜像。

模式切换保留原目录、服务名、Compose 项目名、数据库卷、端口和私有配置；选择模式后再执行 `pull`／`up -d`，不更改已有 OCR、翻译或字体配置。当前仅支持 Linux amd64，不自动模拟 ARM64。

默认模型为 RF-DETR、**PaddleOCR-VL 1.6**、LaMa 和 Pillow。网页“安装推荐配置”负责准备本地组件，翻译服务另行设置；MangaOCR 和其他 OCR 继续可选。升级只补齐缺失配置，不覆盖用户的模型选择或人工修改。

初始化使用部署包内的 `.env.example`、`config.example.yaml` 和现有 Bash 脚本，生成本机配置及数据库密码。私有 `.env` 中的 `MANGAFLOW_IMAGE_TAG` 控制两个应用镜像的版本。PostgreSQL 数据库卷、`data/`、`models/` 和配置文件持续保留。

在私有 `.env` 中配置 `MANGAFLOW_WEB_PORT`（默认 `8080`）和 `MANGAFLOW_API_PORT`（默认 `8000`）即可修改宿主机端口，然后执行 `up -d`。例如设置为 `18080`／`18000`，网页与 MCP 共用 `http://localhost:18080`，后端对外端口为 `18000`；内部通信和健康检查仍使用容器端口。源码 Compose 与发布部署包使用相同变量。MCP 客户端保存的旧地址需要更新并重连。

`MANGAFLOW_CORS_ORIGINS` 采用用户显式配置，Compose 不再固定覆盖。正常网页通过同源代理访问，无需因修改端口添加跨域白名单；确需跨域调用时填写完整且明确的来源，不使用通配符。

## 更新与备份

1. 按[数据库备份说明](configuration.md#postgresql-数据库)备份 PostgreSQL，同时保存 `data/`、`.env`、`config.yaml` 及密钥。
2. 阅读目标版本 Release 说明。普通应用更新在原目录的 `.env` 中修改 `MANGAFLOW_IMAGE_TAG`，前后端始终使用同一个版本。
3. 执行 `pull` 后再 `up -d`，检查 `bash scripts/docker.sh ps` 与日志。不支持通过降级旧镜像回退已经执行的数据库迁移。
4. 若新版本要求修改 Compose，停止旧应用后在原目录合并新的部署文件，保留原数据库卷、挂载目录及私有配置；不要在另一目录重新初始化代替迁移。

**0.1.2 → 0.1.3** 需要同步更新部署文件和镜像：备份后，在原目录合并 0.1.3 部署包的 `docker-compose.yml`、`scripts/`，将私有 `.env` 的 `MANGAFLOW_IMAGE_TAG` 改为 `0.1.3`，按需加入端口变量，然后执行 `pull` 和 `up -d`。保留原 `.env`、`config.yaml`、数据和卷。新增端口变量从 0.1.3 开始生效；旧 Compose 仍写死端口。更新后从设置页复制实际地址，更新旧 MCP 客户端或重新运行安装器并重连。

**升级到 0.1.4**：备份后，在原目录合并新部署包的 `docker-compose.yml`、`docker-compose.cpu.yml` 和 `scripts/`，设置 `MANGAFLOW_IMAGE_TAG=0.1.4`，按实际需要设置 `MANGAFLOW_RUNTIME=gpu|cpu`，再执行 `pull`、`up -d`。CPU 模式会覆盖运行时设备选择而保留原模型设备偏好；切回 GPU 后重建服务即可恢复。只执行 `restart` 不会更新镜像或容器环境。

`docker-compose.yml` 保留源码构建；`docker-compose.release.yml` 只引用镜像。打包脚本将正式 Compose 写入部署包的 `docker-compose.yml`，同一启动脚本可用于源码目录和部署包，并根据显式的 `MANGAFLOW_RUNTIME` 加载 CPU 覆盖文件。源码目录切换模式时使用 `up --build -d`，CPU 构建使用独立本地镜像标签，避免切回 GPU 时复用 CPU 镜像。

## 维护者发布

发布只由 `0.1.2` 格式的正式 Git 标签触发，不在每次更新 main 时构建。标签必须与 `VERSION`、前端版本一致。

1. 提交并推送待发布代码。标签创建前确认当前版本的变更已经准备好。
2. 推送对应正式版本标签，Release 工作流重新执行同一提交的现有 CI。
3. GPU 后端、CPU 后端和共享前端分别构建为暂存镜像并记录 digest；任一失败均不会公开正式 Release 或更新稳定入口。CPU 构建检查三个 Python 环境均使用 CPU PyTorch，禁止重新引入 NVIDIA、GPU Paddle、GPU ONNX Runtime 等依赖。
4. 工作流匿名拉取全部暂存镜像，分别通过独立 CPU／GPU 配置，以临时 Compose 项目、数据目录和非默认端口检查网页、健康接口、20 个 MCP 工具与重建持久化。普通 Linux runner 的 GPU 镜像只验证无模型启动；CPU 验证不通过额外覆盖移除 GPU 请求，并执行 CPU 依赖检查。此发布 smoke 不下载或推理真实模型，不代替模型质量验证。
5. 全部验证成功后，按 digest 推广 `<版本>`、`<版本>-cpu` 与前端版本标签，上传公开部署 ZIP 和 SHA-256 校验文件，再更新适用的 `latest`、`latest-cpu` 并公开 Release；发布较旧版本不会降级稳定入口。

GHCR 首次创建的包默认私有。第一次构建完成后，维护者需在两个包的 Package settings 中将 Visibility 改为 Public。否则匿名拉取检查明确失败，稳定标签和正式 Release 不会发布。改为公开后重跑工作流即可，不需要向用户提供 GitHub 凭据。[GitHub Container Registry 说明](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry#pushing-container-images)

已完整发布的相同版本再次运行会直接跳过，保留原镜像与附件。中断后存在的草稿可以重新完成；失败构建的暂存标签不作为用户安装入口。不要移动已发布版本的 Git 标签。正式发布成功后，同步 README 与本文的部署状态。

若原版本的 CI 与所需镜像构建均成功，但后续验证脚本失败，可在修复验证脚本后运行 Actions 的 **Recover release**，填写原版本和原 Release 的运行 ID。恢复流程核对原运行、Git 标签和提交一致，按原提交是否包含 CPU 配置要求两份或三份构建成功，复用其 digest 附件及镜像；部署包仍从原标签生成，使用修正后的隔离验证脚本重新检查，通过后再发布。旧版两镜像发布仍兼容；新版不能遗漏 CPU 产物。无需移动标签或重新构建模型镜像；原运行的 digest 附件必须尚未过期。

Linux 隔离验证预先创建宿主机挂载目录，并在停止测试容器后恢复临时数据的所有权，解决容器生成 root 所有文件导致的持久化写入及清理失败。此处理仅作用于随机测试 Compose 项目，不更改实际部署的数据权限。

可在本地生成并检查部署包，不触发上传：

```bash
python scripts/package_release.py --output-dir dist/release
```

打包只读取固定公开文件，不遍历漫画、模型或私有配置。ZIP 含 Compose、公开模板、启动脚本、版本、部署说明及许可证，解压后仍需通过初始化创建自己的密码。

## 验证记录

2026-10-27：35 项发布、模板和初始化回归通过；Actionlint、Ruff、Bash 语法及 Compose 校验通过。使用本机已有应用镜像在独立临时目录和数据库卷启动，验证了网页、健康接口、20 个 MCP 工具，以及重建容器后的项目、配置、图片目录和模型目录持久化，原有服务保持运行。

上述本地验收时尚未执行 GHCR 构建上传或真实模型推理，当时两个 `0.1.2` 镜像匿名访问返回 HTTP 403。

2026-10-28：原 `0.1.2` 标签的完整 CI 和两份镜像构建均通过。修复 Linux 隔离验证的目录所有权问题后，50 项针对性回归、Actionlint、Ruff 及本地隔离部署验证通过。[恢复发布任务](https://github.com/ALittlePandaMan/MangaFlow/actions/runs/40623695712)在 GitHub Linux runner 上完成两份镜像的匿名完整拉取、网页、健康接口、20 个 MCP 工具及重建后的持久化验证，并发布了 `0.1.2`、稳定版 `latest` 和部署 ZIP／SHA-256 附件。公开下载的部署包校验和与文件白名单已核对；本次未运行真实模型翻译。

同日的新 CI 使用 pytest 9.2，暴露了限流测试回滚 Starlette 动态状态的问题，以及一项导出测试对数据库返回顺序的错误假设。两处测试已修正，并在 pytest 9.2 下通过对应回归；应用运行逻辑和已发布镜像保持不变。
