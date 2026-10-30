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
import org.webrtc.RtpTransceiver
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

    @Volatile private var lastFrameTimeNs: Long = 0L
    @Volatile private var lastPtsNs: Long = 0L
    @Volatile private var isCapturing: Boolean = false

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

        isCapturing = true
        mediaProjection.registerCallback(projectionCallback, helper.handler)
        helper.setTextureSize(width, height)

        virtualDisplay = mediaProjection.createVirtualDisplay(
            "LuceScreenCapture",
            width,
            height,
            320,
            DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
            Surface(helper.surfaceTexture),
            null,
            helper.handler
        )

        observer.onCapturerStarted(true)

        helper.startListening { frame ->
            if (!isCapturing) return@startListening
            val nowNs = System.nanoTime()
            val safePtsNs = if (nowNs <= lastPtsNs) lastPtsNs + 1_000_000L else nowNs
            lastPtsNs = safePtsNs
            lastFrameTimeNs = nowNs

            frame.buffer.retain()
            val outFrame = VideoFrame(frame.buffer, frame.rotation, safePtsNs)
            observer.onFrameCaptured(outFrame)
            outFrame.release()
        }

        // Tela parada não gera frames: reentrega a textura atual pelo próprio helper (mesmo TextureBuffer, com controle
        // de isTextureInUse), em vez de montar um TextureBufferImpl na mão que o codec lia enquanto o helper trocava a textura.
        keepaliveJob?.cancel()
        keepaliveJob = keepaliveScope.launch {
            while (isActive && isCapturing) {
                delay(IDLE_REPEAT_MS)
                if (lastFrameTimeNs > 0L && System.nanoTime() - lastFrameTimeNs >= IDLE_REPEAT_MS * 1_000_000L) {
                    helper.forceFrame()
                }
            }
        }
    }

    override fun stopCapture() {
        isCapturing = false
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
    }

    /** Rotação ao vivo: o buffer do helper e o VirtualDisplay só podem ser redimensionados na thread do helper. */
    override fun changeCaptureFormat(width: Int, height: Int, framerate: Int) {
        val helper = surfaceTextureHelper ?: return
        helper.handler.post {
            if (!isCapturing) return@post
            helper.setTextureSize(width, height)
            virtualDisplay?.resize(width, height, 320)
        }
    }

    override fun dispose() {
        stopCapture()
    }

    override fun isScreencast(): Boolean = true

    private companion object {
        const val IDLE_REPEAT_MS = 66L // piso de ~15 fps com a tela parada
    }
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
                .setAudioSource(android.media.MediaRecorder.AudioSource.DEFAULT)
                .setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_MOVIE)
                        .build()
                )
                .setUseHardwareAcousticEchoCanceler(false)
                .setUseHardwareNoiseSuppressor(false)
                .setUseStereoInput(true)
                .setUseStereoOutput(true)
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
                sysRecord.startRecording()
                audioRecordField.set(audioInput, sysRecord)
                CoroutineScope(Dispatchers.IO).launch {
                    delay(60)
                    try { oldRecord.stop() } catch (_: Exception) {}
                    try { oldRecord.release() } catch (_: Exception) {}
                }
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

    /** Proporção real do vídeo recebido: o transmissor pode estar em retrato (celular) ou girar ao vivo. */
    val videoAspectRatio = MutableStateFlow(16f / 9f)
    val isPortraitVideo = MutableStateFlow(false)

    private val frameSizeSink = org.webrtc.VideoSink { frame ->
        onFrameResolution(frame.rotatedWidth, frame.rotatedHeight, 0)
    }

    fun onFrameResolution(width: Int, height: Int, rotation: Int) {
        if (width <= 0 || height <= 0) return
        val swap = rotation % 180 != 0
        val effectiveW = if (swap) height else width
        val effectiveH = if (swap) width else height
        val ratio = (effectiveW.toFloat() / effectiveH).coerceIn(0.35f, 3.0f)
        if (videoAspectRatio.value != ratio) videoAspectRatio.value = ratio
        val portrait = effectiveH > effectiveW
        if (isPortraitVideo.value != portrait) isPortraitVideo.value = portrait
    }

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
                            track.addSink(frameSizeSink)
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

                // Só recebe, e na mesma ordem do GStreamer (m=audio, m=video): o SFU casa as tracks por posição
                val recvOnlyInit = RtpTransceiver.RtpTransceiverInit(RtpTransceiver.RtpTransceiverDirection.RECV_ONLY)
                val audioTransceiver = pc.addTransceiver(MediaStreamTrack.MediaType.MEDIA_TYPE_AUDIO, recvOnlyInit)
                // só Opus: com G722/PCMU/CN/telephone-event na m=audio o SFU escolhe o último payload e falha
                val opusCodecs = factory.getRtpReceiverCapabilities(MediaStreamTrack.MediaType.MEDIA_TYPE_AUDIO)
                    .codecs.filter { it.name.equals("opus", ignoreCase = true) }
                if (opusCodecs.isNotEmpty()) {
                    audioTransceiver.setCodecPreferences(opusCodecs)
                }
                val videoTransceiver = pc.addTransceiver(MediaStreamTrack.MediaType.MEDIA_TYPE_VIDEO, recvOnlyInit)
                val h264Codecs = factory.getRtpReceiverCapabilities(MediaStreamTrack.MediaType.MEDIA_TYPE_VIDEO)
                    .codecs.filter { it.name.equals("H264", ignoreCase = true) || it.name.equals("rtx", ignoreCase = true) }
                if (h264Codecs.any { it.name.equals("H264", ignoreCase = true) }) {
                    videoTransceiver.setCodecPreferences(h264Codecs)
                }

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
                val whepResponse = LuceApi.sendWhepOffer(baseUrl, streamName, sanitizedSdp, authToken, myViewerName)
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
                try { videoTrack.value?.removeSink(frameSizeSink) } catch (_: Exception) {}
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

    private var displayListener: DisplayManager.DisplayListener? = null
    private var capturePortrait = false

    /** Lado menor = menor entre largura/altura das configurações; lado maior na proporção real da tela; ambos múltiplos de 16. */
    private fun computeCaptureDimensions(): Pair<Int, Int> {
        val wm = context.getSystemService(Context.WINDOW_SERVICE) as android.view.WindowManager
        val real = android.util.DisplayMetrics()
        @Suppress("DEPRECATION")
        wm.defaultDisplay.getRealMetrics(real)
        val minPhysical = minOf(real.widthPixels, real.heightPixels).coerceAtLeast(1)
        val maxPhysical = maxOf(real.widthPixels, real.heightPixels)
        val shortSide = minOf(settings.resolutionWidth, settings.resolutionHeight)
        val longSide = (shortSide.toLong() * maxPhysical / minPhysical).toInt()
        val s = (shortSide / 16) * 16
        val l = (longSide / 16) * 16
        return if (real.heightPixels > real.widthPixels) Pair(s, l) else Pair(l, s)
    }

    /** Girou o celular: refaz a captura com os lados trocados (o encoder reinicia sozinho ao ver o novo tamanho). */
    private fun startRotationWatcher(capturer: LuceScreenCapturer) {
        val dm = context.getSystemService(Context.DISPLAY_SERVICE) as DisplayManager
        val listener = object : DisplayManager.DisplayListener {
            override fun onDisplayAdded(displayId: Int) {}
            override fun onDisplayRemoved(displayId: Int) {}
            override fun onDisplayChanged(displayId: Int) {
                if (displayId != android.view.Display.DEFAULT_DISPLAY || isStopped.get()) return
                val (w, h) = computeCaptureDimensions()
                val portrait = h > w
                if (portrait == capturePortrait) return
                capturePortrait = portrait
                videoSource?.adaptOutputFormat(w, h, settings.fps)
                capturer.changeCaptureFormat(w, h, settings.fps)
            }
        }
        dm.registerDisplayListener(listener, android.os.Handler(android.os.Looper.getMainLooper()))
        displayListener = listener
    }

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

                val (targetWidth, targetHeight) = computeCaptureDimensions()
                capturePortrait = targetHeight > targetWidth

                val capturer = LuceScreenCapturer(projection) {
                    stop()
                }
                videoCapturer = capturer

                val surfaceHelper = SurfaceTextureHelper.create("LuceScreenCaptureThread", WebRtcEngine.eglBase.eglBaseContext)
                surfaceTextureHelper = surfaceHelper

                val vSource = factory.createVideoSource(true)
                vSource.adaptOutputFormat(targetWidth, targetHeight, settings.fps)
                videoSource = vSource
                capturer.initialize(surfaceHelper, context.applicationContext, vSource.capturerObserver)
                capturer.startCapture(targetWidth, targetHeight, settings.fps)
                startRotationWatcher(capturer)

                val vTrack = factory.createVideoTrack("ARDAMSv0", vSource)
                videoTrack = vTrack

                val audioConstraints = MediaConstraints().apply {
                    mandatory.add(MediaConstraints.KeyValuePair("googEchoCancellation", "false"))
                    mandatory.add(MediaConstraints.KeyValuePair("googAutoGainControl", "false"))
                    mandatory.add(MediaConstraints.KeyValuePair("googHighpassFilter", "false"))
                    mandatory.add(MediaConstraints.KeyValuePair("googNoiseSuppression", "false"))
                }
                val aSource = factory.createAudioSource(audioConstraints)
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
                val aSender = pc.addTrack(aTrack, listOf("ARDAMS"))

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

                // Áudio só Opus, como no espectador
                try {
                    val audioTransceiver = pc.transceivers.firstOrNull {
                        it.mediaType == MediaStreamTrack.MediaType.MEDIA_TYPE_AUDIO
                    }
                    if (audioTransceiver != null) {
                        val opusCodecs = factory.getRtpSenderCapabilities(MediaStreamTrack.MediaType.MEDIA_TYPE_AUDIO)
                            .codecs.filter { it.name.equals("opus", ignoreCase = true) }
                        if (opusCodecs.isNotEmpty()) {
                            audioTransceiver.setCodecPreferences(opusCodecs)
                        }
                    }
                } catch (_: Exception) {}

                // Configura bitrate de vídeo no RtpSender
                try {
                    val params = vSender.parameters
                    // tela compartilhada: perde fps antes de perder nitidez do texto
                    params.degradationPreference = org.webrtc.RtpParameters.DegradationPreference.MAINTAIN_RESOLUTION
                    if (params.encodings.isNotEmpty()) {
                        params.encodings[0].maxBitrateBps = settings.bitrateKbps * 1000
                        params.encodings[0].minBitrateBps = (settings.bitrateKbps * 1000 * 2) / 3
                        params.encodings[0].maxFramerate = settings.fps
                        vSender.parameters = params
                    }
                } catch (_: Exception) {}

                // Configura bitrate de áudio no RtpSender
                try {
                    val aParams = aSender.parameters
                    if (aParams.encodings.isNotEmpty()) {
                        aParams.encodings[0].maxBitrateBps = settings.audioBitrateKbps * 1000
                        aSender.parameters = aParams
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
                val sanitizedSdp = LuceApi.sanitizeSdpOffer(localSdp, isPublish = true, audioBitrateKbps = settings.audioBitrateKbps)

                // Envia para o WHIP
                val whipResponse = LuceApi.sendWhipOffer(baseUrl, streamName, sanitizedSdp, authToken, streamName)
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
                    LuceApi.sendWhipHeartbeat(baseUrl, streamName, authToken, streamName)
                } catch (e: SecurityException) {
                    statusText.value = "permissão revogada (401)"
                    stop()
                    break
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
            displayListener?.let {
                try { (context.getSystemService(Context.DISPLAY_SERVICE) as DisplayManager).unregisterDisplayListener(it) } catch (_: Exception) {}
            }
            displayListener = null
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
