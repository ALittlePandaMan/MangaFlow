# Docker 部署与正式发布

## 用户安装

预构建镜像尚未就绪，首次正式发布与匿名拉取仍待验证。发布后，从 [Releases](https://github.com/ALittlePandaMan/MangaFlow/releases) 下载 `mangaflow-docker-版本号.zip`，解压并在其目录的 Bash 中执行：

```bash
bash scripts/docker.sh pull
bash scripts/docker.sh up -d
```

无需 Git、Python、Node.js 或编译工具。部署包使用 Linux amd64 镜像；Windows 通过 WSL2 与 Docker Desktop 运行，Linux 安装 Docker Compose 和 NVIDIA Container Toolkit。默认需要 NVIDIA CUDA GPU，并支持 Compose `gpus` 配置。模型权重另行下载到 `models/`，镜像内只有应用和模型运行依赖。

正式镜像地址：

| 服务 | 镜像 |
| --- | --- |
| 后端 | `ghcr.io/alittlepandaman/mangaflow-backend` |
| 前端 | `ghcr.io/alittlepandaman/mangaflow-frontend` |

每个部署包默认固定同一个正式版本。`latest` 供希望跟随最新稳定版的用户使用；默认安装保持版本固定，避免拉取时混入其他版本。

默认模型为 RF-DETR、**PaddleOCR-VL 1.6**、LaMa 和 Pillow。网页“安装推荐配置”负责准备本地组件，翻译服务另行设置；MangaOCR 和其他 OCR 继续可选。升级只补齐缺失配置，不覆盖用户的模型选择或人工修改。

初始化使用部署包内的 `.env.example`、`config.example.yaml` 和现有 Bash 脚本，生成本机配置及数据库密码。私有 `.env` 中的 `MANGAFLOW_IMAGE_TAG` 控制两个应用镜像的版本。PostgreSQL 数据库卷、`data/`、`models/` 和配置文件持续保留。

## 更新与备份

1. 按[数据库备份说明](configuration.md#postgresql-数据库)备份 PostgreSQL，同时保存 `data/`、`.env`、`config.yaml` 及密钥。
2. 阅读目标版本 Release 说明。普通应用更新在原目录的 `.env` 中修改 `MANGAFLOW_IMAGE_TAG`，前后端始终使用同一个版本。
3. 执行 `pull` 后再 `up -d`，检查 `bash scripts/docker.sh ps` 与日志。不支持通过降级旧镜像回退已经执行的数据库迁移。
4. 若新版本要求修改 Compose，停止旧应用后在原目录合并新的部署文件，保留原数据库卷、挂载目录及私有配置；不要在另一目录重新初始化代替迁移。

`docker-compose.yml` 保留源码构建；`docker-compose.release.yml` 只引用镜像。打包脚本将正式 Compose 写入部署包的 `docker-compose.yml`，因此相同启动脚本无需识别运行模式。已有源码部署可继续使用原流程。

## 维护者发布

发布只由 `0.1.2` 格式的正式 Git 标签触发，不在每次更新 main 时构建。标签必须与 `VERSION`、前端版本一致。

1. 提交并推送待发布代码。标签创建前确认当前版本的变更已经准备好。
2. 推送对应正式版本标签，Release 工作流重新执行同一提交的现有 CI。
3. 前后端分别构建为暂存镜像并记录 digest；任一失败均不会更新版本标签、`latest` 或正式 Release。
4. 工作流使用没有 Docker 登录配置的环境拉取两个暂存镜像，以独立 Compose 项目、临时目录和随机端口检查网页、健康接口、20 个 MCP 工具、数据库与配置的重建持久化。此检查不下载或推理真实模型，也不验证实际翻译质量。
5. 验证成功后按 digest 推广两个版本标签，上传白名单部署 ZIP 和 SHA-256 校验文件。两个镜像均准备好后再更新 `latest` 并公开 Release；发布较旧版本不会将 `latest` 降级。

GHCR 首次创建的包默认私有。第一次构建完成后，维护者需在两个包的 Package settings 中将 Visibility 改为 Public。否则匿名拉取检查明确失败，稳定标签和正式 Release 不会发布。改为公开后重跑工作流即可，不需要向用户提供 GitHub 凭据。[GitHub Container Registry 说明](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry#pushing-container-images)

已完整发布的相同版本再次运行会直接跳过，保留原镜像与附件。中断后存在的草稿可以重新完成；失败构建的暂存标签不作为用户安装入口。不要移动已发布版本的 Git 标签。首次正式发布成功后，更新 README 与本文的“尚未就绪”状态。

可在本地生成并检查部署包，不触发上传：

```bash
python scripts/package_release.py --output-dir dist/release
```

打包只读取固定公开文件，不遍历漫画、模型或私有配置。ZIP 含 Compose、公开模板、启动脚本、版本、部署说明及许可证，解压后仍需通过初始化创建自己的密码。

## 验证记录

2026-10-27：35 项发布、模板和初始化回归通过；Actionlint、Ruff、Bash 语法及 Compose 校验通过。使用本机已有应用镜像在独立临时目录和数据库卷启动，验证了网页、健康接口、20 个 MCP 工具，以及重建容器后的项目、配置、图片目录和模型目录持久化，原有服务保持运行。

本轮未执行 GHCR 构建上传或真实模型推理。两个 `0.1.2` 镜像的匿名访问仍返回 HTTP 403，不能视为已公开可用；正式镜像构建、匿名完整拉取及发布后验收由上述工作流继续执行。
