# 漫画文字分类

PaddleOCR 仍负责检测文字；RF-DETR 布局分割作为独立的本地分类阶段，输出普通文字、拟声词、气泡和分镜。分镜指漫画的一格画面，不是气泡或文本框。本次用气泡结果保护对话，不替换现有分镜阅读顺序算法。

## 默认行为

- 分类在文字行合并之前执行，只过滤高置信度、覆盖足够且有字形像素支持的拟声词。默认置信度 0.60，可调整为 0.40～0.95。
- 普通文字预测与拟声词预测冲突时保留。可靠气泡内的文字保留。没有预测、不确定、模型缺失、校验失败、运行时错误或超时时，保留原文字框；不是“气泡外文字全部删除”。
- 被过滤的候选不会创建 TextRegion，因此不参与后续 OCR、翻译、蒙版、修复或排版。几何和分类依据保存在页面 metadata_json.text_classification.excluded_candidates，原图不修改。
- 设置 → 文字检测 → 文字分类，可以选择过滤、保留拟声词或关闭分类。修改配置后重新 OCR/文字检测才对现有页面生效，不后台覆盖用户的已编辑工程。
- 关闭过滤并重新检测可以恢复候选；手工创建和锁定的框在重新检测时仍保留。模型分类不是百分之百可靠，旁白、标题、装饰字和手写字仍需抽样复核。
- 分类失败会在画布底部明确显示“已保留所有候选”，提供“重试分类”入口。重试先重置分类器，再请求整页 OCR；涉及重建自动区域时仍先确认。无需重启整个后端。失败后也会在一分钟冷却期结束后的下一次检测中重新尝试，不在后台自动修改页面。

## 模型与运行环境

模型：`mayocream/koharu-layout-rfdetr-seg-2xl-1152`，固定 revision `aed55fdb8ca953c6bec33cf6ed6dd52a9b72bfa2`，SafeTensors SHA-256：`9bf6d2cbd7793c956d8c857bb1672a396eb7f100eb0682f86830d05e31168efb`。

不执行模型仓库的远程 Python 代码。初始化固定 RFDETRSeg2XLarge 架构，以严格匹配方式加载权重并校验哈希；推理禁止网络访问，不向任何服务发送漫画图片。

RF-DETR 1.7 依赖 Transformers 5，MangaOCR 依赖 Transformers 4，因此分类运行在独立持久进程中。Docker 镜像把分类依赖装到 `/opt/layout-classifier`，共享既有 PyTorch/CUDA，但不升级主环境 Transformers。每个分类实例串行使用模型；模型只冷加载一次，最多缓存两张原图的紧凑布局掩码，不在拖框、画蒙版或翻译时重复推理。

安装模型（Docker 镜像内的代码目录）：

```sh
docker compose exec backend python -m app.services.detection.install_classification
```

非 Docker 运行时，在模型目录下创建 `classification-runtime` 虚拟环境，再单独安装 `backend/requirements/classification.txt`，或设置 `MANGAFLOW_CLASSIFIER_PYTHON` 指向独立环境的 Python。不要在 MangaOCR 主环境直接安装这份依赖。模型下载安装是显式操作，网页请求不会自动下载。

参考：[模型说明与训练数据条款](https://huggingface.co/mayocream/koharu-layout-rfdetr-seg-2xl-1152)。模型使用 Manga109 衍生训练数据，模型、训练数据和 RF-DETR 代码的授权应分别核对，不能因为代码开源就假设权重允许任意商业用途。

## 本地验证（2026-10-14）

- 后端 485 项通过、1 项跳过，前端 319 项通过；构建和新增代码检查通过。额外用仅包含本次提交的干净代码副本验证编译和分类测试，未夹带编辑历史等其他未提交改动。
- 在 Tesla V100 16 GB 上只读测试 6 张手写漫画页：首张页过滤 4 个高置信度拟声词候选，抽样看图保留了该页气泡对白；其他不确定结果保留。这不是完整人工真值准确率评测，也不保证所有拟声词都能过滤。
- 首页包括 Paddle 和分类模型冷加载，整套检测约 29.4 秒；后续各页约 2.4～3.4 秒。这里不是分类模型单独耗时。数据库哈希前后相同，诊断图与报告只写入临时目录，不覆盖工程图片。
- 可重复测试：`PYTHONPATH=backend python scripts/benchmark_text_classification.py --project-id <项目ID> --pages 6 --output tmp/text-classification`。
