FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

EXPOSE 8003

ENV OLLAMA_ENDPOINT="http://host.docker.internal:8000"
ENV SETTINGS_DIR="/data"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8003"]
