FROM python:3.11-slim

WORKDIR /app

# Install Piper TTS system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Install Piper binary
RUN pip install --no-cache-dir piper-tts

# Download default voice model
RUN mkdir -p /opt/piper/models && \
    wget -q -O /opt/piper/models/en_US-lessac-medium.onnx \
      "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx" && \
    wget -q -O /opt/piper/models/en_US-lessac-medium.onnx.json \
      "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5000", "--workers", "1"]
