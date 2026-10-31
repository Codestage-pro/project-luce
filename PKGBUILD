# Pacote Arch do Project LUCE — instala luce/ + um lançador "luce" no PATH +
# um ícone no menu de apps (pro.codestage.Luce.desktop), pra abrir sem terminal.
# Build local, sem source remoto: `makepkg -si` dentro da pasta do projeto.
pkgname=project-luce
pkgver=1.0.45
pkgrel=1
pkgdesc="Transmissão de tela do grupo (GPU, WHIP/WHEP via Cloudflare Realtime SFU)"
arch=('any')
license=('custom')
# O xdg-desktop-portal sozinho não captura tela: quem captura é o backend do
# ambiente, e só existe backend de ScreenCast pra gnome/kde/wlroots/hyprland
# (o -gtk, usado em XFCE/Cinnamon/i3, não tem). Por isso os backends estão em
# optdepends e não em depends — num X11 sem backend o app cai no ximagesrc
# (gst-plugins-good, já obrigatório) em vez de ficar sem transmitir.
# gst-plugin-va dá o vah264enc/vah264dec, que é como vídeo integrado (Intel/AMD)
# usa a GPU — sem ele o PC cai no software e nem todo mundo aqui tem NVIDIA.
# gst-libav dá o avdec_h264, último recurso: sem ele quem assiste numa máquina
# sem GPU utilizável não monta vídeo nenhum e só o áudio chega (ver
# _make_decoder em luce/watch_pipeline.py).
depends=(python python-gobject python-dbus gtk4 gstreamer gst-plugins-base
         gst-plugins-good gst-plugins-bad gst-libav gst-plugin-va
         gst-plugin-webrtchttp gst-plugin-gtk4 xdg-desktop-portal)
# O gst-plugin-va não serve de nada sozinho: ele só registra os elementos se
# achar um driver VA-API no render node da placa (confirmado nesta máquina —
# plugin instalado, `gst-inspect-1.0 va` dizendo "0 features" por falta de
# driver compatível). O pacman lista estes na instalação; é a dica pro dono da
# máquina instalar o da GPU dele.
optdepends=('xdg-desktop-portal-gnome: seletor de tela no GNOME'
            'xdg-desktop-portal-kde: seletor de tela no KDE'
            'xdg-desktop-portal-wlr: seletor de tela em sway/wlroots'
            'xdg-desktop-portal-hyprland: seletor de tela no Hyprland'
            'intel-media-driver: GPU Intel Broadwell ou mais nova (vídeo integrado) — sem isso o vídeo vai na CPU'
            'libva-mesa-driver: GPU AMD — sem isso o vídeo vai na CPU'
            'libva-intel-driver: GPU Intel antiga (até Haswell)'
            'nvidia-utils: GPU NVIDIA (NVENC/NVDEC)')

package() {
    install -d "$pkgdir/usr/share/project-luce"
    cp -r "$startdir/luce" "$pkgdir/usr/share/project-luce/"
    rm -rf "$pkgdir/usr/share/project-luce/luce/windows" "$pkgdir/usr/share/project-luce/luce/winenv.py" "$pkgdir/usr/share/project-luce/luce/winsys.py"
    find "$pkgdir/usr/share/project-luce" -name __pycache__ -exec rm -rf {} +
    install -Dm755 "$startdir/packaging/luce-launcher.sh" "$pkgdir/usr/bin/luce"
    install -Dm644 "$startdir/packaging/pro.codestage.Luce.desktop" "$pkgdir/usr/share/applications/pro.codestage.Luce.desktop"
    install -Dm644 "$startdir/packaging/pro.codestage.Luce.svg" "$pkgdir/usr/share/icons/hicolor/scalable/apps/pro.codestage.Luce.svg"
    install -Dm644 "$startdir/packaging/config.toml" "$pkgdir/etc/project-luce/config.toml"
}
