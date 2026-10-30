package pro.codestage.luce

import android.content.Context
import android.content.SharedPreferences
import android.os.Build
import java.text.Normalizer

data class UserSettings(
    val serverUrl: String = "https://luce.codestage.pro",
    val authPassword: String = "",
    val userName: String = "",
    val fps: Int = 30,
    val bitrateKbps: Int = 4000,
    val audioBitrateKbps: Int = 128,
    val audioSource: String = AUDIO_SOURCE_SYSTEM,
    val theme: String = THEME_LUCE,
    val discordToken: String = "",
    val discordUser: String = "",
    val discordAvatar: String = ""
) {
    companion object {
        const val AUDIO_SOURCE_SYSTEM = "SYSTEM"
        const val AUDIO_SOURCE_MUTED = "MUTED"

        const val THEME_LUCE = "luce"
        const val THEME_MURILO = "murilo"
        const val THEME_TIRIGAM = "tirigam"
        const val THEME_NINJA_SOM = "ninja_som"
        const val THEME_DARK = "dark"
    }

    val isFullyAuthenticated: Boolean
        get() = discordToken.isNotBlank() && authPassword.isNotBlank()

    val authToken: String?
        get() = if (isFullyAuthenticated) {
            "$discordToken|bdk:$authPassword"
        } else null
}

object LuceConfig {
    private const val PREFS_NAME = "luce_preferences"
    private const val KEY_SERVER_URL = "server_url"
    private const val KEY_AUTH_PASSWORD = "auth_password"
    private const val KEY_USER_NAME = "user_name"
    private const val KEY_FPS = "fps"
    private const val KEY_BITRATE_KBPS = "bitrate_kbps"
    private const val KEY_AUDIO_BITRATE_KBPS = "audio_bitrate_kbps"
    private const val KEY_AUDIO_SOURCE = "audio_source"
    private const val KEY_THEME = "theme"
    private const val KEY_DISCORD_TOKEN = "discord_token"
    private const val KEY_DISCORD_USER = "discord_user"
    private const val KEY_DISCORD_AVATAR = "discord_avatar"

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
        val rawAudio = prefs.getString(KEY_AUDIO_SOURCE, UserSettings.AUDIO_SOURCE_SYSTEM)
        val audio = if (rawAudio == UserSettings.AUDIO_SOURCE_MUTED) UserSettings.AUDIO_SOURCE_MUTED else UserSettings.AUDIO_SOURCE_SYSTEM
        val rawTheme = prefs.getString(KEY_THEME, UserSettings.THEME_LUCE) ?: UserSettings.THEME_LUCE
        val theme = when (rawTheme) {
            UserSettings.THEME_MURILO, UserSettings.THEME_TIRIGAM,
            UserSettings.THEME_NINJA_SOM, UserSettings.THEME_DARK -> rawTheme
            else -> UserSettings.THEME_LUCE
        }
        return UserSettings(
            serverUrl = prefs.getString(KEY_SERVER_URL, "https://luce.codestage.pro")?.trim() ?: "https://luce.codestage.pro",
            authPassword = prefs.getString(KEY_AUTH_PASSWORD, "")?.trim() ?: "",
            userName = cleanName(prefs.getString(KEY_USER_NAME, "") ?: ""),
            fps = prefs.getInt(KEY_FPS, 30),
            bitrateKbps = prefs.getInt(KEY_BITRATE_KBPS, 4000),
            audioBitrateKbps = prefs.getInt(KEY_AUDIO_BITRATE_KBPS, 128),
            audioSource = audio,
            theme = theme,
            discordToken = prefs.getString(KEY_DISCORD_TOKEN, "")?.trim() ?: "",
            discordUser = prefs.getString(KEY_DISCORD_USER, "")?.trim() ?: "",
            discordAvatar = prefs.getString(KEY_DISCORD_AVATAR, "")?.trim() ?: ""
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
            .putInt(KEY_FPS, settings.fps)
            .putInt(KEY_BITRATE_KBPS, settings.bitrateKbps)
            .putInt(KEY_AUDIO_BITRATE_KBPS, settings.audioBitrateKbps)
            .putString(KEY_AUDIO_SOURCE, settings.audioSource)
            .putString(KEY_THEME, settings.theme)
            .putString(KEY_DISCORD_TOKEN, settings.discordToken.trim())
            .putString(KEY_DISCORD_USER, settings.discordUser.trim())
            .putString(KEY_DISCORD_AVATAR, settings.discordAvatar.trim())
            .apply()
    }

    fun saveUserName(context: Context, name: String): String {
        val cleaned = cleanName(name)
        if (cleaned.isNotBlank()) {
            getPrefs(context).edit().putString(KEY_USER_NAME, cleaned).apply()
        }
        return cleaned
    }

    fun saveDiscordAuth(context: Context, token: String, discordUser: String, userName: String, avatarUrl: String = "") {
        val cleanUser = cleanName(userName)
        val editor = getPrefs(context).edit()
            .putString(KEY_DISCORD_TOKEN, token.trim())
            .putString(KEY_DISCORD_USER, discordUser.trim())
            .putString(KEY_DISCORD_AVATAR, avatarUrl.trim())
        if (cleanUser.isNotBlank()) {
            editor.putString(KEY_USER_NAME, cleanUser)
        }
        editor.apply()
    }

    fun clearDiscordAuth(context: Context) {
        getPrefs(context).edit()
            .remove(KEY_DISCORD_TOKEN)
            .remove(KEY_DISCORD_USER)
            .remove(KEY_DISCORD_AVATAR)
            .apply()
    }

    private fun getPrefs(context: Context): SharedPreferences {
        return context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    }
}
