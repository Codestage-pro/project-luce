-----------Nomes para o Go Live 2.0 Vibe Codado para BDK-----------

1. Project LUCE (Locação Universal Centralizada de Entretenimento)

-----------SETTINGS-----------

-OPÇÃO
GPU QUEIMAD

-----------Project LUCE-----------

Transmissão de tela 100% GPU (NVENC/NVDEC) sem Electron. O cliente publica via
WHIP e assiste via WHEP (`whipsink`/`whepsrc` do GStreamer) num servidor
próprio pequeno, `server/sfu_proxy.py`, que traduz isso para o **Cloudflare
Realtime SFU**:

```
luce share/watch ──WHIP/WHEP (HTTPS)──▶ cloudflared ──▶ sfu_proxy.py ──▶ API do SFU
        └────────────── vídeo/áudio (RTP/UDP) ──────────────▶ Cloudflare (anycast)
```

O vídeo vai direto do cliente para o IP anycast da Cloudflare; o servidor só
fala HTTP. Por isso não existe porta UDP para abrir no roteador, nem VPN. O
`CF_SFU_APP_SECRET` fica só no servidor.

Requisitos (Arch): `gst-plugin-webrtchttp` (pacote `extra`, dá os elementos
`whipsink`/`whepsrc`), `gst-plugins-bad` com `nvcodec` (dá
`nvh264enc`/`nvh264dec`), `gst-plugins-base` com OpenGL.

Rodando tudo na sua máquina (precisa das chaves do SFU, ver Fase 2):
1. `cp server/.env.example server/.env` e preencha as três variáveis. Crie
   `~/.config/project-luce/config.toml` com `auth_password = "<BDK_PASSWORD>"`
   (e `base_url = "http://127.0.0.1:8889"`, que já é o padrão).
2. Suba o servidor: `python server/sfu_proxy.py` (só biblioteca padrão).
3. Publique a tela (ou `--pattern` pra testar sem o seletor de monitor):
   `python -m luce share --path test`
4. Em outro terminal, assista: `python -m luce watch test`
5. `python -m luce doctor` testa NVENC/NVDEC e os elementos necessários sem
   precisar do servidor.
6. `python -m luce gui` abre a janela (GTK4): campo de nome, "Transmitir",
   "Configurações" (servidor, senha, resolução/fps/bitrate) e a lista de quem
   está no ar agora (consulta `GET /v3/paths/list` a cada 3s), com um botão
   "Assistir" por pessoa.
7. Testes do servidor (SFU falso, sem rede): `python -m unittest discover -s server`

Quem está no ar: o servidor registra quem fez `POST /{nome}/whip` e tira do
roster no `DELETE` (encerramento normal) ou, se a pessoa cair/levar kill sem
avisar, 30s depois do último heartbeat (o cliente manda um `PATCH` a cada
10s). O estado fica só em memória: reiniciar o servidor esquece quem estava no
ar até a pessoa transmitir de novo.

Pegadinhas do `gst-plugin-webrtchttp` que custaram tempo pra descobrir,
documentadas nos comentários do código:
- `whepsrc` sempre oferece um m-line de áudio, então o publisher sempre manda
  uma faixa de áudio — muda (via elemento `volume`) quando o usuário desliga
  "transmitir áudio" (`luce/capture_pipeline.py`, `luce/watch_pipeline.py`).
- O offer do `whepsrc` não é aceito como está pelo SFU: ele oferece
  `OPUS/48000` sem o `/2` de canais (o SFU só reconhece `opus/48000/2`) e
  VP8/VP9/H265/AV1 além do H264, e com qualquer um deles o SFU recusa tudo com
  "missing the published track's codec". O proxy corrige o Opus e deixa só o
  H264 (`whep_offer` em `server/sfu_proxy.py`). O WHEP do SFU também só
  funciona se o publisher já estiver com o ICE conectado; o proxy tenta de novo
  (6x, 1,5s) quando recebe `not_found_track_error`.
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

Fora de escopo por enquanto: Windows/macOS e resolução adaptada por espectador
(cada um assistir na qualidade que a rede dele aguenta — o SFU suporta
simulcast, mas o cliente só publica uma qualidade).

-----------Fase 2 — servidor público (Cloudflare)-----------

Roda na máquina do servidor (no seu caso, o notebook Debian via Dokploy). A
sinalização HTTP passa pelo `cloudflared` que já existe; a mídia vai do
cliente direto pro Cloudflare Realtime SFU.

1. Cloudflare dashboard → Realtime → SFU → crie um app; copie App ID e App
   Secret.
2. `server/docker-compose.yml` via Dokploy (tipo "Docker Compose", apontando
   pro repo, **sem domínio configurado nele** — o acesso é só pelo túnel).
   Cole `CF_SFU_APP_ID`, `CF_SFU_APP_SECRET` e `BDK_PASSWORD` (senha de
   verdade, à sua escolha) na aba Environment; o compose lê `server/.env` e
   publica a porta 8889 só em `127.0.0.1` do host.
3. No `/etc/cloudflared/config.yml`, ingress antes da regra catch-all:
   ```yaml
   - hostname: luce.codestage.pro
     service: http://127.0.0.1:8889
   ```
   depois `cloudflared tunnel route dns <tunnel> luce.codestage.pro` e
   reiniciar o `cloudflared`. O hostname `luce-api` do desenho antigo não é
   mais usado (o roster sai do mesmo servidor).
4. O servidor exige `Authorization: Bearer bdk:<BDK_PASSWORD>` em toda rota;
   o mesmo valor vai em `auth_password` no `config.toml` de quem usa.

Pro lado do amigo, instalar de verdade (ícone no menu, sem terminal depois):
`git clone https://github.com/MuriloX06/project-luce.git`, e dentro da pasta
clonada, `makepkg -si` (precisa de `base-devel` instalado — `sudo pacman -S
--needed base-devel` antes, se ele não tiver) — ou `paru -Bi .` (o `.` é
obrigatório: `-B` builda o PKGBUILD de uma pasta, e o alvo é essa pasta),
se ele preferir o paru pra resolver as dependências. **Não é um pacote da
AUR** (não precisa buscar "project-luce" no paru, isso dá erro de pacote não
encontrado) — o `-B`/`-si` só builda o `PKGBUILD` que já está na pasta
clonada. Isso instala as dependências do `PKGBUILD`, o pacote
`luce/` em `/usr/share/project-luce`, o lançador `luce` em `/usr/bin` e o
ícone "Project LUCE" no menu de apps. `packaging/config.toml` já aponta pra
esse domínio por padrão — não precisa configurar nada. Dá pra sobrescrever
o endereço criando `~/.config/project-luce/config.toml`.

**O seletor de tela depende do ambiente.** O `xdg-desktop-portal` sozinho não
captura nada — quem captura é o backend do ambiente, e só GNOME
(`xdg-desktop-portal-gnome`), KDE (`-kde`), wlroots/sway (`-wlr`) e Hyprland
(`-hyprland`) têm backend de ScreenCast. Quem usa XFCE, Cinnamon, i3 e afins
costuma ter só o `-gtk`, que **não** implementa captura: nesses o seletor
nunca aparece. O app detecta isso ao clicar em "Transmitir" e, se a sessão for
X11 de verdade, captura a tela inteira direto pelo servidor gráfico
(`ximagesrc`, sem seletor e sem o botão "Trocar tela"). Em Wayland sem backend
não tem saída — a janela diz qual pacote instalar.

**Quem não tem NVIDIA precisa de um driver VA-API** pra usar a GPU (vídeo
integrado Intel/AMD): o `gst-plugin-va` já vem como dependência, mas ele só
registra os elementos se achar o driver da placa. O pacman mostra a lista na
instalação; na prática é `sudo pacman -S intel-media-driver` (Intel Broadwell
pra cima, inclusive os UHD de desktop), `libva-mesa-driver` (AMD) ou
`libva-intel-driver` (Intel até Haswell). Sem o driver dá pra *assistir* (cai
no `avdec_h264`, software — um i3 aguenta, mas fica no talo), e *transmitir*
não começa: não existe encoder de software aqui de propósito, a janela avisa
"não deu pra transmitir: faltam nvh264enc … e vah264enc …". Pra conferir qual
caminho pegou, rode `luce` pelo terminal: ele imprime `codificando com …` e
`decodificando com VAAPI (GPU AMD/Intel)` ou `software/CPU`.
