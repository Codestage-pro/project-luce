#!/usr/bin/env bash
# Sobe o MediaMTX pra teste à distância (Fase 2, via Tailscale).
set -euo pipefail
cd "$(dirname "$0")/.."
if ! ip addr show tailscale0 &>/dev/null; then
    echo "interface tailscale0 não existe — o Tailscale tá rodando? (tailscale status)" >&2
    exit 1
fi
exec mediamtx server/mediamtx-tailscale.yml
