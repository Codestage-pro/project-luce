package pro.codestage.luce

import android.content.Context
import android.content.SharedPreferences
import android.os.Build
import java.text.Normalizer

data class UserSettings(
    val serverUrl: String = "https://luce.codestage.pro",
    val authPassword: String = "",
    val userName: String = "",
    val resolutionWidth: Int = 1280,
    val resolutionHeight: Int = 720,
    val fps: Int = 30,
    val bitrateKbps: Int = 4000,
    val audioSource: String = AUDIO_SOURCE_SYSTEM
) {
    companion object {
        const val AUDIO_SOURCE_SYSTEM = "SYSTEM"
        const val AUDIO_SOURCE_MIC = "MIC"
        const val AUDIO_SOURCE_MUTED = "MUTED"
    }

    val authToken: String?
        get() = if (authPassword.isNotBlank()) "bdk:$authPassword" else null
}

object LuceConfig {
    private const val PREFS_NAME = "luce_preferences"
    private const val KEY_SERVER_URL = "server_url"
    private const val KEY_AUTH_PASSWORD = "auth_password"
    private const val KEY_USER_NAME = "user_name"
    private const val KEY_RES_WIDTH = "res_width"
    private const val KEY_RES_HEIGHT = "res_height"
    private const val KEY_FPS = "fps"
    private const val KEY_BITRATE_KBPS = "bitrate_kbps"
    private const val KEY_AUDIO_SOURCE = "audio_source"

    fun cleanName(raw: String): String {
        val ascii = Normalizer.normalize(raw.trim(), Normalizer.Form.NFKD)
            .replace("\\p{M}+".toRegex(), "")
        val underscored = ascii.replace("\\s+".toRegex(), "_")
        val clean = underscored.replace("[^A-Za-z0-9._-]".toRegex(), "")
            .trim('.', '_', '-')
        return clean.take(64)
    }

    fun getOrGenerateUserName(context: Context): String {
        val prefs = getPrefs(context)
        val saved = prefs.getString(KEY_USER_NAME, "")?.trim() ?: ""
        val cleaned = cleanName(saved)
        if (cleaned.isNotBlank()) {
            return cleaned
        }
        val model = Build.MODEL.replace("[^A-Za-z0-9]".toRegex(), "")
        val generated = cleanName("android_${if (model.isNotBlank()) model else "device"}")
        return if (generated.isNotBlank()) generated else "android_user"
    }

    fun load(context: Context): UserSettings {
        val prefs = getPrefs(context)
        return UserSettings(
            serverUrl = prefs.getString(KEY_SERVER_URL, "https://luce.codestage.pro")?.trim() ?: "https://luce.codestage.pro",
            authPassword = prefs.getString(KEY_AUTH_PASSWORD, "")?.trim() ?: "",
            userName = cleanName(prefs.getString(KEY_USER_NAME, "") ?: ""),
            resolutionWidth = prefs.getInt(KEY_RES_WIDTH, 1280),
            resolutionHeight = prefs.getInt(KEY_RES_HEIGHT, 720),
            fps = prefs.getInt(KEY_FPS, 30),
            bitrateKbps = prefs.getInt(KEY_BITRATE_KBPS, 4000),
            audioSource = prefs.getString(KEY_AUDIO_SOURCE, UserSettings.AUDIO_SOURCE_SYSTEM) ?: UserSettings.AUDIO_SOURCE_SYSTEM
        )
    }

    fun save(context: Context, settings: UserSettings) {
        val prefs = getPrefs(context)
        val cleanUrl = settings.serverUrl.trim().trimEnd('/')
        val cleanUser = cleanName(settings.userName)
        prefs.edit()
            .putString(KEY_SERVER_URL, cleanUrl)
            .putString(KEY_AUTH_PASSWORD, settings.authPassword.trim())
            .putString(KEY_USER_NAME, cleanUser)
            .putInt(KEY_RES_WIDTH, settings.resolutionWidth)
            .putInt(KEY_RES_HEIGHT, settings.resolutionHeight)
            .putInt(KEY_FPS, settings.fps)
            .putInt(KEY_BITRATE_KBPS, settings.bitrateKbps)
            .putString(KEY_AUDIO_SOURCE, settings.audioSource)
            .apply()
    }

    fun saveUserName(context: Context, name: String): String {
        val cleaned = cleanName(name)
        if (cleaned.isNotBlank()) {
            getPrefs(context).edit().putString(KEY_USER_NAME, cleaned).apply()
        }
        return cleaned
    }

    private fun getPrefs(context: Context): SharedPreferences {
        return context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    }
}
