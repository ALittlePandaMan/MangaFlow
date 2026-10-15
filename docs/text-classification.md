# 统一漫画版面检测

文字检测统一使用 Koharu 的 RF-DETR 实例分割模型，一次输出普通文字、拟声词、气泡与分镜。分镜是一格漫画画面，不是气泡或文本框。旧 Paddle 文字检测、YOLO 气泡模型与几何合并链路已移除；PaddleOCR 仍可用作识字 Provider。

## 检测与关联

- 默认阈值：文字 0.25、拟声词 0.20、气泡 0.50、分镜 0.50。同类框按 IoU 0.5 去重。
- 只有模型的普通文字实例建立 TextRegion。拟声词不因处于气泡内就转成普通文字，也不生成重复文本框；候选几何保存在页面分类报告中，便于诊断。
- 文字与气泡按文字 bbox 内的字形掩码计算包含率，默认至少 90%；多个候选满足时选择掩码面积最小的气泡。bbox 外的杂散像素不影响归属。
- 阅读顺序参考 Koharu 的分镜 → 气泡 → 文字层级，层级按 bbox 至少 50% 包含和最小父框确定；同级按从上往下、从右往左排序。此层级与 90% 像素归属分别计算。
- 同气泡里的独立文字实例保持独立，气泡的精确掩码供修复约束使用，不以气泡外接矩形代替字形 Mask。
- 检测失败时任务报错，原有区域保持不变，不把异常解释为空白页。手工与锁定区域在重新检测时保留。

设置页可调整文字、气泡、分镜置信度与文字气泡包含率。旧页升级或阈值变化会在整页 OCR 时请求确认，确认前不会重建自动区域；拖框、改字和区域 OCR 不触发重新检测。旧的检测配置自动迁移，但不会后台修改页面。

## 模型与运行环境

模型：`mayocream/koharu-layout-rfdetr-seg-2xl-1152`，固定 revision `aed55fdb8ca953c6bec33cf6ed6dd52a9b72bfa2`，SafeTensors SHA-256：`9bf6d2cbd7793c956d8c857bb1672a396eb7f100eb0682f86830d05e31168efb`。

不执行模型仓库的远程 Python 代码。固定 RFDETRSeg2XLarge 架构，严格匹配权重并校验哈希；本地推理禁止联网，不发送漫画图片。

RF-DETR 1.7 依赖 Transformers 5，MangaOCR 依赖 Transformers 4，因此 RF-DETR 运行于独立持久进程。Docker 安装路径仍为 `/opt/layout-classifier`，复用 PyTorch/CUDA，不升级主环境 Transformers。相同设备与 Python 环境共享模型，阈值变化不会复制 GPU 权重。

后处理先筛选类别/阈值，再逐个缩放掩码到原图尺寸，只传输二值紧凑裁剪；不同时分配 160 张全页浮点掩码。最多缓存两个包含阈值的推理结果；页面及掩码总大小设有上限。

显式安装权重：

```sh
docker compose exec backend python -m app.services.detection.install_classification
```

非 Docker 环境可在模型目录创建 `classification-runtime` 虚拟环境，单独安装 `backend/requirements/classification.txt`，或设置 `MANGAFLOW_CLASSIFIER_PYTHON`。这些路径为兼容既有安装保留，不代表额外的第二套检测模型。

参考：[模型说明与训练数据条款](https://huggingface.co/mayocream/koharu-layout-rfdetr-seg-2xl-1152)。模型涉及 Manga109 衍生数据，模型、训练数据与 RF-DETR 代码授权需分别核对。

## 性能与回归

在本地 Tesla V100 上只读抽测 4 张漫画页，首次检测约 5 秒（不含约 11 秒模型加载），随后约 0.5～0.8 秒。手写对白、横排文字与 WebP 输入均有抽样验证；这不是人工真值准确率评测，不保证所有拟声词与手写字都分类正确。

```sh
PYTHONPATH=backend python scripts/benchmark_layout.py --project-id <项目ID> --pages 3 --output tmp/unified-layout
```

脚本只读工程数据，报告与标注图写入输出目录。回归测试覆盖同气泡独立文字、相邻气泡、拟声词与文字重叠、bbox 外噪点、层级阅读顺序、高分辨率掩码内存、旧配置迁移和失败时保留手工/锁定框。
