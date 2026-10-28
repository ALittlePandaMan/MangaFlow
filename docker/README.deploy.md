# MangaFlow Docker {{VERSION}}

本部署包使用正式预构建镜像，支持 Linux amd64／NVIDIA CUDA。Windows 使用 WSL2 与 Docker Desktop；Linux 需要 NVIDIA Container Toolkit。运行环境需支持 Compose 的 `gpus` 配置。

在解压目录的 Bash 终端运行：

```bash
bash scripts/docker.sh pull
bash scripts/docker.sh up -d
```

默认打开 http://localhost:8080。初始化脚本自动创建私有 `.env`、数据库密码及 `config.yaml`，不会覆盖已有文件。

自定义端口时，在 `.env` 中设置 `MANGAFLOW_WEB_PORT=18080`（网页及 MCP）和 `MANGAFLOW_API_PORT=18000`（后端对外端口），再运行 `bash scripts/docker.sh up -d`。此时网页为 `http://localhost:18080`，MCP 地址与安装链接跟随网页端口；容器内部仍使用 `80`／`8000`。旧 Agent 客户端需从设置页重新复制配置或运行安装器，并重新连接。

首次模型准备较慢时可运行：

```bash
bash scripts/docker.sh ps
bash scripts/docker.sh logs -f backend
```

默认推荐 RF-DETR、**PaddleOCR-VL 1.6**、LaMa 和 Pillow；在网页“安装推荐配置”中准备模型，翻译服务另行配置。模型权重单独下载，镜像内不包含漫画或用户数据。MangaOCR 等其他 OCR 可在设置中选择。

## 更新

先备份 PostgreSQL 数据库、`data/`、`config.yaml` 和 `.env`。在原部署目录的私有 `.env` 中将 `MANGAFLOW_IMAGE_TAG` 设置为已发布的新版本，再执行上方的 `pull` 和 `up -d`。不要在另一个目录重新初始化代替更新，不要删除 `postgres-data` 数据库卷。更新保留已有模型选择和人工编辑。

从 0.1.2 升级到 0.1.3 时，还需将本包的 `docker-compose.yml` 和 `scripts/` 合并到原部署目录；保留原私有配置、数据和数据库卷。新的端口变量由 0.1.3 的部署文件提供，MCP 自动地址由新版应用提供，二者需一起更新。部署端口变化后，更新已安装 Agent 的 MCP 配置并重新连接；连接程序可通过新地址的 `/mangaflow/setup` 更新。

项目记录保存于 PostgreSQL 卷，图片保存在 `data/`，模型缓存保存在 `models/`。只复制图片目录不能完整迁移项目。默认只绑定本机地址，远程部署需自行配置 HTTPS、鉴权和防火墙。

检测使用 [Koharu](https://github.com/koharu-rs/koharu) 发布的 [RF-DETR 权重](https://huggingface.co/mayocream/koharu-layout-rfdetr-seg-2xl-1152)，OCR 来自 [PaddleOCR](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6)。源码使用 MIT，第三方模型及数据遵循各自条款。

完整文档、备份步骤与 MCP 接入见 [MangaFlow](https://github.com/ALittlePandaMan/MangaFlow)。
