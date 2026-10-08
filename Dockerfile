FROM node:20-bookworm-slim AS ui
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim-bookworm AS app
ARG VCS_REF=unknown
ARG VERSION=0.1.0
LABEL org.opencontainers.image.title="HAProxy Management Console" \
      org.opencontainers.image.description="Management UI for native and Docker HAProxy instances with MariaDB" \
      org.opencontainers.image.source="https://github.com/phillipunzen/HaProxy-Management-Console" \
      org.opencontainers.image.revision="$VCS_REF" \
      org.opencontainers.image.version="$VERSION"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN apt-get update && apt-get install -y --no-install-recommends libcrypt1 && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home control
COPY backend/ ./backend/
COPY agent/ ./agent/
COPY docs/AGENT.md ./docs/AGENT.md
COPY docs/IMPORT.md ./docs/IMPORT.md
COPY scripts/install-agent.sh ./scripts/install-agent.sh
COPY downloads/haproxy-management-docker.zip ./downloads/haproxy-management-docker.zip
COPY --from=ui /build/frontend/dist/ ./frontend/dist/
USER control
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)" || exit 1
CMD ["uvicorn","backend.main:app","--host","0.0.0.0","--port","8000","--workers","1","--no-proxy-headers"]
