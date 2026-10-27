package pro.codestage.luce

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.core.content.FileProvider
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.File
import java.io.FileOutputStream
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URL
import java.nio.charset.StandardCharsets

data class UpdateInfo(
    val tagName: String,
    val downloadUrl: String,
    val releaseNotes: String
)

object Updater {
    private const val LATEST_RELEASE_URL =
        "https://api.github.com/repos/MuriloX06/project-luce/releases/latest"
    private const val ANDROID_TEST_RELEASE_URL =
        "https://api.github.com/repos/MuriloX06/project-luce/releases/tags/android-test"

    private fun extractVersion(tagName: String, body: String): String {
        val regex = Regex("""Vers[ãa]o:\s*([^\s\r\n]+)""", RegexOption.IGNORE_CASE)
        val match = regex.find(body)
        if (match != null) {
            return match.groupValues[1].trim()
        }
        return tagName.trim().removePrefix("v")
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
        val url = URL(urlStr)
        val conn = url.openConnection() as HttpURLConnection
        conn.requestMethod = "GET"
        conn.connectTimeout = 6000
        conn.readTimeout = 6000
        conn.setRequestProperty("User-Agent", LuceApi.USER_AGENT)
        conn.setRequestProperty("Accept", "application/vnd.github.v3+json")

        if (conn.responseCode != 200) {
            return null
        }
        val body = BufferedReader(InputStreamReader(conn.inputStream, StandardCharsets.UTF_8)).use { it.readText() }
        return JSONObject(body)
    }

    suspend fun checkUpdate(currentVersionName: String): UpdateInfo? = withContext(Dispatchers.IO) {
        try {
            // 1. Tenta releases/latest
            val latestJson = fetchReleaseJson(LATEST_RELEASE_URL)
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

            // 2. Se latest for igual, mais antiga ou 404, checa releases/tags/android-test
            val testJson = fetchReleaseJson(ANDROID_TEST_RELEASE_URL)
            if (testJson != null) {
                val tagName = testJson.optString("tag_name", "").trim()
                val body = testJson.optString("body", "")
                val ver = extractVersion(tagName, body)
                val apkUrl = findApkUrl(testJson.optJSONArray("assets"))

                if (apkUrl != null && ver.isNotBlank() && ver != currentVersionName) {
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
    ) = withContext(Dispatchers.IO) {
        try {
            val updateDir = File(context.cacheDir, "updates")
            if (!updateDir.exists()) updateDir.mkdirs()
            val apkFile = File(updateDir, "update.apk")
            if (apkFile.exists()) apkFile.delete()

            // Segue redirects (GitHub redireciona assets para AWS S3)
            var currentUrl = downloadUrl
            var conn: HttpURLConnection
            var redirects = 0
            while (true) {
                conn = URL(currentUrl).openConnection() as HttpURLConnection
                conn.instanceFollowRedirects = true
                conn.connectTimeout = 15000
                conn.readTimeout = 30000
                conn.setRequestProperty("User-Agent", LuceApi.USER_AGENT)
                val code = conn.responseCode
                if (code in 300..399) {
                    val loc = conn.getHeaderField("Location") ?: break
                    currentUrl = loc
                    redirects++
                    if (redirects > 5) break
                    continue
                }
                break
            }

            if (conn.responseCode !in 200..299) {
                withContext(Dispatchers.Main) {
                    onError("Erro HTTP ${conn.responseCode} ao baixar atualização")
                }
                return@withContext
            }

            val totalBytes = conn.contentLengthLong
            val buffer = ByteArray(64 * 1024)
            var downloadedBytes = 0L

            conn.inputStream.use { input ->
                FileOutputStream(apkFile).use { output ->
                    var bytesRead: Int
                    while (input.read(buffer).also { bytesRead = it } != -1) {
                        output.write(buffer, 0, bytesRead)
                        downloadedBytes += bytesRead
                        val progress = if (totalBytes > 0) {
                            (downloadedBytes.toFloat() / totalBytes.toFloat()).coerceIn(0f, 1f)
                        } else {
                            -1f
                        }
                        withContext(Dispatchers.Main) {
                            onProgress(progress)
                        }
                    }
                    output.flush()
                }
            }

            if (apkFile.length() == 0L) {
                withContext(Dispatchers.Main) {
                    onError("Arquivo baixado vazio")
                }
                return@withContext
            }

            withContext(Dispatchers.Main) {
                try {
                    val apkUri = FileProvider.getUriForFile(
                        context,
                        "${context.packageName}.fileprovider",
                        apkFile
                    )

                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                        if (!context.packageManager.canRequestPackageInstalls()) {
                            val manageIntent = Intent(
                                Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                                Uri.parse("package:${context.packageName}")
                            ).apply {
                                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                            }
                            context.startActivity(manageIntent)
                        }
                    }

                    val installIntent = Intent(Intent.ACTION_VIEW).apply {
                        setDataAndType(apkUri, "application/vnd.android.package-archive")
                        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    }
                    context.startActivity(installIntent)
                } catch (e: Exception) {
                    onError("Falha ao abrir instalador: ${e.message}")
                }
            }
        } catch (e: Exception) {
            withContext(Dispatchers.Main) {
                onError("Erro durante download: ${e.message}")
            }
        }
    }
}
