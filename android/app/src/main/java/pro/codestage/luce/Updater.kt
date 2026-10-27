package pro.codestage.luce

import android.content.Context
import android.content.Intent
import android.net.Uri
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.BufferedReader
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
    private const val LATEST_RELEASE_URL = "https://api.github.com/repos/Codestage-pro/project-luce/releases/latest"

    suspend fun checkUpdate(currentVersionName: String): UpdateInfo? = withContext(Dispatchers.IO) {
        try {
            val url = URL(LATEST_RELEASE_URL)
            val conn = url.openConnection() as HttpURLConnection
            conn.requestMethod = "GET"
            conn.connectTimeout = 6000
            conn.readTimeout = 6000
            conn.setRequestProperty("User-Agent", LuceApi.USER_AGENT)
            conn.setRequestProperty("Accept", "application/vnd.github.v3+json")

            if (conn.responseCode != 200) {
                return@withContext null
            }

            val body = BufferedReader(InputStreamReader(conn.inputStream, StandardCharsets.UTF_8)).use { it.readText() }
            val json = JSONObject(body)
            val tagName = json.optString("tag_name", "").trim()
            val releaseNotes = json.optString("body", "")

            val assets = json.optJSONArray("assets") ?: return@withContext null
            var apkUrl: String? = null
            for (i in 0 until assets.length()) {
                val asset = assets.getJSONObject(i)
                val name = asset.optString("name", "")
                if (name.equals("Luce-Android.apk", ignoreCase = true) || name.endsWith(".apk", ignoreCase = true)) {
                    apkUrl = asset.optString("browser_download_url", "")
                    break
                }
            }

            if (apkUrl.isNullOrBlank()) {
                return@withContext null
            }

            if (isNewer(tagName, currentVersionName)) {
                return@withContext UpdateInfo(
                    tagName = tagName,
                    downloadUrl = apkUrl,
                    releaseNotes = releaseNotes
                )
            }
            null
        } catch (_: Exception) {
            null
        }
    }

    private fun isNewer(latestTag: String, currentVersion: String): Boolean {
        val latestNumbers = extractNumbers(latestTag)
        val currentNumbers = extractNumbers(currentVersion)
        val maxLen = maxOf(latestNumbers.size, currentNumbers.size)
        for (i in 0 until maxLen) {
            val l = latestNumbers.getOrElse(i) { 0 }
            val c = currentNumbers.getOrElse(i) { 0 }
            if (l > c) return true
            if (l < c) return false
        }
        return false
    }

    private fun extractNumbers(ver: String): List<Int> {
        val clean = ver.trim().removePrefix("v").split("-")[0]
        return "\\d+".toRegex().findAll(clean).map { it.value.toInt() }.toList()
    }

    fun openDownloadPage(context: Context, url: String) {
        val intent = Intent(Intent.ACTION_VIEW, Uri.parse(url)).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK
        }
        context.startActivity(intent)
    }
}
