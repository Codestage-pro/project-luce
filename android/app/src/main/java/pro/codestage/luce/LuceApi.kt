package pro.codestage.luce

import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.nio.charset.StandardCharsets

data class RosterItem(
    val name: String,
    val ready: Boolean,
    val viewers: List<String>,
    val avatar: String = ""
)

data class RosterResponse(
    val items: List<RosterItem>,
    val onlineCount: Int,
    val onlineUsers: List<String>,
    val avatars: Map<String, String> = emptyMap()
)

data class SdpResponse(
    val answerSdp: String,
    val location: String
)

data class DiscordPollResult(
    val status: String,
    val token: String = "",
    val discordUser: String = "",
    val user: String = "",
    val avatar: String = "",
    val message: String = ""
)

object LuceApi {
    const val USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"

    private fun openConnection(urlStr: String, method: String, token: String?, contentType: String? = null): HttpURLConnection {
        val url = URL(urlStr)
        val conn = url.openConnection() as HttpURLConnection
        conn.requestMethod = method
        conn.connectTimeout = 8000
        conn.readTimeout = 8000
        conn.setRequestProperty("User-Agent", USER_AGENT)
        if (!token.isNullOrBlank()) {
            conn.setRequestProperty("Authorization", "Bearer $token")
        }
        if (contentType != null) {
            conn.setRequestProperty("Content-Type", contentType)
        }
        return conn
    }

    private fun readResponse(conn: HttpURLConnection): String {
        val stream = if (conn.responseCode in 200..299) conn.inputStream else conn.errorStream ?: conn.inputStream
        return BufferedReader(InputStreamReader(stream, StandardCharsets.UTF_8)).use { it.readText() }
    }

    fun fetchRosterState(baseUrl: String, token: String?, viewer: String? = null, avatarUrl: String? = null): RosterResponse {
        val conn = openConnection("$baseUrl/v3/paths/list", "GET", token)
        if (!viewer.isNullOrBlank()) {
            conn.setRequestProperty("X-Luce-Viewer", viewer)
        }
        if (!avatarUrl.isNullOrBlank() && avatarUrl.startsWith("https://cdn.discordapp.com/")) {
            conn.setRequestProperty("X-Luce-Avatar", avatarUrl)
        }
        val code = conn.responseCode
        if (code == 401) {
            throw SecurityException("HTTP 401: Não autorizado")
        }
        if (code !in 200..299) {
            val err = readResponse(conn)
            throw RuntimeException("Erro ao consultar transmissões (HTTP $code): $err")
        }
        val body = readResponse(conn)
        val json = JSONObject(body)
        val avatarsJson = json.optJSONObject("avatars")
        val avatarsMap = mutableMapOf<String, String>()
        if (avatarsJson != null) {
            val keys = avatarsJson.keys()
            while (keys.hasNext()) {
                val k = keys.next()
                val v = avatarsJson.optString(k, "")
                if (v.isNotBlank()) {
                    avatarsMap[k] = v
                }
            }
        }
        val itemsArray = json.optJSONArray("items")
        val list = mutableListOf<RosterItem>()
        if (itemsArray != null) {
            for (i in 0 until itemsArray.length()) {
                val item = itemsArray.getJSONObject(i)
                val name = item.optString("name", "")
                val ready = item.optBoolean("ready", false)
                var avatar = item.optString("avatar", "")
                if (avatar.isBlank() && name.isNotBlank() && avatarsMap.containsKey(name)) {
                    avatar = avatarsMap[name] ?: ""
                }
                val viewersJson = item.optJSONArray("viewers")
                val viewers = mutableListOf<String>()
                if (viewersJson != null) {
                    for (j in 0 until viewersJson.length()) {
                        viewers.add(viewersJson.getString(j))
                    }
                }
                if (name.isNotBlank()) {
                    list.add(RosterItem(name = name, ready = ready, viewers = viewers, avatar = avatar))
                }
            }
        }
        val onlineCount = json.optInt("online_count", list.size)
        val usersArray = json.optJSONArray("online_users")
        val onlineUsers = mutableListOf<String>()
        if (usersArray != null) {
            for (i in 0 until usersArray.length()) {
                onlineUsers.add(usersArray.getString(i))
            }
        }
        return RosterResponse(items = list, onlineCount = onlineCount, onlineUsers = onlineUsers, avatars = avatarsMap)
    }

    fun listPaths(baseUrl: String, token: String?, viewer: String? = null): List<RosterItem> {
        return fetchRosterState(baseUrl, token, viewer).items
    }

    fun pollDiscordAuth(baseUrl: String, state: String): DiscordPollResult {
        val sSafe = URLEncoder.encode(state, StandardCharsets.UTF_8.name())
        val conn = openConnection("$baseUrl/auth/discord/poll?state=$sSafe", "GET", null)
        return try {
            val code = conn.responseCode
            if (code != 200) {
                DiscordPollResult(status = "error", message = "HTTP $code")
            } else {
                val raw = readResponse(conn)
                val json = JSONObject(raw)
                DiscordPollResult(
                    status = json.optString("status", "error"),
                    token = json.optString("token", ""),
                    discordUser = json.optString("discord_user", ""),
                    user = json.optString("user", ""),
                    avatar = json.optString("avatar_url", "").ifBlank { json.optString("avatar", "") },
                    message = json.optString("message", "")
                )
            }
        } catch (e: Exception) {
            DiscordPollResult(status = "error", message = e.message ?: "Falha de rede")
        } finally {
            conn.disconnect()
        }
    }

    fun sendWatching(baseUrl: String, stream: String, viewer: String, token: String?, on: Boolean) {
        val sSafe = URLEncoder.encode(stream, StandardCharsets.UTF_8.name())
        val vSafe = URLEncoder.encode(viewer, StandardCharsets.UTF_8.name())
        val url = "$baseUrl/$sSafe/watch?viewer=$vSafe"
        val conn = openConnection(url, if (on) "PATCH" else "DELETE", token)
        try {
            conn.responseCode
        } finally {
            conn.disconnect()
        }
    }

    fun sendWhipOffer(baseUrl: String, stream: String, offerSdp: String, token: String?): SdpResponse {
        val sSafe = URLEncoder.encode(stream, StandardCharsets.UTF_8.name())
        val conn = openConnection("$baseUrl/$sSafe/whip", "POST", token, "application/sdp")
        conn.doOutput = true
        conn.outputStream.use { it.write(offerSdp.toByteArray(StandardCharsets.UTF_8)) }
        val code = conn.responseCode
        if (code == 401) {
            val err = readResponse(conn)
            throw SecurityException("HTTP 401: Não autorizado: $err")
        }
        if (code != 201) {
            val err = readResponse(conn)
            throw RuntimeException("Erro ao iniciar transmissão WHIP (HTTP $code): $err")
        }
        val loc = conn.getHeaderField("Location") ?: "/$sSafe/whip"
        val answer = readResponse(conn)
        return SdpResponse(answerSdp = answer, location = loc)
    }

    fun sendWhipHeartbeat(baseUrl: String, stream: String, token: String?) {
        val sSafe = URLEncoder.encode(stream, StandardCharsets.UTF_8.name())
        val conn = openConnection("$baseUrl/$sSafe/whip", "PATCH", token)
        try {
            val code = conn.responseCode
            if (code == 401) {
                throw SecurityException("HTTP 401: Não autorizado")
            }
        } finally {
            conn.disconnect()
        }
    }

    fun sendWhipDelete(baseUrl: String, location: String, token: String?) {
        val loc = if (location.startsWith("/")) location else "/$location"
        val conn = openConnection("$baseUrl$loc", "DELETE", token)
        try {
            conn.responseCode
        } finally {
            conn.disconnect()
        }
    }

    fun sendWhepOffer(baseUrl: String, stream: String, offerSdp: String, token: String?): SdpResponse {
        val sSafe = URLEncoder.encode(stream, StandardCharsets.UTF_8.name())
        val conn = openConnection("$baseUrl/$sSafe/whep", "POST", token, "application/sdp")
        conn.doOutput = true
        conn.outputStream.use { it.write(offerSdp.toByteArray(StandardCharsets.UTF_8)) }
        val code = conn.responseCode
        if (code == 401) {
            val err = readResponse(conn)
            throw SecurityException("HTTP 401: Não autorizado: $err")
        }
        if (code != 201) {
            val err = readResponse(conn)
            throw RuntimeException("Erro ao assistir stream WHEP (HTTP $code): $err")
        }
        val loc = conn.getHeaderField("Location") ?: "/$sSafe/whep"
        val answer = readResponse(conn)
        return SdpResponse(answerSdp = answer, location = loc)
    }

    fun sendWhepDelete(baseUrl: String, location: String, token: String?) {
        val loc = if (location.startsWith("/")) location else "/$location"
        val conn = openConnection("$baseUrl$loc", "DELETE", token)
        try {
            conn.responseCode
        } finally {
            conn.disconnect()
        }
    }

    fun sanitizeSdpOffer(sdp: String, isPublish: Boolean, audioBitrateKbps: Int = 128): String {
        // 1. Áudio: O SFU Cloudflare exige rtpmap opus/48000/2
        var modified = sdp.replace("(?im)^(a=rtpmap:\\d+) opus/48000\\r?$".toRegex(), "$1 opus/48000/2")

        val opusPt = Regex("(?im)^a=rtpmap:(\\d+)\\s+opus/48000(?:/2)?").find(modified)?.groupValues?.get(1)

        // 2. Extrai e seleciona codecs de vídeo
        val rtpmapRegex = "a=rtpmap:(\\d+)\\s+([A-Za-z0-9_-]+)/90000".toRegex(RegexOption.IGNORE_CASE)
        val fmtpRegex = "a=fmtp:(\\d+)\\s+(.*)".toRegex()

        val rtpMap = mutableMapOf<String, String>() // pt -> codecName
        for (m in rtpmapRegex.findAll(modified)) {
            val pt = m.groupValues[1]
            val codec = m.groupValues[2].uppercase()
            rtpMap[pt] = codec
        }

        val fmtpMap = mutableMapOf<String, String>() // pt -> fmtpParams
        for (m in fmtpRegex.findAll(modified)) {
            val pt = m.groupValues[1]
            val params = m.groupValues[2]
            fmtpMap[pt] = params
        }

        // Escolhe o H264 ideal: preferência por 42e01f ou 42001f (baseline/constrained-baseline)
        val h264Pts = rtpMap.filter { it.value == "H264" }.keys
        var chosenH264Pt: String? = null
        for (pt in h264Pts) {
            val fmtp = fmtpMap[pt] ?: ""
            if (fmtp.contains("42e01f", ignoreCase = true) || fmtp.contains("42001f", ignoreCase = true)) {
                chosenH264Pt = pt
                break
            }
        }
        if (chosenH264Pt == null) {
            chosenH264Pt = h264Pts.firstOrNull()
        }

        // RTX associado ao H264 escolhido (ex: a=fmtp:103 apt=102)
        var chosenRtxPt: String? = null
        if (chosenH264Pt != null) {
            for ((pt, params) in fmtpMap) {
                if (rtpMap[pt] == "RTX" && params.contains("apt=$chosenH264Pt")) {
                    chosenRtxPt = pt
                    break
                }
            }
        }

        // Todos os outros payloads de vídeo a serem descartados
        val dropPts = mutableSetOf<String>()
        for ((pt, codec) in rtpMap) {
            if (codec in setOf("VP8", "VP9", "H265", "AV1", "RED", "ULPFEC", "FLEXFEC-03")) {
                dropPts.add(pt)
            } else if (codec == "H264" && pt != chosenH264Pt) {
                dropPts.add(pt)
            } else if (codec == "RTX" && pt != chosenRtxPt) {
                dropPts.add(pt)
            }
        }

        // Reconstrói as linhas da SDP
        val lines = modified.lines()
        val result = mutableListOf<String>()

        var inVideoSection = false
        for (line in lines) {
            val trimmed = line.trimEnd('\r', '\n')
            if (trimmed.startsWith("m=video")) {
                inVideoSection = true
                val parts = trimmed.split(" ")
                val header = parts.take(3).joinToString(" ") // m=video <port> <proto>
                val keptPayloads = mutableListOf<String>()
                if (isPublish) {
                    if (chosenH264Pt != null) keptPayloads.add(chosenH264Pt)
                    if (chosenRtxPt != null) keptPayloads.add(chosenRtxPt)
                } else {
                    if (chosenRtxPt != null) keptPayloads.add(chosenRtxPt)
                    if (chosenH264Pt != null) keptPayloads.add(chosenH264Pt)
                }
                if (keptPayloads.isEmpty()) {
                    // fallback caso não tenha encontrado H264
                    for (pt in parts.drop(3)) {
                        if (pt !in dropPts) keptPayloads.add(pt)
                    }
                }
                result.add("$header ${keptPayloads.joinToString(" ")}")
                continue
            } else if (trimmed.startsWith("m=")) {
                inVideoSection = false
            }

            if (opusPt != null) {
                if (trimmed.startsWith("a=fmtp:$opusPt")) {
                    var fmtpLine = trimmed.replace(";?maxaveragebitrate=\\d+".toRegex(), "")
                    if (!fmtpLine.contains("stereo=1")) {
                        fmtpLine += ";stereo=1;sprop-stereo=1"
                    }
                    fmtpLine += ";maxaveragebitrate=${audioBitrateKbps * 1000}"
                    result.add(fmtpLine)
                    continue
                } else if (trimmed.startsWith("a=rtpmap:$opusPt") && !modified.contains("a=fmtp:$opusPt")) {
                    result.add(trimmed)
                    result.add("a=fmtp:$opusPt minptime=10;useinbandfec=1;stereo=1;sprop-stereo=1;maxaveragebitrate=${audioBitrateKbps * 1000}")
                    continue
                }
            }

            if (inVideoSection) {
                val ptMatch = Regex("^a=(?:rtpmap|fmtp|rtcp-fb):(\\d+)").find(trimmed)
                if (ptMatch != null) {
                    val pt = ptMatch.groupValues[1]
                    if (pt in dropPts) {
                        continue // descarta atributos de codecs não selecionados
                    }
                    if (chosenH264Pt != null && pt == chosenH264Pt && trimmed.startsWith("a=fmtp:$chosenH264Pt")) {
                        var fmtpLine = trimmed
                        if (!fmtpLine.contains("packetization-mode=1")) {
                            fmtpLine += ";packetization-mode=1"
                        }
                        result.add(fmtpLine)
                        continue
                    }
                }
            }
            result.add(trimmed)
        }

        return result.joinToString("\r\n") + "\r\n"
    }
}
