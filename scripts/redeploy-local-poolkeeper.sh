#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="${HOME}/.local/go/bin:${PATH}"
export GOPROXY="${GOPROXY:-https://goproxy.cn,direct}"
export GOCACHE="${ROOT}/.gocache"
export CGO_ENABLED=0 GOOS=linux GOARCH=amd64

mkdir -p /tmp/opencode/g2a-bin /tmp/opencode/g2a-docker
cd "${ROOT}/backend"
go build -buildvcs=false -trimpath -ldflags="-s -w" -o /tmp/opencode/g2a-bin/grok2api ./cmd/grok2api
cp /tmp/opencode/g2a-bin/grok2api /tmp/opencode/g2a-docker/grok2api
printf '%s\n+poolkeeper\n' "$(cat "${ROOT}/VERSION")" > /tmp/opencode/g2a-docker/VERSION
cat > /tmp/opencode/g2a-docker/Dockerfile <<'D'
FROM ghcr.io/chenyme/grok2api:latest
COPY grok2api /app/grok2api
COPY VERSION /app/VERSION
RUN chmod 755 /app/grok2api
D
docker build -t grok2api:local-poolkeeper /tmp/opencode/g2a-docker
docker stop grok2api || true
docker rm grok2api || true
docker run -d \
  --name grok2api \
  --restart unless-stopped \
  --network grok2api_default \
  --init \
  --security-opt no-new-privileges:true \
  --stop-timeout 30 \
  -p 8000:8000 \
  --add-host host.docker.internal:host-gateway \
  -e TZ=Asia/Shanghai \
  -e HTTP_PROXY=http://172.17.0.1:7890 \
  -e HTTPS_PROXY=http://172.17.0.1:7890 \
  -e http_proxy=http://172.17.0.1:7890 \
  -e https_proxy=http://172.17.0.1:7890 \
  -e NO_PROXY=localhost,127.0.0.1 \
  -e no_proxy=localhost,127.0.0.1 \
  -e ALL_PROXY=http://172.17.0.1:7890 \
  -e all_proxy=http://172.17.0.1:7890 \
  -v /home/ljc/grok2api/config.yaml:/run/grok2api/config.yaml:ro \
  -v grok2api_grok2api-data:/app/data \
  grok2api:local-poolkeeper
sleep 3
curl -fsS http://127.0.0.1:8000/healthz
echo
docker exec grok2api cat /app/VERSION
