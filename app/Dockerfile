FROM python:3.11-slim

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create data directories and copy CSV training data as defaults
RUN mkdir -p /data/audio /data/csv-defaults

EXPOSE 8080

# Entrypoint script copies default CSVs if /data is empty (fresh volume),
# then starts uvicorn.
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
