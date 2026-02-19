FROM python:3.11-slim-bookworm

LABEL maintainer="local-tts"
LABEL description="Local Text-to-Speech using Piper TTS on Raspberry Pi"

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    wget \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create directories for models and output
RUN mkdir -p /app/models /app/output

# Copy application code
COPY app/ /app/app/
COPY scripts/ /app/scripts/

# Download a default English voice model (en_US-lessac-medium)
# This runs at build time so the image is ready to go
RUN python /app/scripts/download_model.py --voice en_US-lessac-medium --output-dir /app/models

# Expose the API port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:5000/health || exit 1

# Run the TTS server
CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "5000"]
