package org.webrtc

import android.media.MediaCodecInfo
import android.media.MediaCodecList
import android.os.Build

class LuceVideoEncoderFactory(
    private val eglContext: EglBase.Context?
) : VideoEncoderFactory {

    companion object {
        private const val H264_MIME = "video/avc"

        private val PARAMS_42E01F: Map<String, String> = mapOf(
            "level-asymmetry-allowed" to "1",
            "packetization-mode" to "1",
            "profile-level-id" to "42e01f"
        )

        private val PARAMS_42001F: Map<String, String> = mapOf(
            "level-asymmetry-allowed" to "1",
            "packetization-mode" to "1",
            "profile-level-id" to "42001f"
        )

        fun findH264Encoder(): MediaCodecInfo? {
            val codecList = MediaCodecList(MediaCodecList.ALL_CODECS)
            val candidates = mutableListOf<MediaCodecInfo>()

            for (info in codecList.codecInfos) {
                if (!info.isEncoder) continue
                val types = info.supportedTypes
                if (types.any { it.equals(H264_MIME, ignoreCase = true) }) {
                    candidates.add(info)
                }
            }

            if (candidates.isEmpty()) return null

            return candidates.maxByOrNull { info ->
                val name = info.name.lowercase()
                val isSoftware = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    info.isSoftwareOnly
                } else {
                    name.startsWith("omx.google.") || name.startsWith("c2.android.") || name.contains("sw")
                }

                when {
                    name.startsWith("c2.qti.") || name.startsWith("omx.qcom.") -> 100
                    name.startsWith("c2.exynos.") || name.startsWith("omx.exynos.") -> 95
                    name.startsWith("c2.mtk.") || name.startsWith("omx.mtk.") -> 90
                    !isSoftware -> 80
                    name.startsWith("c2.android.avc.encoder") -> 20
                    name.startsWith("omx.google.h264.encoder") -> 10
                    else -> 0
                }
            }
        }
    }

    override fun getSupportedCodecs(): Array<VideoCodecInfo> {
        val encoder = findH264Encoder() ?: return emptyArray()
        return arrayOf(
            VideoCodecInfo("H264", PARAMS_42E01F, emptyList<String>()),
            VideoCodecInfo("H264", PARAMS_42001F, emptyList<String>())
        )
    }

    override fun getImplementations(): Array<VideoCodecInfo> = getSupportedCodecs()

    override fun createEncoder(inputCodecInfo: VideoCodecInfo): VideoEncoder? {
        if (!inputCodecInfo.name.equals("H264", ignoreCase = true)) {
            return null
        }

        val info = findH264Encoder() ?: return null
        val codecName = info.name
        val caps = info.getCapabilitiesForType(H264_MIME)

        val surfaceColorFormat = MediaCodecUtils.selectColorFormat(
            MediaCodecUtils.TEXTURE_COLOR_FORMATS, caps
        )
        val yuvColorFormat = MediaCodecUtils.selectColorFormat(
            MediaCodecUtils.ENCODER_COLOR_FORMATS, caps
        )

        if (surfaceColorFormat == null && yuvColorFormat == null) {
            return null
        }

        val bitrateAdjuster: BitrateAdjuster = if (
            codecName.startsWith("OMX.Exynos.", ignoreCase = true) ||
            codecName.startsWith("c2.exynos.", ignoreCase = true)
        ) {
            FramerateBitrateAdjuster()
        } else {
            BaseBitrateAdjuster()
        }

        val sharedContext: EglBase14.Context? = if (eglContext is EglBase14.Context) eglContext else null

        return HardwareVideoEncoder(
            MediaCodecWrapperFactoryImpl(),
            codecName,
            VideoCodecMimeType.H264,
            surfaceColorFormat,
            yuvColorFormat,
            inputCodecInfo.params,
            1,      // keyFrameIntervalSec = 1s
            1000,   // forceKeyFrameIntervalMs = 1000ms
            bitrateAdjuster,
            sharedContext
        )
    }
}
