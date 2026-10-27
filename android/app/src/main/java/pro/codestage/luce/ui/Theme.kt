package pro.codestage.luce.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

val LuceNight = Color(0xFF0A0C1F)
val LuceCard = Color(0xFF12153A)
val LuceCardElevated = Color(0xFF181C4A)
val LuceBorder = Color(0xFF232859)
val LuceCyan = Color(0xFF00E5FF)
val LucePurple = Color(0xFF7C4DFF)
val LuceMagenta = Color(0xFFFF2D95)
val LuceText = Color(0xFFE8EBFF)
val LuceMuted = Color(0xFF8D93C7)
val LuceSuccess = Color(0xFF00E676)
val LuceError = Color(0xFFFF5252)

private val DarkColorScheme = darkColorScheme(
    primary = LuceCyan,
    onPrimary = LuceNight,
    primaryContainer = LucePurple,
    onPrimaryContainer = LuceText,
    secondary = LuceMagenta,
    onSecondary = Color.White,
    background = LuceNight,
    onBackground = LuceText,
    surface = LuceCard,
    onSurface = LuceText,
    surfaceVariant = LuceCardElevated,
    onSurfaceVariant = LuceMuted,
    outline = LuceBorder,
)

@Composable
fun LuceTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = DarkColorScheme,
        content = content
    )
}
