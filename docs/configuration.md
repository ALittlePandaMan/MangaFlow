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

启动时会幂等读取配置清单：默认只补齐缺失阶段，不覆盖数据库中已有的生效配置。开启 `preload` 后，本地 Provider 的缺失权重会下载到 `models/`。

支持 GPU 的本地模型默认使用 `device: auto`：加载时优先选择可用 GPU，不可用则使用 CPU；遇到明确的 GPU 初始化或推理故障也会回退 CPU。该策略会原样保存，迁移或重启后重新选择设备，不会把某台机器的 CPU 探测结果固化到配置中。旧的 `device: recommended` 等同于 `auto`。也可以在设置中明确指定 CPU 或 GPU；仅支持 CPU 的 OpenCV、Tesseract、Pillow 不受影响，云端模型不设置本机设备。

修复使用的字形分割由 `MANGAFLOW_SEGMENTATION_BACKEND=auto` 控制：优先使用可用的 ONNX CUDA 运行时，显存余量不足、运行时缺失或推理异常时回退 OpenCV CPU。可显式设为 `opencv` 保留 CPU；设备切换不改变分割输入规格、mask 膨胀规则，GPU 与 CPU 的概率缓存保持隔离。

## 首次创建本地配置

```bash
cp .env.example .env
cp config.example.yaml config.yaml
```

也可以运行 `./scripts/bootstrap.sh` 或 `./scripts/docker.sh up --build` 自动创建缺失文件。脚本不会覆盖已存在的非空密码或本地设置。

## 迁移到另一台设备

1. 克隆仓库或复制源码。
2. 单独安全复制原设备的 `config.yaml` 和 `.env`。
3. 如需保留项目，备份并恢复 PostgreSQL 数据库，同时复制 `data/` 和其中的 `.secret_key`；如需避免重新下载模型，也复制 `models/`。仅复制 `data/` 不会迁移 PostgreSQL 持久卷中的项目记录。
4. 在新设备启动 PostgreSQL、恢复数据库，再执行 `./scripts/docker.sh up --build -d`。备份方式见下文。

只复制 `config.yaml` 而不复制 `.env` 时，模型和运行参数仍可恢复，但云端翻译需要重新填写 API Key。

## Docker 挂载

Compose 将 `config.yaml` 和 `.env` 以文件形式挂载到后端容器，网页保存设置后会同步回宿主机。`data/` 与 `models/` 也使用 bind mount，重建容器不会主动删除它们。

真实配置已同时加入 `.gitignore` 与 `.dockerignore`，既不会进入 Git，也不会发送到 Docker build context。运行时挂载不受 `.dockerignore` 影响。

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

性能设置保存 GPU 并行上限（1–2）和显存预留（至少 2048 MB）。旧策略文件自动采用一路、2048 MB。上限不是绕过模型安全限制的开关：只有独立实例通过吞吐、P95 和显存门禁后，实际并行才可增加；当前本地模型共用单路门禁。CTD 与 MangaOCR 的批大小采用至少 `max(预留设置, 总显存的 15%)` 的安全余量。

执行状态只读取已有诊断，不为了显示设备而加载模型。它显示 CTD、MangaOCR、RF-DETR 和 LaMa 实际使用的后端与设备，CPU 回退原因不会被“配置为 CUDA”掩盖。
