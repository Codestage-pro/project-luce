package pro.codestage.luce.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
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
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
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
            WatchTile(
                session = session,
                isFullscreen = false,
                onToggleFullscreen = { onToggleFullscreen(session.streamName) },
                onClose = { onCloseStream(session) },
                modifier = Modifier
                    .fillMaxWidth()
                    .aspectRatio(16f / 9f)
                    .clip(RoundedCornerShape(12.dp))
            )
        }
    }
}

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

    var showControls by remember { mutableStateOf(true) }

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
            .clickable(
                interactionSource = remember { MutableInteractionSource() },
                indication = null
            ) { showControls = !showControls }
    ) {
        // Renderizador de vídeo WebRTC (SurfaceViewRenderer via EGL)
        AndroidView(
            factory = { ctx ->
                SurfaceViewRenderer(ctx).apply {
                    init(WebRtcEngine.eglBase.eglBaseContext, null)
                    setScalingType(RendererCommon.ScalingType.SCALE_ASPECT_FIT)
                    setEnableHardwareScaler(true)
                }
            },
            update = { renderer ->
                videoTrack?.addSink(renderer)
            },
            modifier = Modifier.fillMaxSize()
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
                            .background(Color(0xCC12153A))
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
