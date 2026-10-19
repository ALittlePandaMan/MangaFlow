"""Optional FP32 CTD execution. The caller owns preprocessing and model locks."""
from __future__ import annotations

from pathlib import Path


class CudaUnavailable(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def memory_headroom(torch) -> bool:
    from app.services.execution import gpu_reserve_bytes

    free, total = torch.cuda.mem_get_info(0)
    return free >= gpu_reserve_bytes(total) + 512 * 1024**2


class CudaSegmentationSession:
    def __init__(self, path: Path, threads: int):
        # Importing torch first loads this application's CUDA/cuDNN libraries.
        import onnxruntime as ort
        import torch

        if not torch.cuda.is_available() or "CUDAExecutionProvider" not in ort.get_available_providers():
            raise CudaUnavailable("cuda_provider_unavailable")
        if not memory_headroom(torch):
            raise CudaUnavailable("cuda_memory_reserve")
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self.session = ort.InferenceSession(str(path), sess_options=options, providers=[
            ("CUDAExecutionProvider", {"device_id": 0, "use_tf32": 0,
                "gpu_mem_limit": 2 * 1024**3, "arena_extend_strategy": "kSameAsRequested",
                "cudnn_conv_algo_search": "HEURISTIC", "cudnn_conv_use_max_workspace": 0}),
            "CPUExecutionProvider",
        ])
        self.session.disable_fallback()
        if self.session.get_providers()[0] != "CUDAExecutionProvider":
            raise CudaUnavailable("cuda_provider_initialization_failed")
        inputs = self.session.get_inputs()
        if len(inputs) != 1 or inputs[0].type != "tensor(float)" or inputs[0].shape != [1, 3, 1024, 1024]:
            raise ValueError("Unsupported CTD FP32 input")
        self.input_name = inputs[0].name
        outputs = {output.name:output for output in self.session.get_outputs()}
        if "seg" not in outputs or outputs["seg"].type != "tensor(float)" or outputs["seg"].shape != [1,1,1024,1024]:
            raise ValueError("Unsupported CTD seg output")

    def forward(self, blob):
        import torch

        if not memory_headroom(torch):
            raise CudaUnavailable("cuda_memory_reserve")
        return self.session.run(["seg"], {self.input_name: blob})
