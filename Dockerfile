# Internet Health: monitoring with a web page, Speedtest (Fast.com, Cloudflare) and Telegram alerts.
# It started as a fork of NetProbe_Python (MIT, see THIRD_PARTY_NOTICES.md) and is built from source on a pinned base image.
# The base (Debian 13, Python 3.14) is pinned by digest; Dependabot proposes newer digests and package versions.
FROM python:3.14-slim@sha256:35caba83af2fe6da9a5d922f9b0156f21d1246a48278c58a08bd253303ba42e8

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

# ping (the probes), ip (the default gateway), the CA certificates (Fast.com, IP lookups, Telegram)
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates iputils-ping iproute2 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/app.py /app/app.py
COPY src/netprobe_extensions.py /app/netprobe_extensions.py
COPY src/index.html /app/templates/index.html
# starts as root only to hand /data to PUID:PGID (default 99:100), then runs the app as that user
COPY --chmod=755 src/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# One worker only: the probe loop is a thread of the process (a second worker would probe twice). The threads keep
# the page responsive while a Speedtest runs. Override with -e GUNICORN_CMD_ARGS=... if you must.
ENV HOME=/tmp \
    WEB_PORT=8080 \
    GUNICORN_TIMEOUT=240 \
    GUNICORN_CMD_ARGS="--threads 4"

ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.title="Internet Health" \
      org.opencontainers.image.description="Home internet monitoring: per-target history, readable outage alerts (Telegram, Unraid), packet-loss report, WAN / ISP, Speedtest (Fast.com, Cloudflare, Ookla)" \
      org.opencontainers.image.source="https://github.com/d1-m4ss/internet-health" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      net.unraid.docker.icon="https://raw.githubusercontent.com/d1-m4ss/internet-health/main/docs/icon.png"

# /api/health answers 503 when the probe loop has stored no cycle for a long time (the web page alone being up is not enough)
HEALTHCHECK --interval=60s --timeout=8s --start-period=90s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.getenv('WEB_PORT', '8080'), timeout=6)" || exit 1

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["sh", "-c", "exec gunicorn -b 0.0.0.0:${WEB_PORT:-8080} --workers 1 --timeout ${GUNICORN_TIMEOUT:-240} --log-level info app:app"]
