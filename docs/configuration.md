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

也可以运行 `./scripts/bootstrap.sh` 或 `./scripts/docker.sh up --build` 自动创建缺失文件。脚本不会覆盖已存在的本地设置。

## 迁移到另一台设备

1. 克隆仓库或复制源码。
2. 单独安全复制原设备的 `config.yaml` 和 `.env`。
3. 如需保留项目，复制 `data/`；如需避免重新下载模型，也复制 `models/`。
4. 执行 `./scripts/docker.sh up --build -d`。

只复制 `config.yaml` 而不复制 `.env` 时，模型和运行参数仍可恢复，但云端翻译需要重新填写 API Key。

## Docker 挂载

Compose 将 `config.yaml` 和 `.env` 以文件形式挂载到后端容器，网页保存设置后会同步回宿主机。`data/` 与 `models/` 也使用 bind mount，重建容器不会主动删除它们。

真实配置已同时加入 `.gitignore` 与 `.dockerignore`，既不会进入 Git，也不会发送到 Docker build context。运行时挂载不受 `.dockerignore` 影响。

## 维护启动模式

迁移验证时可以通过环境变量设置 `MANGAFLOW_MAINTENANCE_MODE=true` 后启动新进程。该模式不执行数据库初始化或模型清单同步，不恢复任务和历史，不启动工作线程或资产清理。它保留已有排队、运行和暂停任务的数据库状态，不会提前调用云端服务。

维护模式下只有 `/health` 可访问，其 `status` 为 `maintenance`；其他 HTTP 请求返回 503，任务 WebSocket 关闭码为 1013。因此健康端点可达只表示维护进程存活，不表示业务已经就绪。数据库与文件校验需通过离线工具完成。

此开关不是热切换或自动迁移工具。先停止接收业务并安全排空旧进程，再启动维护实例；不能同时让旧实例继续写入同一数据库。验证通过后撤销开关并重新启动正常服务，才允许恢复队列。维护模式本身不备份、不迁移或回滚数据。

## SQLite / PostgreSQL 迁移

默认继续使用 SQLite。正常启动会执行 Alembic 升级；维护模式不会执行。旧库按版本补齐字段并保留所有已支持的旧字段，不用 `create_all` 推测未来迁移。未知缺失字段、未匹配的表或列会阻止复制，不静默丢弃。禁止破坏性 schema downgrade；回退通过验证后的反向数据复制完成。

`docker-compose.postgres.yml` 是显式启用的覆盖文件，不会自动切换现用数据库。它使用 PostgreSQL 18、内部网络和持久卷，不发布数据库端口。密码从私有环境变量 `MANGAFLOW_POSTGRES_PASSWORD` 读取，应用通过 `PGPASSWORD` 单独传给驱动，避免密码里的 `@`、`/` 等字符破坏连接 URL。不要提交密码、渲染包含密钥的 Compose 配置，或使用 `down -v` 删除数据库卷。

本机迁移验证完成后，在私有 `.env` 中设置 `MANGAFLOW_LOCAL_POSTGRES=true`，`scripts/docker.sh` 会自动叠加这个覆盖文件，后续启动、停止和重建沿用 PostgreSQL 部署。该标记只选择 Docker 配置，不执行迁移。直接使用 Compose 时仍需同时指定两个文件；外部 PostgreSQL 不使用此本机标记。不要通过恢复旧 `.env` 回滚一个已经开放新写入的数据库。

迁移顺序：

1. 停止新请求和调度，等待当前任务退出；保留排队/暂停状态，不用强制取消代替排空。确认没有第二个写入进程。
2. 备份私有 `.env`、`config.yaml`、原始 `data/.secret_key`（或原 `MANGAFLOW_SECRET_KEY`）、项目资源及数据库。SQLite 必须用下面的在线备份命令，不能只复制 live `.db` 而遗漏 WAL。
3. 留一份只读原始备份；另复制一份演练快照，对快照执行 `upgrade`。不要用现用数据库作为升级演练目标。
4. 在空 PostgreSQL 库执行 `copy`。工具先校验目标无数据，全部行在同一目标事务中复制，逐表校验数量和 SHA-256，并恢复历史自增序列。失败不会激活目标；目标已有任何数据均拒绝覆盖。
5. 对迁移库执行 `verify-assets`，确认资源不存在缺失且密钥可解密；保留工具输出的 JSON 证据。首次切库前再从 PostgreSQL 反向复制到全新 SQLite 文件并验证。源读取使用一致性快照，源与目标仍应保持停写。
6. 新应用先以维护模式连接目标库，验证连接和配置；全部检查通过后正常启动，检查图片、区域、编辑、撤销与任务恢复，再开放业务。不要让两套服务同时恢复相同排队任务。

离线工具不会启动服务、下载模型或调用云端。URL 从命名环境变量读取，避免放进命令行日志；环境变量须由本机安全方式提供。`--report-file` 只新建文件，已有报告不会覆盖。运行命令前创建备份和报告目录：

```bash
PYTHONPATH=backend .venv/bin/python -m app.services.database_transfer backup-sqlite data/mangaflow.db <新备份路径> --report-file <新备份报告路径>
PYTHONPATH=backend .venv/bin/python -m app.services.database_transfer upgrade --url-env SNAPSHOT_DATABASE_URL
PYTHONPATH=backend .venv/bin/python -m app.services.database_transfer copy --source-env SNAPSHOT_DATABASE_URL --target-env TARGET_DATABASE_URL --report-file <新复制报告路径>
PYTHONPATH=backend .venv/bin/python -m app.services.database_transfer verify-assets --url-env TARGET_DATABASE_URL --data-dir data --secret-key-file data/.secret_key --report-file <新资源报告路径>
```

使用注入的 `MANGAFLOW_SECRET_KEY` 时省略 `--secret-key-file`。路径参数里的尖括号是占位符，需替换。`copy` 支持 PostgreSQL → SQLite；反向时只交换源和目标的环境变量名，目标必须是新库。切库后只要发生过新写入，就不能直接换回旧 SQLite 文件：必须再次排空、备份 PostgreSQL、反向复制并核对新增任务/历史/文本，才能切回，以免丢失新数据。

资源校验同样要求素材目录停写。重复引用只解析一次，父目录解析使用有界缓存；数据库读取结束后，最多四路文件读取逐一核对 SHA-256。清单顺序、缺失文件检查、密钥解密和软链接越界检查保持不变；不能把此离线校验当作并发修改素材时的安全快照。

## 执行策略与推理诊断

性能设置保存 GPU 并行上限（1–2）和显存预留（至少 2048 MB）。旧策略文件自动采用一路、2048 MB。上限不是绕过模型安全限制的开关：只有独立实例通过吞吐、P95 和显存门禁后，实际并行才可增加；当前本地模型共用单路门禁。CTD 与 MangaOCR 的批大小采用至少 `max(预留设置, 总显存的 15%)` 的安全余量。

执行状态只读取已有诊断，不为了显示设备而加载模型。它显示 CTD、MangaOCR、RF-DETR 和 LaMa 实际使用的后端与设备，CPU 回退原因不会被“配置为 CUDA”掩盖。
