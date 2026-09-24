package com.argus.edge.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

/** Same palette as mock_frontend/src/core/theme.js, so the phone and the command view read as one product. */
object Argus {
    val Void = Color(0xFF05070B)
    val Surface = Color(0xFF0B1017)
    val SurfaceHigh = Color(0xFF121A26)
    val SurfaceTop = Color(0xFF1B2634)
    val Outline = Color(0xFF1F2B3A)
    val Accent = Color(0xFF38BDF8)
    val AccentDeep = Color(0xFF0284C7)
    val Text = Color(0xFFE2E8F0)
    val TextDim = Color(0xFF8B9BB0)
    val TextFaint = Color(0xFF5B6B80)
    val Good = Color(0xFF34D399)
    val Warn = Color(0xFFFBBF24)
    val Bad = Color(0xFFEF4444)
    val Pothole = Color(0xFFFBBF24)
    val Crack = Color(0xFFF472B6)
}

private val scheme = darkColorScheme(
    primary = Argus.Accent,
    onPrimary = Argus.Void,
    primaryContainer = Argus.AccentDeep,
    onPrimaryContainer = Argus.Text,
    secondary = Argus.Good,
    background = Argus.Void,
    onBackground = Argus.Text,
    surface = Argus.Surface,
    onSurface = Argus.Text,
    surfaceVariant = Argus.SurfaceHigh,
    onSurfaceVariant = Argus.TextDim,
    surfaceContainer = Argus.Surface,
    surfaceContainerHigh = Argus.SurfaceHigh,
    surfaceContainerHighest = Argus.SurfaceTop,
    outline = Argus.Outline,
    outlineVariant = Argus.Outline,
    error = Argus.Bad,
)

val Mono = FontFamily.Monospace

private val typography = Typography(
    displaySmall = TextStyle(fontWeight = FontWeight.Black, fontSize = 40.sp, letterSpacing = 8.sp),
    headlineSmall = TextStyle(fontWeight = FontWeight.Bold, fontSize = 22.sp, letterSpacing = 0.2.sp),
    titleLarge = TextStyle(fontWeight = FontWeight.SemiBold, fontSize = 20.sp),
    titleMedium = TextStyle(fontWeight = FontWeight.SemiBold, fontSize = 16.sp, letterSpacing = 0.1.sp),
    titleSmall = TextStyle(fontWeight = FontWeight.SemiBold, fontSize = 14.sp),
    bodyLarge = TextStyle(fontSize = 15.sp, lineHeight = 22.sp),
    bodyMedium = TextStyle(fontSize = 14.sp, lineHeight = 20.sp),
    bodySmall = TextStyle(fontSize = 12.sp, lineHeight = 17.sp),
    labelLarge = TextStyle(fontWeight = FontWeight.SemiBold, fontSize = 14.sp, letterSpacing = 0.4.sp),
    labelMedium = TextStyle(fontWeight = FontWeight.Medium, fontSize = 12.sp, letterSpacing = 0.5.sp),
    labelSmall = TextStyle(fontWeight = FontWeight.SemiBold, fontSize = 10.sp, letterSpacing = 1.2.sp),
)

@Composable
fun ArgusTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = scheme, typography = typography, content = content)
}
