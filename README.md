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
dela no roster da GUI.

Com Tailscale ativo na máquina, o MediaMTX por padrão também anuncia
candidatos ICE da interface Tailscale, o que deixava o WHEP instável em
teste local; `server/mediamtx.yml` restringe a loopback
(`webrtcIPsFromInterfaces: no`) — isso precisa voltar a incluir interfaces
reais na Fase 2 (teste com um amigo via LAN/Tailscale).

Fora de escopo por enquanto: Windows/macOS, GUI completa, VPS/TLS (fases
seguintes do plano).

-----------Fase 2 — teste à distância (Tailscale)-----------

Servidor no seu PC, amigo conecta pela sua IP do Tailscale. No seu lado:
```
./scripts/run-tailscale-server.sh
```
(usa `server/mediamtx-tailscale.yml` — escuta em todas as interfaces, não só
loopback, e libera a API do MediaMTX pra faixa do Tailscale também; por
padrão o MediaMTX só deixa a `api` vir de 127.0.0.1, o que quebraria o
roster da GUI do amigo.)

Pro lado do amigo, instalar de verdade (ícone no menu, sem terminal depois):
`sudo pacman -S --needed base-devel` (uma vez, se ele não tiver) e então,
dentro da pasta do projeto, `makepkg -si`. Isso instala as dependências do
`PKGBUILD`, o pacote `luce/` em `/usr/share/project-luce`, o lançador `luce`
em `/usr/bin` e o ícone "Project LUCE" no menu de apps — já apontando pro seu
IP do Tailscale por padrão (`/etc/project-luce/config.toml`, ver
`packaging/`); sem autenticação, a rede do Tailscale é o controle de acesso.
Dá pra sobrescrever o endereço criando `~/.config/project-luce/config.toml`.
