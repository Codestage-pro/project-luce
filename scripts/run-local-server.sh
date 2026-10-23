#!/usr/bin/env bash
# Sobe o MediaMTX local pra teste (Fase 1, sem systemd/VPS).
set -euo pipefail
cd "$(dirname "$0")/.."
exec mediamtx server/mediamtx.yml
