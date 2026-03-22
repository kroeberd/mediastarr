FROM python:3.12-slim

LABEL org.opencontainers.image.title="Mediastarr"
LABEL org.opencontainers.image.description="Automated missing-content and quality-upgrade search for Sonarr & Radarr"
LABEL org.opencontainers.image.version="6.3.7"
LABEL org.opencontainers.image.source="https://github.com/kroeberd/mediastarr"
LABEL org.opencontainers.image.url="https://mediastarr.de/"
LABEL org.opencontainers.image.licenses="MIT"
LABEL org.opencontainers.image.authors="kroeberd"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/       ./app/
COPY templates/ ./templates/
COPY static/    ./static/

VOLUME ["/data"]
EXPOSE 7979

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:7979/api/state')" || exit 1

ENV DATA_DIR=/data

CMD ["gunicorn", "--bind", "0.0.0.0:7979", "--workers", "1", "--threads", "4", "--timeout", "120", "--chdir", "/app", "app.main:app"]
