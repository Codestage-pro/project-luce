package pro.codestage.luce

import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioPlaybackCaptureConfiguration
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.projection.MediaProjection
import android.os.Build
import android.os.Process
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import org.webrtc.AudioSource
import org.webrtc.AudioTrack
import org.webrtc.DataChannel
import org.webrtc.DefaultVideoDecoderFactory
import org.webrtc.DefaultVideoEncoderFactory
import org.webrtc.EglBase
import org.webrtc.IceCandidate
import org.webrtc.MediaConstraints
import org.webrtc.MediaStream
import org.webrtc.MediaStreamTrack
import org.webrtc.PeerConnection
import org.webrtc.PeerConnectionFactory
import org.webrtc.RtpReceiver
import org.webrtc.RtpTransceiver
import org.webrtc.SdpObserver
import org.webrtc.SessionDescription
import org.webrtc.SurfaceTextureHelper
import org.webrtc.VideoCapturer
import org.webrtc.VideoSource
import org.webrtc.VideoTrack
import org.webrtc.audio.JavaAudioDeviceModule

object WebRtcEngine {
    val eglBase: EglBase by lazy { EglBase.create() }

    private var factory: PeerConnectionFactory? = null
    private var adm: JavaAudioDeviceModule? = null

    @Synchronized
    fun getFactory(context: Context): PeerConnectionFactory {
        if (factory == null) {
            val initOptions = PeerConnectionFactory.InitializationOptions.builder(context.applicationContext)
                .setEnableInternalTracer(false)
                .createInitializationOptions()
            PeerConnectionFactory.initialize(initOptions)

            val audioDeviceModule = JavaAudioDeviceModule.builder(context.applicationContext)
                .setUseHardwareAcousticEchoCanceler(true)
                .setUseHardwareNoiseSuppressor(true)
                .setAudioSource(MediaRecorder.AudioSource.MIC)
                .createAudioDeviceModule()
            adm = audioDeviceModule

            val encoderFactory = DefaultVideoEncoderFactory(
                eglBase.eglBaseContext,
                /* enableIntelVp8 = */ true,
                /* enableH264HighProfile = */ true
            )
            val decoderFactory = DefaultVideoDecoderFactory(eglBase.eglBaseContext)

            factory = PeerConnectionFactory.builder()
                .setAudioDeviceModule(audioDeviceModule)
                .setVideoEncoderFactory(encoderFactory)
                .setVideoDecoderFactory(decoderFactory)
                .createPeerConnectionFactory()
        }
        return factory!!
    }

    fun createIceServers(): List<PeerConnection.IceServer> {
        return listOf(
            PeerConnection.IceServer.builder("stun:stun.cloudflare.com:3478").createIceServer(),
            PeerConnection.IceServer.builder("stun:stun.l.google.com:19302").createIceServer()
        )
    }

    fun createRtcConfig(): PeerConnection.RTCConfiguration {
        val config = PeerConnection.RTCConfiguration(createIceServers())
        config.sdpSemantics = PeerConnection.SdpSemantics.UNIFIED_PLAN
        config.continualGatheringPolicy = PeerConnection.ContinualGatheringPolicy.GATHER_ONCE
        return config
    }
}

class WatchSession(
    private val context: Context,
    val streamName: String,
    val baseUrl: String,
    val myViewerName: String,
    val authToken: String?
) {
    private val scope = CoroutineScope(Dispatchers.Default + Job())
    private var peerConnection: PeerConnection? = null
    private var whepLocation: String? = null

    val videoTrack = MutableStateFlow<VideoTrack?>(null)
    val audioTrack = MutableStateFlow<AudioTrack?>(null)
    val statsText = MutableStateFlow("conectando…")
    val isMuted = MutableStateFlow(false)
    val volume = MutableStateFlow(1.0f)
    val isStalled = MutableStateFlow(false)
    val isReconnecting = MutableStateFlow(false)

    private var lastPacketsReceived: Long = 0
    private var stallSecondsCount: Int = 0

    fun start() {
        scope.launch {
            connect()
        }
    }

    private suspend fun connect() {
        withContext(Dispatchers.Default) {
            try {
                isReconnecting.value = true
                val factory = WebRtcEngine.getFactory(context)
                val rtcConfig = WebRtcEngine.createRtcConfig()

                val iceGatheringDone = CompletableDeferred<Unit>()

                val pcObserver = object : PeerConnection.Observer {
                    override fun onSignalingChange(state: PeerConnection.SignalingState?) {}
                    override fun onIceConnectionChange(state: PeerConnection.IceConnectionState?) {}
                    override fun onIceConnectionReceivingChange(receiving: Boolean) {}
                    override fun onIceGatheringChange(state: PeerConnection.IceGatheringState?) {
                        if (state == PeerConnection.IceGatheringState.COMPLETE) {
                            iceGatheringDone.complete(Unit)
                        }
                    }
                    override fun onIceCandidate(candidate: IceCandidate?) {}
                    override fun onIceCandidatesRemoved(candidates: Array<out IceCandidate>?) {}
                    override fun onAddStream(stream: MediaStream?) {}
                    override fun onRemoveStream(stream: MediaStream?) {}
                    override fun onDataChannel(channel: DataChannel?) {}
                    override fun onRenegotiationNeeded() {}
                    override fun onAddTrack(receiver: RtpReceiver?, streams: Array<out MediaStream>?) {}

                    override fun onTrack(transceiver: RtpTransceiver?) {
                        val track = transceiver?.receiver?.track()
                        if (track is VideoTrack) {
                            videoTrack.value = track
                        } else if (track is AudioTrack) {
                            audioTrack.value = track
                            track.setVolume(if (isMuted.value) 0.0 else volume.value.toDouble())
                        }
                    }
                }

                val pc = factory.createPeerConnection(rtcConfig, pcObserver)
                    ?: throw RuntimeException("Falha ao criar PeerConnection")
                peerConnection = pc

                // Adiciona transceivers RECV_ONLY
                pc.addTransceiver(
                    MediaStreamTrack.MediaType.MEDIA_TYPE_VIDEO,
                    RtpTransceiver.RtpTransceiverInit(RtpTransceiver.RtpTransceiverDirection.RECV_ONLY)
                )
                pc.addTransceiver(
                    MediaStreamTrack.MediaType.MEDIA_TYPE_AUDIO,
                    RtpTransceiver.RtpTransceiverInit(RtpTransceiver.RtpTransceiverDirection.RECV_ONLY)
                )

                // Cria Oferta
                val offerDeferred = CompletableDeferred<SessionDescription>()
                pc.createOffer(object : SdpObserver {
                    override fun onCreateSuccess(desc: SessionDescription) { offerDeferred.complete(desc) }
                    override fun onSetSuccess() {}
                    override fun onCreateFailure(error: String?) { offerDeferred.completeExceptionally(RuntimeException(error)) }
                    override fun onSetFailure(error: String?) {}
                }, MediaConstraints())

                val offer = offerDeferred.await()
                val setLocalDeferred = CompletableDeferred<Unit>()
                pc.setLocalDescription(object : SdpObserver {
                    override fun onCreateSuccess(desc: SessionDescription?) {}
                    override fun onSetSuccess() { setLocalDeferred.complete(Unit) }
                    override fun onCreateFailure(error: String?) {}
                    override fun onSetFailure(error: String?) { setLocalDeferred.completeExceptionally(RuntimeException(error)) }
                }, offer)
                setLocalDeferred.await()

                // Aguarda ICE gathering completo (com timeout de 2500ms)
                withTimeoutOrNull(2500) {
                    iceGatheringDone.await()
                }

                val localSdp = pc.localDescription?.description
                    ?: throw RuntimeException("SDP local vazia")
                val sanitizedSdp = LuceApi.sanitizeSdpOffer(localSdp, isPublish = false)

                // Envia para o WHEP
                val whepResponse = LuceApi.sendWhepOffer(baseUrl, streamName, sanitizedSdp, authToken)
                whepLocation = whepResponse.location

                // Define Answer remota
                val setRemoteDeferred = CompletableDeferred<Unit>()
                val answer = SessionDescription(SessionDescription.Type.ANSWER, whepResponse.answerSdp)
                pc.setRemoteDescription(object : SdpObserver {
                    override fun onCreateSuccess(desc: SessionDescription?) {}
                    override fun onSetSuccess() { setRemoteDeferred.complete(Unit) }
                    override fun onCreateFailure(error: String?) {}
                    override fun onSetFailure(error: String?) { setRemoteDeferred.completeExceptionally(RuntimeException(error)) }
                }, answer)
                setRemoteDeferred.await()

                isReconnecting.value = false
                isStalled.value = false
                stallSecondsCount = 0

                // Registra que estamos assistindo no roster
                LuceApi.sendWatching(baseUrl, streamName, myViewerName, authToken, on = true)

                // Inicia loops de estatísticas e heartbeat
                startStatsLoop()
                startWatchHeartbeatLoop()
            } catch (e: Exception) {
                isReconnecting.value = false
                statsText.value = "falha ao conectar: ${e.message}"
            }
        }
    }

    private fun startStatsLoop() {
        scope.launch {
            var lastBytes: Long = 0
            var lastTimeMs: Long = System.currentTimeMillis()

            while (isActive) {
                delay(1000)
                val pc = peerConnection ?: break
                pc.getStats { report ->
                    var currentBytes: Long = 0
                    var currentPackets: Long = 0
                    var packetsLost: Long = 0
                    var jitterSecs: Double = 0.0

                    for (stats in report.statsMap.values) {
                        if (stats.type == "inbound-rtp") {
                            val members = stats.members
                            val mediaType = members["mediaType"] as? String ?: members["kind"] as? String
                            if (mediaType == "video") {
                                currentBytes += (members["bytesReceived"] as? Number)?.toLong() ?: 0L
                                currentPackets += (members["packetsReceived"] as? Number)?.toLong() ?: 0L
                                packetsLost += (members["packetsLost"] as? Number)?.toLong() ?: 0L
                                jitterSecs = (members["jitter"] as? Number)?.toDouble() ?: 0.0
                            }
                        }
                    }

                    val now = System.currentTimeMillis()
                    val deltaSec = (now - lastTimeMs).coerceAtLeast(1) / 1000.0
                    val deltaBytes = (currentBytes - lastBytes).coerceAtLeast(0)
                    val deltaPkts = (currentPackets - lastPacketsReceived).coerceAtLeast(0)

                    val mbps = (deltaBytes * 8.0) / (deltaSec * 1_000_000.0)
                    val jitterMs = (jitterSecs * 1000.0).toInt()

                    statsText.value = "↓ ${"%.1f".format(mbps)} Mbps · $currentPackets pkts · $packetsLost loss · ${jitterMs}ms"

                    // Detecção de congelamento (3 segundos sem receber nenhum pacote novo)
                    if (currentPackets > 0 && deltaPkts == 0L) {
                        stallSecondsCount++
                        if (stallSecondsCount >= 3) {
                            isStalled.value = true
                            stallSecondsCount = 0
                            scope.launch { reconnect() }
                        }
                    } else if (deltaPkts > 0L) {
                        stallSecondsCount = 0
                        isStalled.value = false
                    }

                    lastBytes = currentBytes
                    lastPacketsReceived = currentPackets
                    lastTimeMs = now
                }
            }
        }
    }

    private fun startWatchHeartbeatLoop() {
        scope.launch {
            while (isActive) {
                delay(10_000)
                try {
                    LuceApi.sendWatching(baseUrl, streamName, myViewerName, authToken, on = true)
                } catch (_: Exception) {}
            }
        }
    }

    fun setMute(muted: Boolean) {
        isMuted.value = muted
        audioTrack.value?.setVolume(if (muted) 0.0 else volume.value.toDouble())
    }

    fun setVolume(vol: Float) {
        volume.value = vol.coerceIn(0.0f, 1.0f)
        if (!isMuted.value) {
            audioTrack.value?.setVolume(volume.value.toDouble())
        }
    }

    suspend fun reconnect() {
        withContext(Dispatchers.Default) {
            try {
                whepLocation?.let { loc ->
                    try { LuceApi.sendWhepDelete(baseUrl, loc, authToken) } catch (_: Exception) {}
                }
                peerConnection?.dispose()
                peerConnection = null
                connect()
            } catch (_: Exception) {}
        }
    }

    fun stop() {
        scope.launch(Dispatchers.IO) {
            try {
                LuceApi.sendWatching(baseUrl, streamName, myViewerName, authToken, on = false)
            } catch (_: Exception) {}
            whepLocation?.let { loc ->
                try { LuceApi.sendWhepDelete(baseUrl, loc, authToken) } catch (_: Exception) {}
            }
            withContext(Dispatchers.Main) {
                peerConnection?.dispose()
                peerConnection = null
                videoTrack.value = null
                audioTrack.value = null
            }
        }
    }
}

class PublishSession(
    private val context: Context,
    val streamName: String,
    val baseUrl: String,
    val authToken: String?,
    val settings: UserSettings
) {
    private val scope = CoroutineScope(Dispatchers.Default + Job())
    private var peerConnection: PeerConnection? = null
    private var whipLocation: String? = null
    private var videoCapturer: VideoCapturer? = null
    private var surfaceTextureHelper: SurfaceTextureHelper? = null
    private var videoSource: VideoSource? = null
    private var audioSource: AudioSource? = null
    private var videoTrack: VideoTrack? = null
    private var audioTrack: AudioTrack? = null

    val isLive = MutableStateFlow(false)
    val statusText = MutableStateFlow("iniciando transmissão…")
    val statsText = MutableStateFlow("")

    fun start(mediaProjectionIntent: Intent) {
        scope.launch(Dispatchers.Default) {
            try {
                val factory = WebRtcEngine.getFactory(context)
                val rtcConfig = WebRtcEngine.createRtcConfig()

                surfaceTextureHelper = SurfaceTextureHelper.create("ScreenCaptureThread", WebRtcEngine.eglBase.eglBaseContext)
                val capturer = org.webrtc.ScreenCapturerAndroid(mediaProjectionIntent, object : MediaProjection.Callback() {
                    override fun onStop() {
                        stop()
                    }
                })
                videoCapturer = capturer

                val vSource = factory.createVideoSource(/* isScreencast = */ true)
                videoSource = vSource
                capturer.initialize(surfaceTextureHelper, context.applicationContext, vSource.capturerObserver)

                val width = if (settings.resolutionWidth > 0) settings.resolutionWidth else 1280
                val height = if (settings.resolutionHeight > 0) settings.resolutionHeight else 720
                val fps = if (settings.fps > 0) settings.fps else 30
                capturer.startCapture(width, height, fps)

                val vTrack = factory.createVideoTrack("ARDAMSv0", vSource)
                vTrack.setEnabled(true)
                videoTrack = vTrack

                val aSource = factory.createAudioSource(MediaConstraints())
                audioSource = aSource
                val aTrack = factory.createAudioTrack("ARDAMSa0", aSource)
                aTrack.setEnabled(settings.audioSource != UserSettings.AUDIO_SOURCE_MUTED)
                audioTrack = aTrack

                val iceGatheringDone = CompletableDeferred<Unit>()
                val pcObserver = object : PeerConnection.Observer {
                    override fun onSignalingChange(state: PeerConnection.SignalingState?) {}
                    override fun onIceConnectionChange(state: PeerConnection.IceConnectionState?) {}
                    override fun onIceConnectionReceivingChange(receiving: Boolean) {}
                    override fun onIceGatheringChange(state: PeerConnection.IceGatheringState?) {
                        if (state == PeerConnection.IceGatheringState.COMPLETE) {
                            iceGatheringDone.complete(Unit)
                        }
                    }
                    override fun onIceCandidate(candidate: IceCandidate?) {}
                    override fun onIceCandidatesRemoved(candidates: Array<out IceCandidate>?) {}
                    override fun onAddStream(stream: MediaStream?) {}
                    override fun onRemoveStream(stream: MediaStream?) {}
                    override fun onDataChannel(channel: DataChannel?) {}
                    override fun onRenegotiationNeeded() {}
                    override fun onAddTrack(receiver: RtpReceiver?, streams: Array<out MediaStream>?) {}
                    override fun onTrack(transceiver: RtpTransceiver?) {}
                }

                val pc = factory.createPeerConnection(rtcConfig, pcObserver)
                    ?: throw RuntimeException("Falha ao criar PeerConnection de transmissão")
                peerConnection = pc

                val vSender = pc.addTrack(vTrack, listOf("ARDAMS"))
                pc.addTrack(aTrack, listOf("ARDAMS"))

                // Configura bitrate no RtpSender
                try {
                    val params = vSender.parameters
                    if (params.encodings.isNotEmpty()) {
                        params.encodings[0].maxBitrateBps = settings.bitrateKbps * 1000
                        params.encodings[0].minBitrateBps = (settings.bitrateKbps * 1000) / 4
                        vSender.parameters = params
                    }
                } catch (_: Exception) {}

                // Cria Oferta WHIP
                val offerDeferred = CompletableDeferred<SessionDescription>()
                pc.createOffer(object : SdpObserver {
                    override fun onCreateSuccess(desc: SessionDescription) { offerDeferred.complete(desc) }
                    override fun onSetSuccess() {}
                    override fun onCreateFailure(error: String?) { offerDeferred.completeExceptionally(RuntimeException(error)) }
                    override fun onSetFailure(error: String?) {}
                }, MediaConstraints())

                val offer = offerDeferred.await()
                val setLocalDeferred = CompletableDeferred<Unit>()
                pc.setLocalDescription(object : SdpObserver {
                    override fun onCreateSuccess(desc: SessionDescription?) {}
                    override fun onSetSuccess() { setLocalDeferred.complete(Unit) }
                    override fun onCreateFailure(error: String?) {}
                    override fun onSetFailure(error: String?) { setLocalDeferred.completeExceptionally(RuntimeException(error)) }
                }, offer)
                setLocalDeferred.await()

                // Aguarda ICE completo
                withTimeoutOrNull(2500) {
                    iceGatheringDone.await()
                }

                val localSdp = pc.localDescription?.description
                    ?: throw RuntimeException("SDP local de publicação vazia")
                val sanitizedSdp = LuceApi.sanitizeSdpOffer(localSdp, isPublish = true)

                // Envia WHIP POST
                val whipResponse = LuceApi.sendWhipOffer(baseUrl, streamName, sanitizedSdp, authToken)
                whipLocation = whipResponse.location

                // Define Answer remota
                val setRemoteDeferred = CompletableDeferred<Unit>()
                val answer = SessionDescription(SessionDescription.Type.ANSWER, whipResponse.answerSdp)
                pc.setRemoteDescription(object : SdpObserver {
                    override fun onCreateSuccess(desc: SessionDescription?) {}
                    override fun onSetSuccess() { setRemoteDeferred.complete(Unit) }
                    override fun onCreateFailure(error: String?) {}
                    override fun onSetFailure(error: String?) { setRemoteDeferred.completeExceptionally(RuntimeException(error)) }
                }, answer)
                setRemoteDeferred.await()

                isLive.value = true
                statusText.value = "transmitindo como $streamName (${width}x${height}@${fps}fps)"

                startWhipHeartbeatLoop()
                startPublishStatsLoop()
            } catch (e: Exception) {
                isLive.value = false
                statusText.value = "erro na transmissão: ${e.message}"
            }
        }
    }

    private fun startWhipHeartbeatLoop() {
        scope.launch {
            while (isActive) {
                delay(10_000)
                try {
                    LuceApi.sendWhipHeartbeat(baseUrl, streamName, authToken)
                } catch (_: Exception) {}
            }
        }
    }

    private fun startPublishStatsLoop() {
        scope.launch {
            var lastBytes: Long = 0
            var lastTimeMs: Long = System.currentTimeMillis()

            while (isActive) {
                delay(1000)
                val pc = peerConnection ?: break
                pc.getStats { report ->
                    var currentBytes: Long = 0
                    var currentPackets: Long = 0

                    for (stats in report.statsMap.values) {
                        if (stats.type == "outbound-rtp") {
                            val members = stats.members
                            val mediaType = members["mediaType"] as? String ?: members["kind"] as? String
                            if (mediaType == "video") {
                                currentBytes += (members["bytesSent"] as? Number)?.toLong() ?: 0L
                                currentPackets += (members["packetsSent"] as? Number)?.toLong() ?: 0L
                            }
                        }
                    }

                    val now = System.currentTimeMillis()
                    val deltaSec = (now - lastTimeMs).coerceAtLeast(1) / 1000.0
                    val deltaBytes = (currentBytes - lastBytes).coerceAtLeast(0)

                    val mbps = (deltaBytes * 8.0) / (deltaSec * 1_000_000.0)
                    statsText.value = "↑ ${"%.1f".format(mbps)} Mbps · $currentPackets pkts"

                    lastBytes = currentBytes
                    lastTimeMs = now
                }
            }
        }
    }

    fun stop() {
        scope.launch(Dispatchers.IO) {
            isLive.value = false
            statusText.value = "transmissão encerrada"
            whipLocation?.let { loc ->
                try { LuceApi.sendWhipDelete(baseUrl, loc, authToken) } catch (_: Exception) {}
            }
            try { videoCapturer?.stopCapture() } catch (_: Exception) {}
            videoCapturer?.dispose()
            surfaceTextureHelper?.dispose()
            withContext(Dispatchers.Main) {
                videoTrack?.dispose()
                audioTrack?.dispose()
                videoSource?.dispose()
                audioSource?.dispose()
                peerConnection?.dispose()
                peerConnection = null
            }
        }
    }
}
