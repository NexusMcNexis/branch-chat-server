# Branch Chat Server: one image for linux/amd64 and linux/arm64 (Raspberry Pi 4/5 on a 64-bit OS).
#   docker build -t branch-chat .
#   docker buildx build --platform linux/amd64,linux/arm64 -t branch-chat .
# All state (accounts, ciphertext, uploads, keys) lives in the /data volume. Nothing secret is baked in.
FROM python:3.13-slim

ARG BC_VERSION=dev
LABEL org.opencontainers.image.title="Branch Chat Server" \
      org.opencontainers.image.description="Self-hosted, end-to-end encrypted family chat" \
      org.opencontainers.image.version="${BC_VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BC_HOST=0.0.0.0 \
    BC_PORT=8766 \
    BC_STATE_DIR=/data \
    BC_APP_DIR=/app

# curl: Web Push delivery (HTTP/2 to Apple/Google/Mozilla push services).
# ca-certificates: TLS for push, link previews and SMTP.
RUN apt-get -o Acquire::Retries=5 update \
 && apt-get -o Acquire::Retries=5 install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY server/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

RUN groupadd --system --gid 10001 branchchat \
 && useradd --system --uid 10001 --gid 10001 --home-dir /data --no-create-home --shell /usr/sbin/nologin branchchat \
 && mkdir -p /data && chown 10001:10001 /data && chmod 700 /data

WORKDIR /app
COPY --chown=root:root web/ /app/
# The shared version: --build-arg BC_VERSION=... wins, otherwise the repo-root VERSION file.
COPY VERSION /tmp/VERSION
RUN v="${BC_VERSION}"; if [ -z "$v" ] || [ "$v" = dev ]; then v="$(head -n1 /tmp/VERSION)"; fi; \
    echo "$v" > /app/VERSION && rm /tmp/VERSION && python -m compileall -q /app >/dev/null

USER 10001:10001
EXPOSE 8766
VOLUME ["/data"]
# Python exits cleanly on SIGINT (it ignores SIGTERM as PID 1).
STOPSIGNAL SIGINT
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["python", "/app/manage.py", "healthcheck"]
# CMD (not ENTRYPOINT) so "docker compose run branch-chat python manage.py status" works.
CMD ["python", "/app/server.py"]
