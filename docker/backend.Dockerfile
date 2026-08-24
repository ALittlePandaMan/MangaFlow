# syntax=docker/dockerfile:1.7
FROM pytorch/pytorch:2.6.0-cuda12.6-cudnn9-runtime

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 fonts-noto-cjk && rm -rf /var/lib/apt/lists/*
COPY backend/requirements/base.txt /tmp/requirements-base.txt
COPY backend/requirements/models.txt /tmp/requirements-models.txt
RUN pip install --no-cache-dir --timeout 300 --retries 10 -r /tmp/requirements-base.txt
RUN pip install --no-cache-dir --timeout 300 --retries 10 \
    protobuf opt_einsum==3.3.0 'safetensors>=0.6.0' \
    nvidia-cufile-cu12==1.11.1.6 nvidia-cuda-cccl-cu12==12.6.77
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-deps --timeout 300 --retries 10 \
    -i https://www.paddlepaddle.org.cn/packages/stable/cu126/ paddlepaddle-gpu==3.2.0
RUN sed '/^paddlepaddle==/d' /tmp/requirements-models.txt > /tmp/requirements-models-gpu.txt \
    && pip install --no-cache-dir --timeout 300 --retries 10 -r /tmp/requirements-models-gpu.txt
RUN pip install --no-cache-dir --no-deps simple-lama-inpainting==0.1.2
# Paddle's native extension looks for libgomp through the system loader. The
# PyTorch base image already ships the library in Conda, so register that copy
# instead of downloading a duplicate Ubuntu package during every image build.
RUN ln -sf /opt/conda/lib/libgomp.so.1 /usr/local/lib/libgomp.so.1 && ldconfig
COPY VERSION /app/VERSION
COPY backend /app/backend
WORKDIR /app/backend
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
