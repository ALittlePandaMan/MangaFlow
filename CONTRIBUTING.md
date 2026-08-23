# 参与 MangaFlow 开发

感谢你愿意改进 MangaFlow。请先在 issue 中说明较大的功能或架构调整，避免重复实现；小型修复可以直接提交 Pull Request。

## 准备环境

需要 Python 3.11/3.12、Node.js 20+。首次运行：

```bash
git clone git@github.com:ALittlePandaMan/MangaFlow.git
cd MangaFlow
./scripts/bootstrap.sh
./scripts/dev.sh
```

`bootstrap.sh` 会从公开模板创建本地 `.env` 和 `config.yaml`。这两个文件、`data/`、`models/` 与 `.agents/` 都不会进入 Git。

## 开发约定

- 保持 `backend/`、`frontend/` 的边界；跨多个页面复用的前端代码放在 `components/`，单一业务的代码放在 `features/<name>/`。
- API 路由只处理协议和校验，可复用命令放进 `backend/app/application/`，模型实现按流水线阶段放进 `services/`。
- 不要提交 API Key、真实私有 API 地址、模型权重、项目图片或导出文件。
- 不要用 fallback 伪装成功。缺少模型、密钥或运行时依赖时，应返回可理解且可恢复的错误。
- 行为变化必须补充测试；用户可见的配置或工作流变化要同步更新 `README.md` 或 `docs/`。

## 提交前验证

```bash
./scripts/check.sh
./scripts/docker.sh config --quiet
```

提交信息推荐使用 Conventional Commits，例如：

```text
feat(editor): add polygon rotation handles
fix(export): preserve vertical punctuation
docs: explain portable model configuration
```

Pull Request 请保持单一目标，并说明验证方式、兼容性影响以及是否涉及配置迁移。
