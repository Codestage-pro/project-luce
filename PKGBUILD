# Pacote Arch do Project LUCE — instala luce/ + um lançador "luce" no PATH +
# um ícone no menu de apps (project-luce.desktop), pra abrir sem terminal.
# Build local, sem source remoto: `makepkg -si` dentro da pasta do projeto.
pkgname=project-luce
pkgver=1.0.0
pkgrel=1
pkgdesc="Transmissão de tela do grupo (GPU, WHIP/WHEP via MediaMTX)"
arch=('any')
license=('custom')
depends=(python python-gobject python-dbus gtk4 gstreamer gst-plugins-base
         gst-plugins-good gst-plugins-bad gst-plugin-webrtchttp gst-plugin-gtk4
         xdg-desktop-portal)
optdepends=('gst-plugin-va: aceleração de vídeo em GPU AMD/Intel'
            'nvidia-utils: aceleração de vídeo em GPU NVIDIA')

package() {
    install -d "$pkgdir/usr/share/project-luce"
    cp -r "$startdir/luce" "$pkgdir/usr/share/project-luce/"
    find "$pkgdir/usr/share/project-luce" -name __pycache__ -exec rm -rf {} +
    install -Dm755 "$startdir/packaging/luce-launcher.sh" "$pkgdir/usr/bin/luce"
    install -Dm644 "$startdir/packaging/project-luce.desktop" "$pkgdir/usr/share/applications/project-luce.desktop"
    install -Dm644 "$startdir/packaging/config.toml" "$pkgdir/etc/project-luce/config.toml"
}
