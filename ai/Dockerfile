# The vendor's AI service (the thing the learners build). Build context is the repo root (it needs customer/ only for tests, not here).
FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY ai/requirements.txt .
RUN pip install -r requirements.txt && useradd --uid 10001 --create-home app
# Real embeddings (BAAI/bge-small, ONNX, CPU) are opt-in because the model downloads from Hugging Face at build time:
#   docker compose build --build-arg FASTEMBED=1 ai    and run with EMBEDDER=fastembed
ARG FASTEMBED=0
ENV FASTEMBED_CACHE_PATH=/opt/fastembed
RUN if [ "$FASTEMBED" = "1" ]; then pip install fastembed && python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')" && chmod -R a+rX /opt/fastembed; fi
COPY ai/copilot copilot
COPY ai/scripts scripts
USER 10001
EXPOSE 8080
CMD ["uvicorn", "copilot.app:app", "--host", "0.0.0.0", "--port", "8080"]
