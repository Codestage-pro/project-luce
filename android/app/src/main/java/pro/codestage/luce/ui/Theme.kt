package pro.codestage.luce.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import pro.codestage.luce.UserSettings

data class LucePalette(
    val night: Color,
    val card: Color,
    val cardElevated: Color,
    val border: Color,
    val primary: Color,
    val secondary: Color,
    val onAir: Color,
    val text: Color,
    val muted: Color,
    val success: Color = Color(0xFF00E676),
    val error: Color = Color(0xFFFF5252)
)

val PaletteLuce = LucePalette(
    night = Color(0xFF0A0C1F),
    card = Color(0xFF12153A),
    cardElevated = Color(0xFF181C4A),
    border = Color(0xFF232859),
    primary = Color(0xFF00E5FF),     // Ciano
    secondary = Color(0xFF7C4DFF),   // Violeta
    onAir = Color(0xFFFF2D95),       // Magenta
    text = Color(0xFFE8EBFF),
    muted = Color(0xFF8D93C7)
)

val PaletteMurilo = LucePalette(
    night = Color(0xFF0D0718),
    card = Color(0xFF1A0F2E),
    cardElevated = Color(0xFF251542),
    border = Color(0xFF341D59),
    primary = Color(0xFFA855F7),     // Roxo Neon
    secondary = Color(0xFF6366F1),   // Índigo
    onAir = Color(0xFFEC4899),       // Rosa/Magenta
    text = Color(0xFFF3E8FF),
    muted = Color(0xFFA78BFA)
)

val PaletteTirigam = LucePalette(
    night = Color(0xFF0F0406),
    card = Color(0xFF1C070B),
    cardElevated = Color(0xFF2B0B12),
    border = Color(0xFF45101A),
    primary = Color(0xFFFF1744),     // Vermelho Puro Vibrante
    secondary = Color(0xFFB71C1C),   // Vermelho Sangue Profundo (zero laranja)
    onAir = Color(0xFFFF0033),       // Carmesim Ao Vivo
    text = Color(0xFFFFEEF0),
    muted = Color(0xFFC97A85)
)

val PaletteNinjaSom = LucePalette(
    night = Color(0xFF050D1A),
    card = Color(0xFF0B1930),
    cardElevated = Color(0xFF112547),
    border = Color(0xFF183461),
    primary = Color(0xFF3B82F6),     // Azul Elétrico
    secondary = Color(0xFF06B6D4),   // Ciano Profundo
    onAir = Color(0xFF38BDF8),       // Azul Celeste
    text = Color(0xFFE5F0FF),
    muted = Color(0xFF7DA2D6)
)

val PaletteDark = LucePalette(
    night = Color(0xFF050507),
    card = Color(0xFF101014),
    cardElevated = Color(0xFF18181F),
    border = Color(0xFF26262E),
    primary = Color(0xFFE4E4E7),     // Prata/Branco
    secondary = Color(0xFF52525B),   // Grafite
    onAir = Color(0xFFEF4444),       // Vermelho Ao Vivo
    text = Color(0xFFFAFAFA),
    muted = Color(0xFF8E8E99)
)

val LocalLucePalette = staticCompositionLocalOf { PaletteLuce }

val LuceNight: Color
    @Composable
    @ReadOnlyComposable
    get() = LocalLucePalette.current.night

val LuceCard: Color
    @Composable
    @ReadOnlyComposable
    get() = LocalLucePalette.current.card

val LuceCardElevated: Color
    @Composable
    @ReadOnlyComposable
    get() = LocalLucePalette.current.cardElevated

val LuceBorder: Color
    @Composable
    @ReadOnlyComposable
    get() = LocalLucePalette.current.border

val LuceCyan: Color
    @Composable
    @ReadOnlyComposable
    get() = LocalLucePalette.current.primary

val LucePurple: Color
    @Composable
    @ReadOnlyComposable
    get() = LocalLucePalette.current.secondary

val LuceMagenta: Color
    @Composable
    @ReadOnlyComposable
    get() = LocalLucePalette.current.onAir

val LuceText: Color
    @Composable
    @ReadOnlyComposable
    get() = LocalLucePalette.current.text

val LuceMuted: Color
    @Composable
    @ReadOnlyComposable
    get() = LocalLucePalette.current.muted

val LuceSuccess: Color
    @Composable
    @ReadOnlyComposable
    get() = LocalLucePalette.current.success

val LuceError: Color
    @Composable
    @ReadOnlyComposable
    get() = LocalLucePalette.current.error

@Composable
fun LuceTheme(
    themeKey: String = UserSettings.THEME_LUCE,
    content: @Composable () -> Unit
) {
    val palette = when (themeKey) {
        UserSettings.THEME_MURILO -> PaletteMurilo
        UserSettings.THEME_TIRIGAM -> PaletteTirigam
        UserSettings.THEME_NINJA_SOM -> PaletteNinjaSom
        UserSettings.THEME_DARK -> PaletteDark
        else -> PaletteLuce
    }

    val colorScheme = darkColorScheme(
        primary = palette.primary,
        onPrimary = palette.night,
        primaryContainer = palette.secondary,
        onPrimaryContainer = palette.text,
        secondary = palette.onAir,
        onSecondary = Color.White,
        background = palette.night,
        onBackground = palette.text,
        surface = palette.card,
        onSurface = palette.text,
        surfaceVariant = palette.cardElevated,
        onSurfaceVariant = palette.muted,
        outline = palette.border
    )

    CompositionLocalProvider(LocalLucePalette provides palette) {
        MaterialTheme(
            colorScheme = colorScheme,
            content = content
        )
    }
}
