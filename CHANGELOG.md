# 更新记录

本项目采用 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 的结构，并计划遵循语义化版本。

## [Unreleased]

### Added

- 可迁移的 `config.yaml` 模型清单与脱敏的 `config.example.yaml`。
- 首次启动硬件检测、推荐配置安装和六阶段可用性检查。
- 编辑器多选、多边形/四边形变换、透视文字、区域级重处理和多种导出方式。
- GitHub Actions、Dependabot、Issue 模板和贡献/安全文档。

### Changed

- 编辑器代码按 feature 聚合并按路由延迟加载。
- 云端翻译密钥只写入 `.env`，其余生效配置持久化到 `config.yaml`。
- 导出图片使用按总页数补零的稳定文件名。

### Fixed

- 后台任务完成后前端仍停留在加载状态的问题。
- 区域重处理覆盖人工编辑几何坐标的问题。
- 净图全图色偏、竖排标点方向和透视文字导出清晰度问题。
