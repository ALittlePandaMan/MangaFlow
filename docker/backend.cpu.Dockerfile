# syntax=docker/dockerfile:1.7
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    CUDA_VISIBLE_DEVICES="" NVIDIA_VISIBLE_DEVICES=void MANGAFLOW_SEGMENTATION_BACKEND=opencv MANGAFLOW_RUNTIME=cpu
WORKDIR /app
# Optional build-time mirror; signed APT metadata is still checked and the
# installed image always retains the official HTTPS source configuration.
ARG DEBIAN_MIRROR=https://deb.debian.org
# Full unrar retains RAR5/solid archive support, including in the CPU image.
RUN sed -i -e 's/^Components: main$/Components: main non-free/' \
    -e 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && cp /etc/apt/sources.list.d/debian.sources /tmp/debian.sources \
    && sed -i "s|https://deb.debian.org|${DEBIAN_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 libgomp1 fonts-noto-cjk unrar \
    && mv /tmp/debian.sources /etc/apt/sources.list.d/debian.sources \
    && rm -rf /var/lib/apt/lists/*
COPY backend/requirements/cpu-constraints.txt /tmp/cpu-constraints.txt
COPY backend/requirements/base.txt backend/requirements/models.txt /tmp/requirements/
# Build-only mirror override; it does not change the installed application's settings.
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    pip install --no-deps --timeout 300 --retries 10 torch==2.6.0+cpu torchvision==0.21.0+cpu \
    --index-url https://download.pytorch.org/whl/cpu \
    && pip install --timeout 300 --retries 10 -c /tmp/cpu-constraints.txt \
    torch torchvision -r /tmp/requirements/base.txt -r /tmp/requirements/models.txt \
    && pip install --no-deps simple-lama-inpainting==0.1.2
COPY backend/requirements/classification.txt backend/requirements/local-ocr.txt /tmp/requirements/
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    python -m venv --system-site-packages /opt/layout-classifier \
    && /opt/layout-classifier/bin/python -m pip install --timeout 300 --retries 10 \
    -c /tmp/cpu-constraints.txt -r /tmp/requirements/classification.txt \
    && python -m venv --system-site-packages /opt/local-ocr \
    && /opt/local-ocr/bin/python -m pip install --timeout 300 --retries 10 \
    -c /tmp/cpu-constraints.txt -r /tmp/requirements/local-ocr.txt
COPY docker/verify_cpu_runtime.py /usr/local/bin/mangaflow-verify-cpu
RUN python /usr/local/bin/mangaflow-verify-cpu
COPY VERSION /app/VERSION
COPY connectors/mangaflow-mcp /app/connectors/mangaflow-mcp
COPY backend /app/backend
WORKDIR /app/backend
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
