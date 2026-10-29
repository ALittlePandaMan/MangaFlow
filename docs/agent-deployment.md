# Agent 部署 MangaFlow

先检查**实际部署主机**，再选择运行方式。部署服务器时检查服务器；使用远程 Docker context 时同时核对 Docker 服务端，不能用 Agent 本机的显卡代替目标主机信息。

## 1. 先确认硬件与运行环境

记录操作系统、CPU 架构与型号、可用核心、内存、剩余磁盘、NVIDIA 显卡型号与显存，以及 Docker 服务端和 Compose 版本。容器或 WSL 有资源限制时记录实际分配值。不要输出整个环境变量或私有配置。

- Windows：可用 PowerShell `Get-CimInstance Win32_Processor`、`Get-CimInstance Win32_ComputerSystem`、`Get-CimInstance Win32_VideoController`、`Get-PSDrive -PSProvider FileSystem` 查询；检查 WSL2、Docker Desktop 和 WSL 集成。
- Linux：可用 `uname -m`、`lscpu`、`free -h`、`df -h`、`lspci` 查询。缺少某个命令不代表缺少硬件，可用系统已有的其他查询方式。
- NVIDIA：运行 `nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader`。命令缺失或失败时结合系统显卡清单区分“无 NVIDIA”与“驱动未就绪”。
- Docker：运行 `docker version`、`docker info --format '{{.OSType}}/{{.Architecture}}'`、`docker compose version`，确认客户端连接的是预期目标。当前 Docker 路径要求 Linux amd64 服务端与 Compose 2.30+，不自动启用 ARM64 模拟运行。
- GPU 容器访问：主机查询成功后，还需验证容器可访问 GPU。优先复用已有合适镜像；必要时用小型探测容器，例如 `docker run --rm --gpus all ubuntu:24.04 nvidia-smi`。不要先下载整个 MangaFlow GPU 镜像再判断主机有没有显卡。

系统有 AMD、Intel 显卡或 Apple GPU，不等于当前 NVIDIA CUDA 路径可用。不凭未验证的显存阈值保证模型能运行；显存不足、资源受限时应说明风险，并在部署后先用一页验证。

## 2. 根据证据选择

| 检查结果 | Agent 的下一步 |
| --- | --- |
| NVIDIA GPU、驱动和容器访问均可用 | 推荐 GPU 部署，使用 `MANGAFLOW_RUNTIME=gpu` |
| 有 NVIDIA GPU，驱动或容器访问失败 | 说明具体缺项，优先修复驱动／NVIDIA Container Toolkit／WSL 透传；不能直接称为“没有 GPU”或静默改成 CPU |
| 已确认没有可用 NVIDIA GPU | 明确不推荐本地批量部署，提醒检测、蒙版、修复耗时较长；得到用户明确接受 CPU 性能的答复后再下载和启动 |
| 远程信息不足、权限不足、命令结果不明 | 标记“硬件尚未确认”，获取缺少的信息后再选模式，不能直接判定无 GPU |
| 用户已知晓性能影响并明确要求 CPU 部署 | 直接执行 CPU 路径，不重复询问 |

无 NVIDIA 时可使用以下说明，按已掌握的信息填写，不捏造硬件或耗时：

> 当前目标主机未检测到可用 NVIDIA GPU，不推荐用它批量处理漫画。检测、文字蒙版和背景修复会主要由 CPU 计算，等待时间可能较长。项目的一组 10 页实测中，Xeon E5-2698B v3 的 CPU 四阶段合计约 12 分 38 秒，V100 16GB 约 2 分 7 秒；不含 OCR 和翻译，不代表本机固定耗时。默认本地 OCR 还会增加时间。若接受这些限制，可以继续 CPU 部署。

该实测使用 10 张 1280×1807 图片、114 个固定文本区域、预热后串行处理和当前默认线程配置；CPU 平均检测约 13 秒／页，GPU 约 0.6 秒／页。不要把该倍率作为所有硬件的速度承诺。云端 OCR 可以减少本地负担，不能消除检测和修复的 CPU 开销。

确认放在 Agent 对话中；脚本没有交互式确认框，也不会自动从 GPU 降级为 CPU。翻译任务本身不包含部署授权。

## 3. 执行选定模式

CPU 预构建镜像从 **0.1.4** 起提供，以对应 Release 完成、镜像可匿名拉取为准；0.1.3 只有 GPU 镜像，不能使用 `0.1.3-cpu`。Agent 应按实际 Release 提供的文件和标签操作；没有所需镜像时明确说明，按用户允许的范围使用源码构建，不伪造拉取成功。

在部署目录的私有 `.env` 中设置 `MANGAFLOW_RUNTIME=gpu` 或 `MANGAFLOW_RUNTIME=cpu`。缺省为 `gpu`，进程环境变量优先；仅接受这两个字面值，不把 `.env` 当 Shell 脚本执行。保留其他配置与凭据。

正式镜像和部署包可用时：

进入解压目录后，若还没有 `.env`，先执行 `cp -n .env.example .env`，再将 `MANGAFLOW_RUNTIME` 改为已确认的模式。保留空的数据库密码，启动脚本会生成；不要覆盖已有 `.env`。随后执行：

```bash
bash scripts/docker.sh pull
bash scripts/docker.sh up -d
bash scripts/docker.sh ps
```

从包含新 CPU 文件的源码目录构建：

```bash
# 先在私有 .env 中设置 MANGAFLOW_RUNTIME=cpu
bash scripts/docker.sh up --build -d
```

CPU 模式使用独立 CPU 后端镜像、共用前端，并禁用 GPU 资源请求、CUDA 和 GPU 分割。它保留相同模型与功能，权重仍需下载并持久化。CPU 镜像标签为后端同一 GHCR 包的 `<版本>-cpu`，前端仍用 `<版本>`；稳定 CPU 别名为 `latest-cpu`。

切换模式时沿用原部署目录、Compose 项目名、端口、数据库卷、`data/`、`models/` 与私有配置；确认 `.env` 中的模式后执行对应更新命令。不要删除卷或重新建目录代替切换，也不要覆盖已有模型、OCR 或云端服务选择。

CPU 模式会在运行时使用 CPU，包括原模型配置明确指定 `cuda:0` 的情况；就绪检查使用同一规则。数据库和配置文件中的设备偏好继续保留，切回 GPU 并重建服务后恢复使用。模式切换需执行 `up -d`（源码目录使用 `up --build -d`），仅执行 `restart` 不会应用新的容器配置。

## 4. 部署后核验并汇报

核对服务健康状态、网页访问、实际端口、模型运行设备和 MCP 设置／安装地址。首次模型下载可能较慢，查看日志区分正常下载与启动错误。根据用户已授权的范围准备模型，用隔离样本或一页验证检测、识字、修复和排版；调用付费服务需遵循用户的服务使用范围。

CPU 模式可以保留本地 OCR，也可由用户配置云端 OCR；不要自动上传漫画、替用户选择付费服务或输出 API Key。翻译服务仍需单独配置，`passthrough` 不是真正翻译。

最终报告实际部署目标、硬件检查结论、CPU／GPU 模式、版本、网页和 MCP 实际地址、验证结果及未完成事项。硬件未知、模型未验证或云端配置未完成时如实说明。Skill 加载与终端权限取决于客户端，不能仅靠文字保证每个 Agent 自动遵守。
