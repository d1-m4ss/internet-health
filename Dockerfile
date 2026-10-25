# Internet Pulse: the NetProbe image with the three Internet Pulse files baked in (no bind mounts needed).
# The base is pinned by digest; scripts/verify-image.sh checks that its original files are the ones src/app.py.patch was made for.
# The base image is linux/amd64 only, so this one is too.
FROM bmmbmm01/netprobe@sha256:386bdc2c177b11aef4a1223dfd91ae47fe2f9804c5449c5f4c2caae1d048c155

COPY src/app.py /app/app.py
COPY src/netprobe_extensions.py /app/netprobe_extensions.py
COPY src/index.html /app/templates/index.html

# The stock image runs one single-threaded worker; threads keep the page responsive while a Speedtest runs (the probe loop
# is a thread of the same process, so never raise --workers). Override with -e GUNICORN_CMD_ARGS=... if you must.
ENV GUNICORN_CMD_ARGS="--threads 4"

ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.title="Internet Pulse" \
      org.opencontainers.image.description="Internet monitoring for Unraid: per-target history, WAN / ISP, packet-loss report, Speedtest and Telegram alerts (NetProbe with extensions)" \
      org.opencontainers.image.source="https://github.com/d1-m4ss/internet-health" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      net.unraid.docker.icon="https://raw.githubusercontent.com/d1-m4ss/internet-health/main/docs/icon.png"

HEALTHCHECK --interval=60s --timeout=8s --start-period=90s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/prefs' % os.getenv('WEB_PORT', '8080'), timeout=6)" || exit 1
