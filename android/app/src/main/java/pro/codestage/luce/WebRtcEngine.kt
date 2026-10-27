package pro.codestage.luce

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioPlaybackCaptureConfiguration
import android.media.AudioRecord
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Process
import android.view.Surface
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
import org.webrtc.CapturerObserver
import org.webrtc.DataChannel
import org.webrtc.DefaultVideoDecoderFactory
import org.webrtc.EglBase
import org.webrtc.IceCandidate
import org.webrtc.MediaConstraints
import org.webrtc.MediaStream
import org.webrtc.MediaStreamTrack
import org.webrtc.PeerConnection
import org.webrtc.PeerConnectionFactory
import org.webrtc.RtpReceiver
import org.webrtc.SdpObserver
import org.webrtc.SessionDescription
import org.webrtc.SurfaceTextureHelper
import org.webrtc.VideoCapturer
import org.webrtc.VideoFrame
import org.webrtc.VideoSource
import org.webrtc.VideoTrack
import org.webrtc.audio.JavaAudioDeviceModule

class LuceScreenCapturer(
    private val mediaProjection: MediaProjection,
    private val onProjectionStopped: () -> Unit
) : VideoCapturer {

    private var surfaceTextureHelper: SurfaceTextureHelper? = null
    private var capturerObserver: CapturerObserver? = null
    private var virtualDisplay: VirtualDisplay? = null

    @Volatile private var lastI420Buffer: VideoFrame.I420Buffer? = null
    @Volatile private var lastRotation: Int = 0
    @Volatile private var lastFrameTimeNs: Long = 0L
    @Volatile private var lastPtsNs: Long = 0L
    private val frameLock = Any()

    private val keepaliveScope = CoroutineScope(Dispatchers.Default + Job())
    private var keepaliveJob: Job? = null

    private val projectionCallback = object : MediaProjection.Callback() {
        override fun onStop() {
            onProjectionStopped()
        }
    }

    override fun initialize(
        surfaceTextureHelper: SurfaceTextureHelper,
        applicationContext: Context,
        capturerObserver: CapturerObserver
    ) {
        this.surfaceTextureHelper = surfaceTextureHelper
        this.capturerObserver = capturerObserver
    }

    override fun startCapture(width: Int, height: Int, framerate: Int) {
        val helper = surfaceTextureHelper ?: return
        val observer = capturerObserver ?: return

        mediaProjection.registerCallback(projectionCallback, helper.handler)
        helper.setTextureSize(width, height)

        virtualDisplay = mediaProjection.createVirtualDisplay(
            "LuceScreenCapture",
            width,
            height,
            400,
            DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
            Surface(helper.surfaceTexture),
            null,
            helper.handler
        )

        observer.onCapturerStarted(true)

        helper.startListening { frame ->
            val nowNs = System.nanoTime()
            val safePtsNs = if (nowNs <= lastPtsNs) lastPtsNs + 1_000_000L else nowNs
            lastPtsNs = safePtsNs
            lastFrameTimeNs = nowNs

            // OBRIGATÓRIO: retain() antes de envolver em outro VideoFrame, pois outFrame.release()
            // vai decrementar o refCount do buffer E o SurfaceTextureHelper também vai decrementar!
            frame.buffer.retain()
            val outFrame = VideoFrame(frame.buffer, frame.rotation, safePtsNs)
            observer.onFrameCaptured(outFrame)
            outFrame.release()

            // Atualiza cópia I420 independente (a cada ~250ms) para o keepalive não prender a textura GL
            if (lastI420Buffer == null || nowNs - lastFrameTimeNs == 0L) {
                val i420 = runCatching { frame.buffer.toI420() }.getOrNull()
                if (i420 != null) {
                    synchronized(frameLock) {
                        val old = lastI420Buffer
                        lastI420Buffer = i420
                        lastRotation = frame.rotation
                        old?.release()
                    }
                }
            }
        }

        // Keepalive limpo (sem reflexão): se a tela do celular ficar parada por >100ms,
        // reenvia o último buffer I420 com timestamp novo para o encoder H.264 nunca parar.
        keepaliveJob?.cancel()
        keepaliveJob = keepaliveScope.launch {
            while (isActive) {
                delay(100)
                if (System.nanoTime() - lastFrameTimeNs >= 100_000_000L) {
                    val buf: VideoFrame.I420Buffer?
                    val rot: Int
                    synchronized(frameLock) {
                        buf = lastI420Buffer
                        buf?.retain()
                        rot = lastRotation
                    }
                    if (buf != null) {
                        val nowNs = System.nanoTime()
                        val safePtsNs = if (nowNs <= lastPtsNs) lastPtsNs + 1_000_000L else nowNs
                        lastPtsNs = safePtsNs
                        val repeatFrame = VideoFrame(buf, rot, safePtsNs)
                        try {
                            observer.onFrameCaptured(repeatFrame)
                        } catch (_: Exception) {}
                        repeatFrame.release()
                    }
                }
            }
        }
    }

    override fun stopCapture() {
        keepaliveJob?.cancel()
        keepaliveJob = null
        val helper = surfaceTextureHelper
        if (helper != null) {
            val done = java.util.concurrent.CountDownLatch(1)
            helper.handler.post {
                try { helper.stopListening() } catch (_: Exception) {}
                done.countDown()
            }
            runCatching { done.await(1000, java.util.concurrent.TimeUnit.MILLISECONDS) }
        }
        try { capturerObserver?.onCapturerStopped() } catch (_: Exception) {}
        try { virtualDisplay?.release() } catch (_: Exception) {}
        virtualDisplay = null
        try { mediaProjection.unregisterCallback(projectionCallback) } catch (_: Exception) {}
        synchronized(frameLock) {
            lastI420Buffer?.release()
            lastI420Buffer = null
        }
    }

    override fun changeCaptureFormat(width: Int, height: Int, framerate: Int) {
        surfaceTextureHelper?.setTextureSize(width, height)
        virtualDisplay?.resize(width, height, 400)
    }

    override fun dispose() {
        stopCapture()
    }

    override fun isScreencast(): Boolean = false
}

object WebRtcEngine {
    val eglBase: EglBase by lazy { EglBase.create() }

    var activeMediaProjection: MediaProjection? = null

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
                .setUseHardwareAcousticEchoCanceler(false)
                .setUseHardwareNoiseSuppressor(false)
                .setAudioRecordStateCallback(object : JavaAudioDeviceModule.AudioRecordStateCallback {
                    override fun onWebRtcAudioRecordStart() {
                        attachSystemAudioCapture()
                    }
                    override fun onWebRtcAudioRecordStop() {}
                })
                .createAudioDeviceModule()
            adm = audioDeviceModule

            factory = PeerConnectionFactory.builder()
                .setOptions(PeerConnectionFactory.Options())
                .setAudioDeviceModule(audioDeviceModule)
                .setVideoEncoderFactory(org.webrtc.LuceVideoEncoderFactory(eglBase.eglBaseContext))
                .setVideoDecoderFactory(DefaultVideoDecoderFactory(eglBase.eglBaseContext))
                .createPeerConnectionFactory()
        }
        return factory!!
    }

    fun setMicrophoneMute(muted: Boolean) {
        adm?.setMicrophoneMute(muted)
    }

    fun attachSystemAudioCapture() {
        val projection = activeMediaProjection ?: return
        val currentAdm = adm ?: return
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return
        try {
            val audioInputField = currentAdm.javaClass.declaredFields.firstOrNull {
                it.type.name.endsWith("WebRtcAudioRecord")
            } ?: return
            audioInputField.isAccessible = true
            val audioInput = audioInputField.get(currentAdm) ?: return

            val audioRecordField = audioInput.javaClass.declaredFields.firstOrNull {
                it.type == AudioRecord::class.java
            } ?: return
            audioRecordField.isAccessible = true
            val oldRecord = audioRecordField.get(audioInput) as? AudioRecord ?: return

            val sampleRate = oldRecord.sampleRate
            val channelConfig = oldRecord.channelConfiguration
            val channelCount = oldRecord.channelCount

            val playbackConfig = AudioPlaybackCaptureConfiguration.Builder(projection)
                .addMatchingUsage(AudioAttributes.USAGE_MEDIA)
                .addMatchingUsage(AudioAttributes.USAGE_GAME)
                .addMatchingUsage(AudioAttributes.USAGE_UNKNOWN)
                .excludeUid(Process.myUid())
                .build()

            val audioFormat = AudioFormat.Builder()
                .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                .setSampleRate(sampleRate)
                .setChannelMask(channelConfig)
                .build()

            val minBuf = AudioRecord.getMinBufferSize(
                sampleRate,
                channelConfig,
                AudioFormat.ENCODING_PCM_16BIT
            ).coerceAtLeast(oldRecord.bufferSizeInFrames * channelCount * 2)

            val sysRecord = AudioRecord.Builder()
                .setAudioPlaybackCaptureConfig(playbackConfig)
                .setAudioFormat(audioFormat)
                .setBufferSizeInBytes(minBuf)
                .build()

            if (sysRecord.state == AudioRecord.STATE_INITIALIZED) {
                try { oldRecord.stop() } catch (_: Exception) {}
                try { oldRecord.release() } catch (_: Exception) {}
                sysRecord.startRecording()
                audioRecordField.set(audioInput, sysRecord)
            } else {
                sysRecord.release()
            }
        } catch (_: Exception) {}
    }

    fun createRtcConfig(): PeerConnection.RTCConfiguration {
        val iceServers = listOf(
            PeerConnection.IceServer.builder("stun:stun.cloudflare.com:3478").createIceServer(),
            PeerConnection.IceServer.builder("stun:stun.l.google.com:19302").createIceServer()
        )
        return PeerConnection.RTCConfiguration(iceServers).apply {
            sdpSemantics = PeerConnection.SdpSemantics.UNIFIED_PLAN
            continualGatheringPolicy = PeerConnection.ContinualGatheringPolicy.GATHER_ONCE
            bundlePolicy = PeerConnection.BundlePolicy.MAXBUNDLE
            rtcpMuxPolicy = PeerConnection.RtcpMuxPolicy.REQUIRE
            tcpCandidatePolicy = PeerConnection.TcpCandidatePolicy.ENABLED
        }
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
                    override fun onAddTrack(receiver: RtpReceiver?, streams: Array<out MediaStream>?) {
                        val track = receiver?.track() ?: return
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

                // Adiciona transceivers para receber áudio e vídeo
                pc.addTransceiver(MediaStreamTrack.MediaType.MEDIA_TYPE_VIDEO)
                pc.addTransceiver(MediaStreamTrack.MediaType.MEDIA_TYPE_AUDIO)

                // Cria Oferta WHEP
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

                // Aguarda gathering completo (ou timeout 2500ms)
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

                    for (stat in report.statsMap.values) {
                        if (stat.type == "inbound-rtp") {
                            val members = stat.members
                            val mediaType = members["mediaType"] as? String ?: members["kind"] as? String
                            if (mediaType == "video") {
                                currentBytes += (members["bytesReceived"] as? Number)?.toLong() ?: 0L
                                currentPackets += (members["packetsReceived"] as? Number)?.toLong() ?: 0L
                            }
                        }
                    }

                    val now = System.currentTimeMillis()
                    val deltaSec = (now - lastTimeMs).coerceAtLeast(1) / 1000.0
                    val deltaBytes = (currentBytes - lastBytes).coerceAtLeast(0)

                    val mbps = (deltaBytes * 8.0) / (deltaSec * 1_000_000.0)
                    statsText.value = "↓ ${"%.1f".format(mbps)} Mbps · $currentPackets pkts"

                    // Detecção de congelamento (stalling) se os pacotes pararem por mais de 3 segundos
                    if (currentPackets > 0 && currentPackets == lastPacketsReceived) {
                        stallSecondsCount++
                        if (stallSecondsCount >= 3) {
                            isStalled.value = true
                            if (stallSecondsCount >= 5) {
                                scope.launch { reconnect() }
                            }
                        }
                    } else {
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
                delay(10000)
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
    private var videoTrack: VideoTrack? = null
    private var audioSource: AudioSource? = null
    private var audioTrack: AudioTrack? = null

    private val isStopped = java.util.concurrent.atomic.AtomicBoolean(false)

    val isLive = MutableStateFlow(false)
    val statusText = MutableStateFlow("iniciando transmissão…")
    val statsText = MutableStateFlow("")

    fun start(mediaProjectionIntent: Intent) {
        scope.launch {
            try {
                statusText.value = "preparando captura…"
                val factory = WebRtcEngine.getFactory(context)

                val mpManager = context.getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
                val projection = mpManager.getMediaProjection(Activity.RESULT_OK, mediaProjectionIntent)
                    ?: throw RuntimeException("Falha ao obter MediaProjection")

                WebRtcEngine.activeMediaProjection = projection
                WebRtcEngine.setMicrophoneMute(settings.audioSource == UserSettings.AUDIO_SOURCE_MUTED)

                // Detecta orientação atual da tela e ajusta para retrato ou paisagem
                val metrics = context.resources.displayMetrics
                val isPortrait = metrics.heightPixels > metrics.widthPixels

                var targetWidth = settings.resolutionWidth
                var targetHeight = settings.resolutionHeight

                if (isPortrait) {
                    val minDim = minOf(targetWidth, targetHeight)
                    val maxDim = maxOf(targetWidth, targetHeight)
                    targetWidth = minDim
                    targetHeight = maxDim
                } else {
                    val minDim = minOf(targetWidth, targetHeight)
                    val maxDim = maxOf(targetWidth, targetHeight)
                    targetWidth = maxDim
                    targetHeight = minDim
                }

                // Garante que as dimensões sejam múltiplos de 16 para o encoder de vídeo
                targetWidth = (targetWidth / 16) * 16
                targetHeight = (targetHeight / 16) * 16

                val capturer = LuceScreenCapturer(projection) {
                    stop()
                }
                videoCapturer = capturer

                val surfaceHelper = SurfaceTextureHelper.create("LuceScreenCaptureThread", WebRtcEngine.eglBase.eglBaseContext)
                surfaceTextureHelper = surfaceHelper

                val vSource = factory.createVideoSource(false)
                vSource.adaptOutputFormat(targetWidth, targetHeight, settings.fps)
                videoSource = vSource
                capturer.initialize(surfaceHelper, context.applicationContext, vSource.capturerObserver)
                capturer.startCapture(targetWidth, targetHeight, settings.fps)

                val vTrack = factory.createVideoTrack("ARDAMSv0", vSource)
                videoTrack = vTrack

                val aSource = factory.createAudioSource(MediaConstraints())
                audioSource = aSource
                val aTrack = factory.createAudioTrack("ARDAMSa0", aSource)
                audioTrack = aTrack
                aTrack.setEnabled(settings.audioSource != UserSettings.AUDIO_SOURCE_MUTED)

                statusText.value = "conectando ao servidor…"

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
                }

                val pc = factory.createPeerConnection(rtcConfig, pcObserver)
                    ?: throw RuntimeException("Falha ao criar PeerConnection de transmissão")
                peerConnection = pc

                val vSender = pc.addTrack(vTrack, listOf("ARDAMS"))
                pc.addTrack(aTrack, listOf("ARDAMS"))

                // Força preferências de codec H264 no transceiver de vídeo
                try {
                    val videoTransceiver = pc.transceivers.firstOrNull {
                        it.mediaType == MediaStreamTrack.MediaType.MEDIA_TYPE_VIDEO
                    }
                    if (videoTransceiver != null) {
                        val caps = factory.getRtpSenderCapabilities(MediaStreamTrack.MediaType.MEDIA_TYPE_VIDEO)
                        val h264Codecs = caps.codecs.filter {
                            it.name.equals("H264", ignoreCase = true) || it.mimeType.contains("h264", ignoreCase = true)
                        }
                        if (h264Codecs.isNotEmpty()) {
                            videoTransceiver.setCodecPreferences(h264Codecs)
                        }
                    }
                } catch (_: Exception) {}

                // Configura bitrate no RtpSender
                try {
                    val params = vSender.parameters
                    params.degradationPreference = org.webrtc.RtpParameters.DegradationPreference.MAINTAIN_FRAMERATE
                    if (params.encodings.isNotEmpty()) {
                        params.encodings[0].maxBitrateBps = settings.bitrateKbps * 1000
                        params.encodings[0].minBitrateBps = (settings.bitrateKbps * 1000) / 2
                        params.encodings[0].maxFramerate = settings.fps
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

                // Aguarda gathering completo (ou timeout 2500ms)
                withTimeoutOrNull(2500) {
                    iceGatheringDone.await()
                }

                val localSdp = pc.localDescription?.description
                    ?: throw RuntimeException("SDP local vazia")
                val sanitizedSdp = LuceApi.sanitizeSdpOffer(localSdp, isPublish = true)

                // Envia para o WHIP
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
                statusText.value = "transmitindo ao vivo"

                startStatsLoop()
                startWhipHeartbeatLoop()
            } catch (e: Exception) {
                isLive.value = false
                statusText.value = "falha ao iniciar: ${e.message}"
                stop()
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

                    for (stat in report.statsMap.values) {
                        if (stat.type == "outbound-rtp") {
                            val members = stat.members
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

    private fun startWhipHeartbeatLoop() {
        scope.launch {
            while (isActive) {
                delay(8000)
                try {
                    LuceApi.sendWhipHeartbeat(baseUrl, streamName, authToken)
                } catch (_: Exception) {}
            }
        }
    }

    fun stop() {
        if (!isStopped.compareAndSet(false, true)) return
        scope.launch(Dispatchers.IO) {
            isLive.value = false
            statusText.value = "transmissão encerrada"
            whipLocation?.let { loc ->
                try { LuceApi.sendWhipDelete(baseUrl, loc, authToken) } catch (_: Exception) {}
            }
            try { videoCapturer?.stopCapture() } catch (_: Exception) {}

            withContext(Dispatchers.Main) {
                try { peerConnection?.dispose() } catch (_: Exception) {}
                peerConnection = null

                try { videoTrack?.dispose() } catch (_: Exception) {}
                videoTrack = null
                try { audioTrack?.dispose() } catch (_: Exception) {}
                audioTrack = null
                try { videoSource?.dispose() } catch (_: Exception) {}
                videoSource = null
                try { audioSource?.dispose() } catch (_: Exception) {}
                audioSource = null
                try { videoCapturer?.dispose() } catch (_: Exception) {}
                videoCapturer = null
                try { surfaceTextureHelper?.dispose() } catch (_: Exception) {}
                surfaceTextureHelper = null
                try { WebRtcEngine.activeMediaProjection?.stop() } catch (_: Exception) {}
                WebRtcEngine.activeMediaProjection = null
            }
        }
    }
}
