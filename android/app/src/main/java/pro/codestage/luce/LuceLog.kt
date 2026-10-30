package pro.codestage.luce

import android.content.Context
import android.os.Process
import android.util.Log
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.TimeUnit

/**
 * Log persistente do app (filesDir/logs/luce.log, rotaciona em luce.old.log acima de 1 MB).
 * Nunca registre tokens, senhas ou o segredo do Discord: logue só nomes de stream, códigos HTTP e métricas.
 */
object LuceLog {
    private const val MAX_FILE_BYTES = 1024L * 1024L
    private const val LOGCAT_LINES = 80
    private val LOGCAT_KEYWORDS = listOf(
        "webrtc", "mediacodec", "codec2", "omx", "luce", "virtualdisplay", "mediaprojection",
        "androidruntime", "encoder", "surfacetexture"
    )

    private val lock = Any()
    private val timeFormat = SimpleDateFormat("HH:mm:ss.SSS", Locale.US)
    private var logFile: File? = null
    private var handlerInstalled = false

    fun init(context: Context) {
        synchronized(lock) {
            if (logFile == null) {
                val dir = File(context.applicationContext.filesDir, "logs")
                try {
                    dir.mkdirs()
                    logFile = File(dir, "luce.log")
                } catch (e: Exception) {
                    Log.e("LuceLog", "Falha ao criar diretório de logs", e)
                }
            }
            if (!handlerInstalled) {
                handlerInstalled = true
                val previous = Thread.getDefaultUncaughtExceptionHandler()
                Thread.setDefaultUncaughtExceptionHandler { thread, throwable ->
                    try {
                        e("Crash", "Exceção não tratada na thread ${thread.name}", throwable)
                    } catch (_: Throwable) {
                    }
                    previous?.uncaughtException(thread, throwable)
                }
            }
        }
    }

    fun i(tag: String, msg: String) {
        Log.i(tag, msg)
        write("INFO", tag, msg, null)
    }

    fun w(tag: String, msg: String) {
        Log.w(tag, msg)
        write("WARN", tag, msg, null)
    }

    fun e(tag: String, msg: String, tr: Throwable? = null) {
        Log.e(tag, msg, tr)
        write("ERRO", tag, msg, tr)
    }

    private fun write(level: String, tag: String, msg: String, tr: Throwable?) {
        synchronized(lock) {
            val file = logFile ?: return
            try {
                if (file.exists() && file.length() > MAX_FILE_BYTES) {
                    val old = File(file.parentFile, "luce.old.log")
                    old.delete()
                    file.renameTo(old)
                }
                val sb = StringBuilder()
                sb.append(timeFormat.format(Date())).append(" [").append(level).append("] [")
                    .append(tag).append("] ").append(msg).append('\n')
                if (tr != null) sb.append(Log.getStackTraceString(tr)).append('\n')
                file.appendText(sb.toString())
            } catch (ex: Exception) {
                Log.e("LuceLog", "Falha ao gravar log", ex)
            }
        }
    }

    /** Últimos [maxBytes] do luce.log + últimas linhas relevantes do logcat do próprio processo. */
    fun readLogs(maxBytes: Int = 64 * 1024): String {
        val fileText = synchronized(lock) {
            val file = logFile
            if (file == null || !file.exists()) {
                ""
            } else {
                try {
                    val len = file.length()
                    if (len <= maxBytes) {
                        file.readText()
                    } else {
                        file.inputStream().use { input ->
                            input.skip(len - maxBytes)
                            String(input.readBytes(), Charsets.UTF_8)
                        }
                    }
                } catch (ex: Exception) {
                    "Falha ao ler o log: ${ex.message}"
                }
            }
        }
        val sb = StringBuilder()
        sb.append("=== luce.log ===\n")
        sb.append(if (fileText.isBlank()) "(vazio)\n" else fileText)
        if (!fileText.endsWith("\n") && fileText.isNotBlank()) sb.append('\n')
        sb.append("\n=== logcat (processo do app) ===\n")
        sb.append(readLogcat())
        return sb.toString()
    }

    fun clearLogs() {
        synchronized(lock) {
            val dir = logFile?.parentFile ?: return
            try {
                File(dir, "luce.log").delete()
                File(dir, "luce.old.log").delete()
            } catch (ex: Exception) {
                Log.e("LuceLog", "Falha ao limpar logs", ex)
            }
        }
    }

    private fun readLogcat(): String {
        return try {
            val process = ProcessBuilder(
                "logcat", "-d", "-v", "time", "--pid=${Process.myPid()}", "*:I"
            ).redirectErrorStream(true).start()
            val lines = process.inputStream.bufferedReader().use { it.readLines() }
            if (!process.waitFor(2, TimeUnit.SECONDS)) process.destroy()
            val relevant = lines.filter { line ->
                val lower = line.lowercase(Locale.ROOT)
                line.startsWith("E/") || line.startsWith("W/") || LOGCAT_KEYWORDS.any { lower.contains(it) }
            }
            relevant.takeLast(LOGCAT_LINES).joinToString("\n").ifBlank { "(sem linhas relevantes)" } + "\n"
        } catch (ex: Exception) {
            "logcat indisponível: ${ex.message}\n"
        }
    }
}
