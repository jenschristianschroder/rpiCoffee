# =============================================================================
# Dockerfile for Raspberry Pi (linux/arm64)
#
# Build ON the Pi (or with QEMU enabled):
#   docker build -t coffee-llm .
#
# Run:
#   docker run -p 8000:8000 coffee-llm
#
# NOTE: First run Dockerfile.quantize on your dev machine to produce
#       coffee-gguf/coffee-Q4_K_M.gguf, then copy it to the Pi.
# =============================================================================

FROM python:3.11-slim-bookworm

ENV CMAKE_ARGS="-DGGML_BLAS=OFF"

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git libgomp1 \
    && pip install --no-cache-dir llama-cpp-python==0.3.8 \
    && apt-get purge -y build-essential cmake git \
    && apt-get autoremove -y \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY server.py .
COPY coffee-gguf/coffee-Q4_K_M.gguf model/coffee-Q4_K_M.gguf

EXPOSE 8000

ENV MODEL_PATH="model/coffee-Q4_K_M.gguf"
ENV THREADS="4"
ENV CTX="512"
ENV BATCH="64"

CMD ["sh", "-c", "python server.py --model $MODEL_PATH --threads $THREADS --ctx $CTX --batch $BATCH"]
