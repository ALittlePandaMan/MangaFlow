"""Fail the CPU image build if any runtime pulls accelerator dependencies back in."""
import json
import subprocess
import sys

PROBE = '''
import importlib.metadata as metadata
import json
import torch
names={item.metadata['Name'].lower().replace('_','-') for item in metadata.distributions()}
forbidden=sorted(name for name in names if name.startswith('nvidia-') or name in {
    'paddlepaddle-gpu','onnxruntime-gpu','onnxruntime-directml','tensorrt','triton'})
assert not forbidden, forbidden
assert torch.version.cuda is None and not torch.cuda.is_available(), 'CPU PyTorch required'
print(json.dumps({'python':__import__('sys').executable,'torch':torch.__version__,'cuda':torch.version.cuda,'forbidden':forbidden}))
'''


def main():
    results = []
    for python in (sys.executable, "/opt/layout-classifier/bin/python", "/opt/local-ocr/bin/python"):
        result = subprocess.run([python, "-c", PROBE], capture_output=True, text=True)
        if result.returncode:
            raise SystemExit(f"CPU runtime check failed for {python}:\n{result.stderr.strip()}")
        results.append(json.loads(result.stdout))
    import paddle
    assert not paddle.device.is_compiled_with_cuda(), "CPU PaddlePaddle required"
    print(json.dumps({"cpu_runtimes": results, "paddle": paddle.__version__}))


if __name__ == "__main__":
    main()
