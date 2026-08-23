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

启动时会幂等读取配置清单：默认只补齐缺失阶段，不覆盖数据库中已有的生效配置。开启 `preload` 后，本地 Provider 的缺失权重会下载到 `models/`。`device: recommended` 只在首次导入时解析为明确的 `cpu` 或 `cuda:0`。

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
