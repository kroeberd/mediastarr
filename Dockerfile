FROM python:3.12-slim

LABEL org.opencontainers.image.title="Mediastarr"
LABEL org.opencontainers.image.description="Automated missing-content and quality-upgrade search for Sonarr & Radarr"
LABEL org.opencontainers.image.version="7.1.7"
LABEL org.opencontainers.image.source="https://github.com/kroeberd/mediastarr"
LABEL org.opencontainers.image.url="https://mediastarr.de/"
LABEL org.opencontainers.image.licenses="MIT"
LABEL org.opencontainers.image.authors="kroeberd"

WORKDIR /app

COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends tini && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements.txt

COPY VERSION    ./
COPY app/       ./app/
COPY templates/ ./templates/
COPY static/    ./static/

VOLUME ["/data"]
EXPOSE 7979

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:7979/api/state')" || exit 1

ENV DATA_DIR=/data

# tini: minimal PID 1 handler — ensures proper signal forwarding and zombie reaping
# S6 overlay is NOT used (single-process app — gunicorn handles everything)
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["gunicorn", "--bind", "0.0.0.0:7979", "--workers", "1", "--threads", "4", "--timeout", "120", "--chdir", "/app", "app.main:app"]
