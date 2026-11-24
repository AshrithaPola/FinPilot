# FinPilot backend — FastAPI + LangGraph pipeline.
# Same Python as the backend/.venv that produced the Phase 0 baseline.
FROM python:3.12.8-slim

WORKDIR /app

# Install dependencies first so code changes don't bust the layer cache.
# torch is installed from the CPU-only wheel BEFORE requirements.txt so
# sentence-transformers doesn't pull the multi-GB CUDA build into the image.
# Version matches backend/.venv and CI. constraints.txt pins every
# transitive dependency to the baseline venv.
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir torch==2.14.1+cpu --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt -c constraints.txt

# The reranker's ONNX model (~80 MB) baked in: with min instances 0 on Cloud
# Run, every cold start would otherwise download it on the first search.
ENV FASTEMBED_CACHE_PATH=/opt/fastembed
RUN python -c "from fastembed.rerank.cross_encoder import TextCrossEncoder; TextCrossEncoder('Xenova/ms-marco-MiniLM-L-6-v2')"

COPY . .

EXPOSE 8000
# Cloud Run sets PORT; Compose doesn't, so 8000.
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
