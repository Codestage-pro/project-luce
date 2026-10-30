package pro.codestage.luce.ui

import android.app.Activity
import android.content.Context
import android.content.ContextWrapper
import android.content.pm.ActivityInfo
import androidx.activity.compose.BackHandler
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Fullscreen
import androidx.compose.material.icons.filled.FullscreenExit
import androidx.compose.material.icons.filled.VolumeMute
import androidx.compose.material.icons.filled.VolumeUp
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import kotlinx.coroutines.delay
import org.webrtc.RendererCommon
import org.webrtc.SurfaceViewRenderer
import pro.codestage.luce.WatchSession
import pro.codestage.luce.WebRtcEngine

@Composable
fun WatchGrid(
    sessions: List<WatchSession>,
    fullscreenStreamName: String?,
    onToggleFullscreen: (String?) -> Unit,
    onCloseStream: (WatchSession) -> Unit,
    modifier: Modifier = Modifier
) {
    if (sessions.isEmpty()) return

    if (fullscreenStreamName != null) {
        val focused = sessions.find { it.streamName == fullscreenStreamName }
        if (focused != null) {
            val focusedPortrait by focused.isPortraitVideo.collectAsState()
            FullscreenWindowEffect(focusedPortrait)
            BackHandler(enabled = true) { onToggleFullscreen(null) }
            WatchTile(
                session = focused,
                isFullscreen = true,
                onToggleFullscreen = { onToggleFullscreen(null) },
                onClose = {
                    onToggleFullscreen(null)
                    onCloseStream(focused)
                },
                modifier = Modifier.fillMaxSize()
            )
            return
        }
    }

    Column(
        modifier = modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        sessions.forEach { session ->
            key(session.streamName) {
                val videoRatio by session.videoAspectRatio.collectAsState()
                val portrait by session.isPortraitVideo.collectAsState()
                // vertical (celular girado): limita a altura e centraliza; paisagem ocupa a largura toda
                Box(modifier = Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
                    WatchTile(
                        session = session,
                        isFullscreen = false,
                        onToggleFullscreen = { onToggleFullscreen(session.streamName) },
                        onClose = { onCloseStream(session) },
                        modifier = (if (portrait) {
                            Modifier.fillMaxWidth().heightIn(max = 520.dp).aspectRatio(videoRatio)
                        } else {
                            Modifier.fillMaxWidth().aspectRatio(videoRatio)
                        }).clip(RoundedCornerShape(12.dp))
                    )
                }
            }
        }
    }
}

private fun Context.findActivity(): Activity? {
    var c: Context? = this
    while (c is ContextWrapper) {
        if (c is Activity) return c
        c = c.baseContext
    }
    return null
}

/** Tela cheia: acompanha a orientação do vídeo (vertical/paisagem) e esconde as barras do sistema; ao sair, devolve tudo. */
@Composable
private fun FullscreenWindowEffect(isPortraitVideo: Boolean) {
    val activity = LocalContext.current.findActivity()
    DisposableEffect(activity, isPortraitVideo) {
        val window = activity?.window
        val controller = window?.let { WindowCompat.getInsetsController(it, it.decorView) }
        activity?.requestedOrientation = if (isPortraitVideo) {
            ActivityInfo.SCREEN_ORIENTATION_SENSOR_PORTRAIT
        } else {
            ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE
        }
        controller?.systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        controller?.hide(WindowInsetsCompat.Type.systemBars())
        onDispose {
            activity?.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED
            controller?.show(WindowInsetsCompat.Type.systemBars())
        }
    }
}

private const val MAX_ZOOM = 3.5f

@Composable
fun WatchTile(
    session: WatchSession,
    isFullscreen: Boolean,
    onToggleFullscreen: () -> Unit,
    onClose: () -> Unit,
    modifier: Modifier = Modifier
) {
    val videoTrack by session.videoTrack.collectAsState()
    val stats by session.statsText.collectAsState()
    val isMuted by session.isMuted.collectAsState()
    val volume by session.volume.collectAsState()
    val isStalled by session.isStalled.collectAsState()
    val isReconnecting by session.isReconnecting.collectAsState()
    val videoRatio by session.videoAspectRatio.collectAsState()

    var showControls by remember { mutableStateOf(true) }
    // zoom/pan só existem em tela cheia; 1f = vídeo inteiro e centralizado
    var zoomScale by remember(isFullscreen) { mutableFloatStateOf(1f) }
    var offsetX by remember(isFullscreen) { mutableFloatStateOf(0f) }
    var offsetY by remember(isFullscreen) { mutableFloatStateOf(0f) }
    var boxSize by remember { mutableStateOf(IntSize.Zero) }

    // Auto-oculta controles após 4s se estiver em tela cheia
    LaunchedEffect(showControls, isFullscreen) {
        if (showControls && isFullscreen) {
            delay(4000)
            showControls = false
        }
    }

    Box(
        modifier = modifier
            .background(Color.Black)
            .onSizeChanged { boxSize = it }
            .clickable(
                interactionSource = remember { MutableInteractionSource() },
                indication = null
            ) { showControls = !showControls }
            .pointerInput(isFullscreen, videoRatio) {
                if (!isFullscreen) return@pointerInput
                detectTransformGestures { _, pan, zoom, _ ->
                    val scale = (zoomScale * zoom).coerceIn(1f, MAX_ZOOM)
                    if (scale <= 1.02f) {
                        zoomScale = 1f
                        offsetX = 0f
                        offsetY = 0f
                    } else {
                        // o vídeo ocupa a tela inteira em largura ou altura; o pan não deixa a imagem sair do quadro
                        val cw = boxSize.width.toFloat()
                        val ch = boxSize.height.toFloat()
                        val vw = if (cw / ch > videoRatio) ch * videoRatio else cw
                        val vh = if (cw / ch > videoRatio) ch else cw / videoRatio
                        val maxX = maxOf(0f, (vw * scale - cw) / 2f)
                        val maxY = maxOf(0f, (vh * scale - ch) / 2f)
                        zoomScale = scale
                        offsetX = (offsetX + pan.x).coerceIn(-maxX, maxX)
                        offsetY = (offsetY + pan.y).coerceIn(-maxY, maxY)
                    }
                }
            },
        contentAlignment = Alignment.Center
    ) {
        // Renderizador de vídeo WebRTC (SurfaceViewRenderer via EGL)
        AndroidView(
            factory = { ctx ->
                SurfaceViewRenderer(ctx).apply {
                    var lastPortrait: Boolean? = null
                    val rendererEvents = object : RendererCommon.RendererEvents {
                        override fun onFirstFrameRendered() {}

                        // roda na thread principal: reajusta a proporção e zera zoom/pan quando o vídeo gira
                        override fun onFrameResolutionChanged(videoWidth: Int, videoHeight: Int, rotation: Int) {
                            session.onFrameResolution(videoWidth, videoHeight, rotation)
                            val portrait = if (rotation % 180 == 0) videoHeight > videoWidth else videoWidth > videoHeight
                            if (lastPortrait != null && lastPortrait != portrait) {
                                zoomScale = 1f
                                offsetX = 0f
                                offsetY = 0f
                            }
                            lastPortrait = portrait
                        }
                    }
                    init(WebRtcEngine.eglBase.eglBaseContext, rendererEvents)
                    setScalingType(RendererCommon.ScalingType.SCALE_ASPECT_FIT)
                    setEnableHardwareScaler(true)
                }
            },
            update = { renderer ->
                if (renderer.tag !== videoTrack) {
                    (renderer.tag as? org.webrtc.VideoTrack)?.removeSink(renderer)
                    videoTrack?.addSink(renderer)
                    renderer.tag = videoTrack
                }
                // SurfaceView desenha em camada própria: escala/translação direto na view (graphicsLayer não pega em alguns aparelhos)
                renderer.scaleX = zoomScale
                renderer.scaleY = zoomScale
                renderer.translationX = offsetX
                renderer.translationY = offsetY
            },
            modifier = if (isFullscreen) Modifier.aspectRatio(videoRatio) else Modifier.fillMaxSize()
        )

        DisposableEffect(session, videoTrack) {
            onDispose {
                // Ao descartar a view, o addSink é limpo automaticamente pelo WebRTC ou pelo session.stop()
            }
        }

        // Overlay com Controles e Informações
        AnimatedVisibility(
            visible = showControls || isStalled || isReconnecting,
            enter = fadeIn(),
            exit = fadeOut(),
            modifier = Modifier.fillMaxSize()
        ) {
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .background(Color(0x80000000))
                    .padding(12.dp)
            ) {
                // Topo: Nome, Tag Ao Vivo e Botões de Fechar/Tela Cheia
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .align(Alignment.TopCenter),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Box(
                            modifier = Modifier
                                .clip(RoundedCornerShape(4.dp))
                                .background(LuceMagenta)
                                .padding(horizontal = 6.dp, vertical = 2.dp)
                        ) {
                            Text(
                                text = "AO VIVO",
                                color = Color.White,
                                fontSize = 10.sp,
                                fontWeight = FontWeight.Bold
                            )
                        }
                        Spacer(modifier = Modifier.width(8.dp))
                        Text(
                            text = session.streamName,
                            color = Color.White,
                            fontSize = 15.sp,
                            fontWeight = FontWeight.Bold
                        )
                    }

                    Row(verticalAlignment = Alignment.CenterVertically) {
                        if (isFullscreen) {
                            Box(
                                modifier = Modifier
                                    .clip(RoundedCornerShape(18.dp))
                                    .background(Color(0x55000000))
                                    .clickable {
                                        if (zoomScale == 1f) {
                                            zoomScale = 1.35f
                                        } else {
                                            zoomScale = 1f
                                            offsetX = 0f
                                            offsetY = 0f
                                        }
                                    }
                                    .padding(horizontal = 12.dp, vertical = 8.dp)
                            ) {
                                Text(
                                    text = if (zoomScale == 1f) "Ajustado"
                                    else "Preencher (${(zoomScale * 100).toInt()}%) · Resetar",
                                    color = Color.White,
                                    fontSize = 12.sp,
                                    fontWeight = FontWeight.Bold
                                )
                            }
                            Spacer(modifier = Modifier.width(8.dp))
                        }
                        IconButton(
                            onClick = onToggleFullscreen,
                            modifier = Modifier
                                .size(36.dp)
                                .background(Color(0x55000000), CircleShape)
                        ) {
                            Icon(
                                if (isFullscreen) Icons.Default.FullscreenExit else Icons.Default.Fullscreen,
                                contentDescription = "Tela Cheia",
                                tint = Color.White,
                                modifier = Modifier.size(20.dp)
                            )
                        }
                        Spacer(modifier = Modifier.width(8.dp))
                        IconButton(
                            onClick = onClose,
                            modifier = Modifier
                                .size(36.dp)
                                .background(Color(0x55000000), CircleShape)
                        ) {
                            Icon(
                                Icons.Default.Close,
                                contentDescription = "Fechar",
                                tint = Color.White,
                                modifier = Modifier.size(20.dp)
                            )
                        }
                    }
                }

                // Centro: Indicador de Reconexão / Congelamento
                if (isReconnecting || isStalled) {
                    Box(
                        modifier = Modifier
                            .align(Alignment.Center)
                            .clip(RoundedCornerShape(8.dp))
                            .background(LuceCard.copy(alpha = 0.85f))
                            .padding(16.dp),
                        contentAlignment = Alignment.Center
                    ) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(20.dp),
                                color = LuceCyan,
                                strokeWidth = 2.dp
                            )
                            Spacer(modifier = Modifier.width(12.dp))
                            Text(
                                text = if (isReconnecting) "Reconectando transmissão…" else "Stream congelou, recuperando…",
                                color = LuceText,
                                fontSize = 13.sp
                            )
                        }
                    }
                }

                // Base: Estatísticas e Controle de Volume/Mudo
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .align(Alignment.BottomCenter)
                ) {
                    Text(
                        text = stats,
                        color = LuceMuted,
                        fontSize = 11.sp,
                        fontFamily = FontFamily.Monospace,
                        modifier = Modifier
                            .background(Color(0x66000000), RoundedCornerShape(4.dp))
                            .padding(horizontal = 6.dp, vertical = 2.dp)
                    )

                    Spacer(modifier = Modifier.height(4.dp))

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        IconButton(
                            onClick = { session.setMute(!isMuted) },
                            modifier = Modifier.size(32.dp)
                        ) {
                            Icon(
                                if (isMuted) Icons.Default.VolumeMute else Icons.Default.VolumeUp,
                                contentDescription = "Mudo",
                                tint = if (isMuted) LuceError else LuceCyan,
                                modifier = Modifier.size(18.dp)
                            )
                        }

                        Slider(
                            value = if (isMuted) 0f else volume,
                            onValueChange = {
                                if (isMuted) session.setMute(false)
                                session.setVolume(it)
                            },
                            valueRange = 0f..1f,
                            modifier = Modifier
                                .weight(1f)
                                .height(20.dp),
                            colors = SliderDefaults.colors(
                                thumbColor = LuceCyan,
                                activeTrackColor = LuceCyan,
                                inactiveTrackColor = LuceBorder
                            )
                        )
                    }
                }
            }
        }
    }
}
