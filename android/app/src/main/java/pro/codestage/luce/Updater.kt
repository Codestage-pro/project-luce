package pro.codestage.luce

import android.app.DownloadManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Environment
import android.widget.Toast
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.nio.charset.StandardCharsets

data class UpdateInfo(
    val tagName: String,
    val downloadUrl: String,
    val releaseNotes: String
)

object Updater {
    private const val ANDROID_LATEST_URL =
        "https://api.github.com/repos/Codestage-pro/project-luce/releases/tags/android-latest"
    private const val ANDROID_TEST_URL =
        "https://api.github.com/repos/Codestage-pro/project-luce/releases/tags/android-test"

    private const val FALLBACK_ANDROID_LATEST_URL =
        "https://api.github.com/repos/MuriloX06/project-luce/releases/tags/android-latest"
    private const val FALLBACK_ANDROID_TEST_URL =
        "https://api.github.com/repos/MuriloX06/project-luce/releases/tags/android-test"

    private fun extractVersion(tagName: String, body: String): String {
        val regex = Regex("""Vers[ãa]o:\s*([^\s\r\n]+)""", RegexOption.IGNORE_CASE)
        val match = regex.find(body)
        if (match != null) {
            return match.groupValues[1].trim()
        }
        return tagName.trim().removePrefix("android-v").removePrefix("v")
    }

    private fun findApkUrl(assets: JSONArray?): String? {
        if (assets == null) return null
        for (i in 0 until assets.length()) {
            val asset = assets.getJSONObject(i)
            val name = asset.optString("name", "")
            if (name.equals("Luce-Android.apk", ignoreCase = true) || name.endsWith(".apk", ignoreCase = true)) {
                val url = asset.optString("browser_download_url", "")
                if (url.isNotBlank()) return url
            }
        }
        return null
    }

    private fun fetchReleaseJson(urlStr: String): JSONObject? {
        return try {
            val url = URL(urlStr)
            val conn = url.openConnection() as HttpURLConnection
            conn.instanceFollowRedirects = true
            conn.connectTimeout = 8000
            conn.readTimeout = 8000
            conn.setRequestProperty("User-Agent", LuceApi.USER_AGENT)
            conn.setRequestProperty("Accept", "application/vnd.github+json")
            if (conn.responseCode !in 200..299) return null
            val text = conn.inputStream.bufferedReader(StandardCharsets.UTF_8).use { it.readText() }
            JSONObject(text)
        } catch (_: Exception) {
            null
        }
    }

    suspend fun checkUpdate(currentVersionName: String): UpdateInfo? = checkForUpdate(currentVersionName)

    suspend fun checkForUpdate(currentVersionName: String): UpdateInfo? = withContext(Dispatchers.IO) {
        try {
            // 1. Tenta checar a tag android-latest (Codestage-pro com fallback MuriloX06)
            var latestJson = fetchReleaseJson(ANDROID_LATEST_URL)
            if (latestJson == null) {
                latestJson = fetchReleaseJson(FALLBACK_ANDROID_LATEST_URL)
            }
            if (latestJson != null) {
                val tagName = latestJson.optString("tag_name", "").trim()
                val body = latestJson.optString("body", "")
                val ver = extractVersion(tagName, body)
                val apkUrl = findApkUrl(latestJson.optJSONArray("assets"))

                if (apkUrl != null && isNewerVersion(ver, currentVersionName)) {
                    return@withContext UpdateInfo(
                        tagName = ver,
                        downloadUrl = apkUrl,
                        releaseNotes = body
                    )
                }
            }

            // 2. Se android-latest não tiver versão mais nova, checa releases/tags/android-test
            var testJson = fetchReleaseJson(ANDROID_TEST_URL)
            if (testJson == null) {
                testJson = fetchReleaseJson(FALLBACK_ANDROID_TEST_URL)
            }
            if (testJson != null) {
                val tagName = testJson.optString("tag_name", "").trim()
                val body = testJson.optString("body", "")
                val ver = extractVersion(tagName, body)
                val apkUrl = findApkUrl(testJson.optJSONArray("assets"))

                if (apkUrl != null && ver.isNotBlank() && isNewerVersion(ver, currentVersionName)) {
                    return@withContext UpdateInfo(
                        tagName = ver,
                        downloadUrl = apkUrl,
                        releaseNotes = body
                    )
                }
            }
        } catch (_: Exception) {}

        null
    }

    fun isNewerVersion(remoteVer: String, currentVer: String): Boolean {
        if (remoteVer == currentVer) return false
        val rParts = remoteVer.split("-")[0].split(".").mapNotNull { it.toIntOrNull() }
        val cParts = currentVer.split("-")[0].split(".").mapNotNull { it.toIntOrNull() }
        val maxLen = maxOf(rParts.size, cParts.size)
        for (i in 0 until maxLen) {
            val r = rParts.getOrElse(i) { 0 }
            val c = cParts.getOrElse(i) { 0 }
            if (r > c) return true
            if (r < c) return false
        }
        // Versões base iguais, verifica probe/tag
        if (remoteVer.contains("probe") && currentVer.contains("probe")) {
            val rProbe = remoteVer.substringAfterLast(".").toIntOrNull() ?: 0
            val cProbe = currentVer.substringAfterLast(".").toIntOrNull() ?: 0
            return rProbe > cProbe
        }
        return remoteVer != currentVer
    }

    suspend fun downloadAndInstall(
        context: Context,
        downloadUrl: String,
        onProgress: (Float) -> Unit,
        onError: (String) -> Unit
    ) = withContext(Dispatchers.Main) {
        try {
            if (!downloadUrl.startsWith("https://", ignoreCase = true)) {
                onError("URL de download rejeitada: protocolo deve ser HTTPS")
                return@withContext
            }

            val dm = context.getSystemService(Context.DOWNLOAD_SERVICE) as? DownloadManager
            if (dm != null) {
                try {
                    val request = DownloadManager.Request(Uri.parse(downloadUrl)).apply {
                        setTitle("Project LUCE")
                        setDescription("Baixando atualização Luce-Android.apk...")
                        setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                        setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, "Luce-Android.apk")
                        setMimeType("application/vnd.android.package-archive")
                    }
                    dm.enqueue(request)
                    Toast.makeText(context, "Download iniciado nas notificações!", Toast.LENGTH_LONG).show()
                    onProgress(-1f)
                    return@withContext
                } catch (_: Exception) {}
            }

            val browserIntent = Intent(Intent.ACTION_VIEW, Uri.parse(downloadUrl)).apply {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            context.startActivity(browserIntent)
            Toast.makeText(context, "Abrindo download no navegador...", Toast.LENGTH_LONG).show()
            onProgress(-1f)
        } catch (e: Exception) {
            onError("Falha ao iniciar download: ${e.message}")
        }
    }
}
