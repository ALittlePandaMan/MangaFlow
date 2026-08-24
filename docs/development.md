# 开发指南

## 快速开始

```bash
./scripts/bootstrap.sh
./scripts/dev.sh
```

前端地址为 `http://localhost:5173`，FastAPI 文档为 `http://localhost:8000/docs`。Docker 开发可使用：

```bash
./scripts/docker.sh up --build -d
./scripts/docker.sh logs -f
```

## 常用检查

```bash
./scripts/check.sh
./scripts/docker.sh config --quiet
```

后端测试通过 `backend/tests/conftest.py` 使用独立临时目录和 SQLite，不会读写本地项目数据，也不会自动加载模型清单。前端使用 Vitest、Testing Library、ESLint、TypeScript 和生产构建作为质量门禁；组件测试放在被测组件旁，并以用户可见行为和无障碍语义为主要断言。

## 新增 Provider

1. 在对应的 `backend/app/services/<stage>/` 中实现阶段协议。
2. 在 registry 中注册名称、设备、依赖和说明。
3. 为 Provider 添加配置校验和缺少依赖时的明确错误。
4. 更新 `config.example.yaml`、设置页候选说明和文档。
5. 添加不依赖真实云端密钥的测试。

## 新增前端业务

单一业务域放在 `frontend/src/features/<feature>/`，路由页面留在 `pages/` 负责数据编排。可复用表单、Dialog 和加载状态留在 `components/`。体积较大的路由使用 `React.lazy`，并使用直接路径导入以保持分包边界。

## CI

GitHub Actions 在 Python 3.11/3.12 上运行 Ruff 和 Pytest，在 Node 20 上运行 ESLint、Vitest 与生产构建，并校验公开配置模板、应用版本和 Docker Compose。依赖升级由维护者按需评估，不自动创建升级 Pull Request。
