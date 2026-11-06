FROM python:3.13-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    COFFEE_TIMEZONE=Europe/Copenhagen \
    COFFEE_MODEL_VARIANT=large \
    HISTORY_DB=/data/history.sqlite3

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

RUN groupadd --gid 10001 coffee \
    && useradd --uid 10001 --gid coffee --no-create-home coffee \
    && mkdir -p /data \
    && chown coffee:coffee /data

COPY app.py generator.py weights.json weights-large.json ./
USER coffee
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2).read()"]

CMD ["python", "-m", "uvicorn", "app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-proxy-headers"]

FROM base AS test
USER root
COPY requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY phrases.py write_weights.py large_phrases.py large_grammar.py write_large_weights.py ./
COPY tests ./tests
USER coffee
CMD ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]

FROM base AS runtime
