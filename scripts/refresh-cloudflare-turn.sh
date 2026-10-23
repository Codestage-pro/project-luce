#!/usr/bin/env bash
# Gera credenciais novas do Cloudflare Realtime TURN (expiram em até 48h, não
# dá pra deixar uma senha fixa) e substitui o bloco CLOUDFLARE_TURN em
# server/mediamtx-public.yml, depois reinicia o MediaMTX pra aplicar. Rodar
# via cron 1x/dia no servidor (crontab -e):
#   0 4 * * * /caminho/pro/project-luce/scripts/refresh-cloudflare-turn.sh
# ttl de 48h dá margem de sobra pro atraso entre execuções diárias.
# Precisa de curl e jq (apt install -y curl jq no Debian).
set -euo pipefail
cd "$(dirname "$0")/.."

# shellcheck disable=SC1091
source server/.env

response=$(curl -sf \
  "https://rtc.live.cloudflare.com/v1/turn/keys/$TURN_KEY_ID/credentials/generate-ice-servers" \
  -H "Authorization: Bearer $TURN_KEY_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"ttl": 172800}')

# iceServers[0] é o STUN (só urls); iceServers[1] é o TURN (com credenciais).
user=$(echo "$response" | jq -r '.iceServers[1].username')
pass=$(echo "$response" | jq -r '.iceServers[1].credential')

awk -v user="$user" -v pass="$pass" '
  /# BEGIN CLOUDFLARE_TURN/ { print; printf "  - url: turn:turn.cloudflare.com:3478?transport=udp\n    username: %s\n    password: %s\n", user, pass; skip=1; next }
  /# END CLOUDFLARE_TURN/ { skip=0 }
  !skip
' server/mediamtx-public.yml > server/mediamtx-public.yml.tmp
mv server/mediamtx-public.yml.tmp server/mediamtx-public.yml

docker compose -f server/docker-compose.yml restart mediamtx
