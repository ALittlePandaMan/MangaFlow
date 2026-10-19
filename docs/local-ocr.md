# 本地漫画 OCR

设置 → 模型配置 → OCR 识别可选择与 Koharu 对齐的四种本地 OCR：

| Provider | 模型 | 固定 revision |
| --- | --- | --- |
| `paddleocr-vl-1.6`（新安装的推荐项） | PaddlePaddle/PaddleOCR-VL-1.6 | `66317acc4c9fc17bd154591ce650735cd2855f3e` |
| `manga-ocr` | kha-white/manga-ocr-base（原有适配器） | 沿用用户的模型设置 |
| `baberu-ocr` | genshiai-daichi/baberu-ocr | `d9cc13153e9a1cd8fdfa3b7b1cc329da2020aeae` |
| `hayai-ocr` | JustANormalTinkerer/hayai-ocr-v2 | `4a4ce477c9a8841f208b94e1d9ed5c0938965e05` |

新模型检查点对应 Koharu `4df6a4f785e8c2056e25e112eb7b89ccf2f040cc`；推理在本项目的 Python/PyTorch 中完成，不是复制其 Rust 运行时。原有普通 PaddleOCR、Tesseract 和云端 OCR 配置保留，不自动删除或迁移用户设置。

这只改变识字，不改变 RF-DETR 检测、文本框扩边、旋转/去重、Mask 或已有页面。旧原文不会自动重写。要比较效果，请主动对测试页运行“重新检测”（包含 OCR）；这会重建该页的自动框。仅切换设置不会执行付费翻译。

## 隔离及安装

Docker 镜像包含 `/opt/local-ocr` 的独立 Transformers 5 环境；主环境 MangaOCR 的 Transformers 4 和检测进程的环境不变。非 Docker 部署需创建 `models/ocr-runtime` 虚拟环境，安装 `backend/requirements/local-ocr.txt` 及匹配硬件的 PyTorch；可用 `MANGAFLOW_OCR_PYTHON` 指定其 Python。

首次启用模型时会在允许自动准备模型的环境中下载固定版本，后续使用本地缓存。也可提前执行：

```sh
python -m app.services.ocr.install_local --provider paddleocr-vl-1.6
python -m app.services.ocr.install_local --provider baberu-ocr
python -m app.services.ocr.install_local --provider hayai-ocr
```

推理进程离线运行，图片不发送到模型仓库。PaddleOCR-VL 使用 Transformers 自带实现，不执行仓库 Python；Baberu/Hayai 仅加载版本固定且 SHA-256 校验通过的必要模型/配置/分词代码。其辅助视觉配置也固定版本。不会执行仓库训练脚本或使用 Pickle 权重。

三个新模型共享现有单路 GPU 推理门禁；相同 Provider/设备的不同输出长度设置复用一个常驻工作进程。V100 使用 FP32 避免不支持 BF16 或 FP16 溢出，不承诺比 MangaOCR 更快。模型故障和超时会明确报错，不静默换模型、不把失败解释为空白文本。取消在当前有界区域推理返回后生效。

设置中的最大输出长度为 16–1024 Token，单区域超时为 10–600 秒；达到可检测的输出上限会标记待检查。生成式 OCR 不提供校准置信度，兼容字段 `0.8` 不作为真实置信度参与质量判断。手写字、小字、页码识别准确率需要同图验证；新增这些模型不等于已经实现页码过滤。

## 只读验证

从项目根目录设置 `PYTHONPATH=backend`，执行：

```sh
python scripts/smoke_local_ocr.py /path/to/crop.png --providers paddleocr-vl-1.6 baberu-ocr hayai-ocr
```

脚本不创建项目任务、不调用翻译、不改原图或页面；输出实际设备、耗时、原文与源文件摘要检查。可用 `--box x y width height` 直接验证原图裁剪，或用 `--expected` 检查已知文本（NFKC/空白归一化）。这只是接入验证，不是整本漫画准确率评测。
