-----------Nomes para o Go Live 2.0 Vibe Codado para BDK-----------

1. Project LUCE (Locação Universal Centralizada de Entretenimento)

-----------SETTINGS-----------

-OPÇÃO
GPU QUEIMAD

-----------Project LUCE — Fase 1 (loopback local)-----------

Transmissão de tela 100% GPU (NVENC/NVDEC) sem Electron, publicando via WHIP
e assistindo via WHEP num relay MediaMTX próprio (sem depender do backend do
Go Live).

Requisitos (Arch/AUR): `mediamtx-bin`, `gst-plugin-webrtchttp` (pacote
`extra`, dá os elementos `whipsink`/`whepsrc`), `gst-plugins-bad` com
`nvcodec` (dá `nvh264enc`/`nvh264dec`), `gst-plugins-base` com OpenGL.

1. Suba o relay local:
   ```
   ./scripts/run-local-server.sh
   ```
2. Em outro terminal, publique a tela (ou `--pattern` pra testar sem o
   seletor de monitor do portal):
   ```
   python -m luce share --path test
   ```
3. Em outro terminal, assista:
   ```
   python -m luce watch test
   ```
4. `python -m luce doctor` testa NVENC/NVDEC e os elementos necessários sem
   precisar do relay.
5. `python -m luce gui` abre a janela (GTK4): campo de nome, botão
   "Transmitir", botão "Configurações" (endereço do servidor,
   resolução/fps/bitrate) e uma lista com quem está no ar agora (consulta a
   API do MediaMTX a cada 3s), com um botão "Assistir" por pessoa. A janela
   de vídeo em si (sua transmissão e cada pessoa assistida) continua sendo
   a janela GL nativa que o GStreamer abre sozinho — não tem vídeo embutido
   dentro da janela principal ainda.

Validado localmente (loopback, dois processos na mesma máquina): publish com
`--pattern` sobe pro MediaMTX, watch assina via WHEP e abre uma janela GL
mostrando o vídeo decodificado por NVDEC, com NVENC publicando ao mesmo
tempo — ida e volta 100% GPU, sem Electron.

Pegadinhas do `gst-plugin-webrtchttp` que custaram tempo pra descobrir,
documentadas nos comentários do código:
- `whepsrc` sempre oferece um m-line de áudio e tem um bug na negociação do
  Opus padrão quando o publisher não manda áudio nenhum — o MediaMTX rejeita
  a oferta inteira com "codecs not supported by client". Em vez de dar uma
  `audio-caps` inválida pro `whepsrc` (contorno antigo, removido), o
  publisher agora sempre manda uma faixa de áudio — muda (via elemento
  `volume`) quando o usuário desliga "transmitir áudio" — então esse bug
  nunca é disparado (`luce/capture_pipeline.py`, `luce/watch_pipeline.py`).
- O sinal `pad-added` do `whepsrc` dispara antes das caps do pad estarem
  negociadas, e os pads de áudio/vídeo têm nome genérico (`src_%u`) — não dá
  pra filtrar por `media=video`/`media=audio` nesse momento. Resolvido
  plugando um `parsebin` em cada pad novo e olhando o nome da caps resultante
  (`audio/x-opus` ou `video/x-h264`) pra decidir que cadeia de decode montar
  (`luce/watch_pipeline.py:_on_pad_added`).

Áudio: publisher sempre manda uma faixa Opus (`pulsesrc` → funciona em
PulseAudio e PipeWire via pipewire-pulse, sem código separado — aparece como
"Project LUCE" no qpwgraph/pavucontrol pra quem quiser rotear manualmente).
"Transmitir áudio" em Configurações (ou `--no-audio` na CLI) só muda a faixa,
não deixa de publicá-la. Quem assiste tem mute e volume por pessoa, na linha
dela no roster da GUI. Resolução, FPS e qualidade de vídeo/áudio em
Configurações são botões de opção fixa (não caixa de texto) — evita digitar
um valor fora do que o encoder ou a rede aguentam.

Qualquer VPN/interface extra ativa na máquina pode fazer o MediaMTX anunciar
um candidato ICE dela, o que deixava o WHEP instável em teste local;
`server/mediamtx.yml` restringe a loopback (`webrtcIPsFromInterfaces: no`).

Fora de escopo por enquanto: Windows/macOS, GUI completa, VPS/TLS (fases
seguintes do plano). Resolução adaptada por espectador (cada um assiste na
qualidade que a rede dele aguenta) também fica pra depois — o MediaMTX não
suporta isso pelo WHEP hoje (só lê simulcast como tracks independentes via
RTSP/RTMP/SRT, não via WebRTC: github.com/bluenviron/mediamtx/issues/5596) e
não transcodifica nada (github.com/bluenviron/mediamtx/discussions/3953); os
dois jeitos de contornar isso custam caro demais pra v1 — publicar 2 streams
fixos simultâneos (dobra o encode de quem transmite) ou transcode no servidor
via FFmpeg/`runOnDemand` (reintroduz carga de GPU/CPU no servidor, o problema
de escala que fez abandonar o Go Live).

-----------Fase 2 — servidor público (Cloudflare)-----------

Mesma máquina do servidor (no seu caso, o notebook Debian via Dokploy): a
sinalização HTTP (WHIP/WHEP + roster) passa pelo seu `cloudflared` já
existente, e o vídeo/áudio (RTP/ICE) relaia pelo **Cloudflare Realtime
TURN**. Decisão de arquitetura e por quê, nos comentários de
`server/mediamtx-public.yml` e `scripts/refresh-cloudflare-turn.sh`.

No servidor:
1. `server/docker-compose.yml` via Dokploy (tipo "Docker Compose", apontando
   pro repo — **sem domínio configurado nele**, ver comentário no arquivo
   pro bug do Dokploy que isso evita). Monta `server/mediamtx-public.yml`.
2. Cria uma TURN Key em Cloudflare → Realtime → TURN Keys; cola
   `TURN_KEY_ID`/`TURN_KEY_API_TOKEN` na aba Environment do serviço no
   Dokploy (ele escreve um `server/.env` do lado do `docker-compose.yml`
   sozinho — ver `server/.env.example`).
3. Cron rodando `scripts/refresh-cloudflare-turn.sh` 1x/dia (credenciais do
   TURN expiram em até 48h) — aponta pra cópia do script dentro da pasta que
   o Dokploy clonou, não uma clonagem manual separada.
4. Cola `BDK_PASSWORD` (senha de verdade, à sua escolha) na aba Environment
   do Dokploy também — `server/docker-compose.yml` repassa pro MediaMTX via
   `MTX_AUTHINTERNALUSERS_0_PASS` (override nativo dele pra essa entrada de
   `authInternalUsers`, documentado em mediamtx.org/docs/usage/configuration).
   O servidor fica aberto pra internet, então publish/read exigem essa
   senha (mesmo valor de `auth_password` no `config.toml` de quem vai usar).
5. No `/etc/cloudflared/config.yml` do servidor, ingress apontando pras
   portas que o `network_mode: host` já expõe em loopback:
   ```yaml
   - hostname: luce.codestage.pro
     service: http://127.0.0.1:8889
   - hostname: luce-api.codestage.pro
     service: http://127.0.0.1:9997
   ```
   (antes da regra catch-all do final do arquivo), depois
   `cloudflared tunnel route dns <tunnel> luce.codestage.pro` (e de novo pro
   `-api`) e reiniciar o `cloudflared`.

Pro lado do amigo, instalar de verdade (ícone no menu, sem terminal depois):
`sudo pacman -S --needed base-devel` (uma vez, se ele não tiver) e então,
dentro da pasta do projeto, `makepkg -si`. Isso instala as dependências do
`PKGBUILD`, o pacote `luce/` em `/usr/share/project-luce`, o lançador `luce`
em `/usr/bin` e o ícone "Project LUCE" no menu de apps. `packaging/config.toml`
já aponta pra esses domínios por padrão — não precisa configurar nada. Dá pra
sobrescrever o endereço criando `~/.config/project-luce/config.toml`.
