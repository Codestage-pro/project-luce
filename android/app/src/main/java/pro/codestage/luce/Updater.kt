package pro.codestage.luce

import android.content.Context
import android.content.Intent
import android.content.pm.PackageInfo
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.widget.Toast
import androidx.core.content.FileProvider
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.nio.charset.StandardCharsets

data class UpdateInfo(
    val tagName: String,
    val downloadUrl: String,
    val releaseNotes: String
)

object Updater {
    private const val OFFICIAL_LATEST_URL =
        "https://api.github.com/repos/Codestage-pro/project-luce/releases/latest"
    private const val OFFICIAL_ANDROID_TEST_URL =
        "https://api.github.com/repos/Codestage-pro/project-luce/releases/tags/android-test"

    private const val FALLBACK_LATEST_URL =
        "https://api.github.com/repos/MuriloX06/project-luce/releases/latest"
    private const val FALLBACK_ANDROID_TEST_URL =
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
            // 1. Tenta checar a última Release oficial (Codestage-pro com fallback MuriloX06)
            var latestJson = fetchReleaseJson(OFFICIAL_LATEST_URL)
            if (latestJson == null) {
                latestJson = fetchReleaseJson(FALLBACK_LATEST_URL)
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

            // 2. Se latest for igual, mais antiga ou 404, checa releases/tags/android-test
            var testJson = fetchReleaseJson(OFFICIAL_ANDROID_TEST_URL)
            if (testJson == null) {
                testJson = fetchReleaseJson(FALLBACK_ANDROID_TEST_URL)
            }
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

    private fun getSignatures(packageInfo: PackageInfo): List<ByteArray> {
        val signingInfo = packageInfo.signingInfo ?: return emptyList()
        val signatures = if (signingInfo.hasMultipleSigners()) {
            signingInfo.apkContentsSigners
        } else {
            signingInfo.signingCertificateHistory
        }
        return signatures?.map { it.toByteArray() } ?: emptyList()
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
                if (!currentUrl.startsWith("https://", ignoreCase = true)) {
                    withContext(Dispatchers.Main) {
                        onProgress(-1f)
                        onError("URL de download rejeitada: protocolo deve ser HTTPS")
                    }
                    return@withContext
                }
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
                    onProgress(-1f)
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
                    onProgress(-1f)
                    onError("Arquivo baixado vazio")
                }
                return@withContext
            }

            if (totalBytes > 0 && apkFile.length() != totalBytes) {
                withContext(Dispatchers.Main) {
                    onProgress(-1f)
                    onError("Download incompleto: esperado $totalBytes bytes, obtido ${apkFile.length()} bytes")
                }
                return@withContext
            }

            // Verifica assinatura ZIP válida (0x50, 0x4B, 0x03, 0x04)
            val isZipValid = try {
                apkFile.inputStream().use { input ->
                    val header = ByteArray(4)
                    val read = input.read(header)
                    read == 4 &&
                        header[0] == 0x50.toByte() &&
                        header[1] == 0x4B.toByte() &&
                        header[2] == 0x03.toByte() &&
                        header[3] == 0x04.toByte()
                }
            } catch (_: Exception) {
                false
            }

            if (!isZipValid) {
                withContext(Dispatchers.Main) {
                    onProgress(-1f)
                    onError("Arquivo baixado corrompido: cabeçalho de pacote inválido")
                }
                return@withContext
            }

            // Valida se o APK é um pacote Android reconhecido
            val archivePkgInfo = try {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                    context.packageManager.getPackageArchiveInfo(
                        apkFile.absolutePath,
                        PackageManager.PackageInfoFlags.of(PackageManager.GET_SIGNING_CERTIFICATES.toLong())
                    )
                } else {
                    @Suppress("DEPRECATION")
                    context.packageManager.getPackageArchiveInfo(
                        apkFile.absolutePath,
                        PackageManager.GET_SIGNING_CERTIFICATES
                    )
                }
            } catch (_: Exception) {
                null
            }

            if (archivePkgInfo == null) {
                withContext(Dispatchers.Main) {
                    onProgress(-1f)
                    onError("O arquivo baixado não é um pacote Android válido ou está corrompido.")
                }
                return@withContext
            }

            // Validação de assinatura com a versão atualmente instalada
            val currentPkgInfo = try {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                    context.packageManager.getPackageInfo(
                        context.packageName,
                        PackageManager.PackageInfoFlags.of(PackageManager.GET_SIGNING_CERTIFICATES.toLong())
                    )
                } else {
                    @Suppress("DEPRECATION")
                    context.packageManager.getPackageInfo(
                        context.packageName,
                        PackageManager.GET_SIGNING_CERTIFICATES
                    )
                }
            } catch (_: Exception) {
                null
            }

            if (currentPkgInfo != null) {
                val currentSigs = getSignatures(currentPkgInfo)
                val newSigs = getSignatures(archivePkgInfo)
                if (currentSigs.isNotEmpty() && newSigs.isNotEmpty()) {
                    val matches = currentSigs.any { cur -> newSigs.any { newSig -> cur.contentEquals(newSig) } }
                    if (!matches) {
                        withContext(Dispatchers.Main) {
                            onProgress(-1f)
                            Toast.makeText(
                                context,
                                "A assinatura desta atualização mudou. Desinstale a versão antiga uma única vez e instale a nova.",
                                Toast.LENGTH_LONG
                            ).show()
                            try {
                                val browserIntent = Intent(Intent.ACTION_VIEW, Uri.parse(downloadUrl)).apply {
                                    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                                }
                                context.startActivity(browserIntent)
                            } catch (_: Exception) {}
                            onError("A assinatura desta atualização mudou. Desinstale a versão antiga uma única vez e instale a nova.")
                        }
                        return@withContext
                    }
                }
            }

            // Verifica permissão de instalar de fontes desconhecidas (Android 8+)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                if (!context.packageManager.canRequestPackageInstalls()) {
                    withContext(Dispatchers.Main) {
                        onProgress(-1f)
                        Toast.makeText(
                            context,
                            "Permita a instalação de fontes desconhecidas para atualizar",
                            Toast.LENGTH_LONG
                        ).show()
                        val manageIntent = Intent(
                            Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                            Uri.parse("package:${context.packageName}")
                        ).apply {
                            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                        }
                        context.startActivity(manageIntent)
                    }
                    // Retorna sem disparar ACTION_VIEW ao mesmo tempo
                    return@withContext
                }
            }

            // Dispara o instalador de pacotes nativo do Android
            withContext(Dispatchers.Main) {
                try {
                    onProgress(-1f)
                    val apkUri = FileProvider.getUriForFile(
                        context,
                        "${context.packageName}.fileprovider",
                        apkFile
                    )
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
                onProgress(-1f)
                onError("Erro durante download: ${e.message}")
            }
        }
    }
}
