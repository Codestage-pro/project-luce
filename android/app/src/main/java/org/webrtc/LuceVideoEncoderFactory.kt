package org.webrtc

import android.media.MediaCodecInfo
import android.media.MediaCodecList
import android.os.Build
import pro.codestage.luce.LuceLog

class LuceVideoEncoderFactory(
    private val eglContext: EglBase.Context?
) : VideoEncoderFactory {

    companion object {
        private const val TAG = "Encoder"
        private const val H264_MIME = "video/avc"

        private fun h264Params(profileLevelId: String): Map<String, String> = mapOf(
            "level-asymmetry-allowed" to "1",
            "packetization-mode" to "1",
            "profile-level-id" to profileLevelId
        )

        // Level 3.1 (42e01f/42001f) e Level 4.1 (42e029/420029) para 1080p
        private val PARAMS_42E01F = h264Params("42e01f")
        private val PARAMS_42001F = h264Params("42001f")
        private val PARAMS_42E029 = h264Params("42e029")
        private val PARAMS_420029 = h264Params("420029")

        private fun isSoftware(info: MediaCodecInfo): Boolean {
            val name = info.name.lowercase()
            return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                info.isSoftwareOnly
            } else {
                name.startsWith("omx.google.") || name.startsWith("c2.android.") || name.contains("sw")
            }
        }

        private fun h264Encoders(): List<MediaCodecInfo> =
            MediaCodecList(MediaCodecList.ALL_CODECS).codecInfos.filter { info ->
                info.isEncoder && info.supportedTypes.any { it.equals(H264_MIME, ignoreCase = true) }
            }

        fun findH264Encoder(): MediaCodecInfo? {
            return h264Encoders().maxByOrNull { info ->
                val name = info.name.lowercase()
                when {
                    name.startsWith("c2.qti.") || name.startsWith("omx.qcom.") -> 100
                    name.startsWith("c2.exynos.") || name.startsWith("omx.exynos.") -> 95
                    name.startsWith("c2.mtk.") || name.startsWith("omx.mtk.") -> 90
                    !isSoftware(info) -> 80
                    name.startsWith("c2.android.avc.encoder") -> 20
                    name.startsWith("omx.google.h264.encoder") -> 10
                    else -> 0
                }
            }
        }

        /** Codec H.264 por software (MediaCodec) usado como reserva quando o de hardware falha em initEncode. */
        private fun findSoftwareH264Encoder(): MediaCodecInfo? = h264Encoders().firstOrNull { isSoftware(it) }

        /**
         * Reduz (mantendo a proporção e múltiplos de 16) até o encoder H.264 aceitar o tamanho.
         * Evita tela preta / 0 pacotes em SoCs de entrada que rejeitam resoluções acima da sua capacidade.
         */
        fun clampToEncoderCapabilities(width: Int, height: Int): Pair<Int, Int> {
            val videoCaps = try {
                findH264Encoder()?.getCapabilitiesForType(H264_MIME)?.videoCapabilities
            } catch (e: Exception) {
                LuceLog.w(TAG, "Sem VideoCapabilities para validar ${width}x$height: ${e.message}")
                null
            } ?: return Pair(width, height)

            val wAlign = maxOf(16, videoCaps.widthAlignment)
            val hAlign = maxOf(16, videoCaps.heightAlignment)
            var w = width
            var h = height
            var guard = 0
            while (!videoCaps.isSizeSupported(w, h) && !videoCaps.isSizeSupported(h, w) && guard++ < 24) {
                val nextW = (((w * 0.9).toInt()) / wAlign) * wAlign
                val nextH = (((nextW.toLong() * h / w).toInt()) / hAlign) * hAlign
                if (nextW < 160 || nextH < 90) break
                w = nextW
                h = nextH
            }
            if (w != width || h != height) {
                LuceLog.w(TAG, "Resolução ${width}x$height não suportada pelo encoder; reduzida para ${w}x$h")
            }
            return Pair(w, h)
        }
    }

    override fun getSupportedCodecs(): Array<VideoCodecInfo> {
        findH264Encoder() ?: return emptyArray()
        return arrayOf(
            VideoCodecInfo("H264", PARAMS_42E029, emptyList<String>()),
            VideoCodecInfo("H264", PARAMS_420029, emptyList<String>()),
            VideoCodecInfo("H264", PARAMS_42E01F, emptyList<String>()),
            VideoCodecInfo("H264", PARAMS_42001F, emptyList<String>())
        )
    }

    override fun getImplementations(): Array<VideoCodecInfo> = getSupportedCodecs()

    override fun createEncoder(inputCodecInfo: VideoCodecInfo): VideoEncoder? {
        if (!inputCodecInfo.name.equals("H264", ignoreCase = true)) {
            return null
        }

        val info = findH264Encoder()
        if (info == null) {
            LuceLog.e(TAG, "Nenhum encoder H.264 disponível no aparelho")
            return null
        }
        logCapabilities(info)

        val primary = buildEncoder(info) ?: return null
        LuceLog.i(TAG, "createEncoder: usando ${info.name} (profile-level-id=${inputCodecInfo.params["profile-level-id"]})")

        // reserva automática: se o HardwareVideoEncoder falhar em initEncode, o libwebrtc troca para o fallback
        val fallback: VideoEncoder? = try {
            SoftwareVideoEncoderFactory().createEncoder(inputCodecInfo)
        } catch (e: Exception) {
            null
        } ?: findSoftwareH264Encoder()?.takeIf { it.name != info.name }?.let { buildEncoder(it) }

        if (fallback == null) {
            LuceLog.w(TAG, "Sem encoder de reserva por software; falha do hardware não terá fallback")
            return primary
        }
        LuceLog.i(TAG, "Fallback de software registrado (${fallback.implementationName})")
        return VideoEncoderFallback(fallback, primary)
    }

    private fun buildEncoder(info: MediaCodecInfo): VideoEncoder? {
        val caps = try {
            info.getCapabilitiesForType(H264_MIME)
        } catch (e: Exception) {
            LuceLog.e(TAG, "Capabilities inválidas para ${info.name}", e)
            return null
        }
        val surfaceColorFormat = MediaCodecUtils.selectColorFormat(MediaCodecUtils.TEXTURE_COLOR_FORMATS, caps)
        val yuvColorFormat = MediaCodecUtils.selectColorFormat(MediaCodecUtils.ENCODER_COLOR_FORMATS, caps)
        if (surfaceColorFormat == null && yuvColorFormat == null) {
            LuceLog.w(TAG, "${info.name} sem formato de cor compatível (surface/yuv)")
            return null
        }

        val sharedContext: EglBase14.Context? = if (eglContext is EglBase14.Context) eglContext else null

        return HardwareVideoEncoder(
            MediaCodecWrapperFactoryImpl(),
            info.name,
            VideoCodecMimeType.H264,
            surfaceColorFormat,
            yuvColorFormat,
            emptyMap(), // evita CodecException em MediaCodec.configure() por perfil forçado
            1,          // keyFrameIntervalSec = 1s (recupera de perda de pacote sem esperar 2s de imagem quebrada)
            2000,       // forceKeyFrameIntervalMs = 2s (garante keyframe mesmo se o encoder ignorar o intervalo)
            BaseBitrateAdjuster(),
            sharedContext
        )
    }

    private fun logCapabilities(info: MediaCodecInfo) {
        try {
            val caps = info.getCapabilitiesForType(H264_MIME)
            val video = caps.videoCapabilities
            LuceLog.i(TAG, "Encoder H.264 selecionado: ${info.name} (software=${isSoftware(info)})")
            LuceLog.i(TAG, "Formatos de cor suportados: ${caps.colorFormats.joinToString(",") { "0x" + Integer.toHexString(it) }}")
            LuceLog.i(
                TAG,
                "Resolução suportada: largura ${video.supportedWidths} / altura ${video.supportedHeights} " +
                    "(alinhamento ${video.widthAlignment}x${video.heightAlignment})"
            )
        } catch (e: Exception) {
            LuceLog.w(TAG, "Falha ao ler capabilities de ${info.name}: ${e.message}")
        }
    }
}
