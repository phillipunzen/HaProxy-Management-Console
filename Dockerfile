FROM node:26-bookworm-slim AS ui
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm test && npm run build

FROM python:3.13-slim-bookworm AS package
WORKDIR /src
COPY . .
RUN python scripts/package-docker.py /package/haproxy-management-docker.zip

FROM python:3.13-slim-bookworm AS app
ARG VCS_REF=unknown
ARG VERSION=dev
LABEL org.opencontainers.image.title="HAProxy Management Console" \
      org.opencontainers.image.description="Management UI for native and Docker HAProxy instances with MariaDB" \
      org.opencontainers.image.source="https://github.com/phillipunzen/HaProxy-Management-Console" \
      org.opencontainers.image.revision="$VCS_REF" \
      org.opencontainers.image.version="$VERSION"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APP_VERSION=$VERSION
WORKDIR /app
COPY requirements.txt ./
RUN apt-get update && apt-get install -y --no-install-recommends libcrypt1 && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --require-hashes -r requirements.txt && useradd --uid 10001 --create-home control
COPY alembic.ini ./alembic.ini
COPY migrations/ ./migrations/
COPY backend/ ./backend/
COPY agent/ ./agent/
COPY docs/AGENT.md ./docs/AGENT.md
COPY docs/AGENT_DOWNLOADS.md ./docs/AGENT_DOWNLOADS.md
COPY docs/IMPORT.md ./docs/IMPORT.md
COPY docs/CERTIFICATES.md ./docs/CERTIFICATES.md
COPY docs/PROXIES.md ./docs/PROXIES.md
COPY scripts/install-agent.sh ./scripts/install-agent.sh
COPY --from=package /package/haproxy-management-docker.zip ./downloads/haproxy-management-docker.zip
COPY --from=ui /build/frontend/dist/ ./frontend/dist/
USER control
EXPOSE 8000 8001
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)" || exit 1
# Forwarded headers are only honoured from FORWARDED_ALLOW_IPS (default 127.0.0.1, i.e. nobody outside the container).
CMD ["uvicorn","backend.main:app","--host","0.0.0.0","--port","8000","--workers","1","--proxy-headers"]
