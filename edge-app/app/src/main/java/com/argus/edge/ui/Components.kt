package com.argus.edge.ui

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.argus.edge.ui.theme.Argus
import com.argus.edge.ui.theme.Mono

/** The ARGUS mark: an eye with a slowly turning scan ring. */
@Composable
fun ArgusEye(size: Dp, modifier: Modifier = Modifier, animate: Boolean = true) {
    val t = rememberInfiniteTransition(label = "eye")
    val angle by t.animateFloat(0f, 360f, infiniteRepeatable(tween(9000, easing = LinearEasing)), label = "ring")
    val pulse by t.animateFloat(0.85f, 1f, infiniteRepeatable(tween(1600), RepeatMode.Reverse), label = "pulse")
    Canvas(modifier.size(size)) {
        val w = this.size.width
        val c = Offset(w / 2, w / 2)
        val stroke = w * 0.045f
        rotate(if (animate) angle else 0f, c) {
            drawArc(
                brush = Brush.sweepGradient(listOf(Color.Transparent, Argus.Accent.copy(alpha = 0.7f), Color.Transparent), c),
                startAngle = 0f, sweepAngle = 300f, useCenter = false,
                topLeft = Offset(w * 0.04f, w * 0.04f), size = androidx.compose.ui.geometry.Size(w * 0.92f, w * 0.92f),
                style = Stroke(stroke * 0.6f),
            )
        }
        val eye = Path().apply {
            moveTo(w * 0.16f, w / 2)
            quadraticBezierTo(w / 2, w * 0.18f, w * 0.84f, w / 2)
            quadraticBezierTo(w / 2, w * 0.82f, w * 0.16f, w / 2)
            close()
        }
        drawPath(eye, Argus.Accent, style = Stroke(stroke))
        drawCircle(Argus.Accent.copy(alpha = 0.25f), radius = w * 0.17f * (if (animate) pulse else 1f), center = c)
        drawCircle(Argus.Accent, radius = w * 0.12f, center = c)
        drawCircle(Argus.Void, radius = w * 0.045f, center = c)
    }
}

@Composable
fun PulseDot(color: Color, modifier: Modifier = Modifier, pulsing: Boolean = true) {
    val t = rememberInfiniteTransition(label = "dot")
    val a by t.animateFloat(0.35f, 1f, infiniteRepeatable(tween(900), RepeatMode.Reverse), label = "a")
    Box(modifier.size(8.dp).alpha(if (pulsing) a else 1f).clip(CircleShape).background(color))
}

@Composable
fun StatusPill(text: String, color: Color, modifier: Modifier = Modifier, pulsing: Boolean = true) {
    Row(
        modifier
            .clip(RoundedCornerShape(50))
            .background(color.copy(alpha = 0.14f))
            .border(1.dp, color.copy(alpha = 0.35f), RoundedCornerShape(50))
            .padding(horizontal = 12.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        PulseDot(color, pulsing = pulsing)
        Spacer(Modifier.width(8.dp))
        Text(text, style = MaterialTheme.typography.labelMedium, color = color)
    }
}

@Composable
fun Panel(
    modifier: Modifier = Modifier,
    highlight: Color? = null,
    content: @Composable ColumnScope.() -> Unit,
) {
    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(22.dp),
        color = Argus.Surface,
        border = BorderStroke(1.dp, highlight?.copy(alpha = 0.55f) ?: Argus.Outline),
    ) {
        Column(Modifier.padding(18.dp), content = content)
    }
}

@Composable
fun SectionHeader(title: String, modifier: Modifier = Modifier, trailing: @Composable RowScope.() -> Unit = {}) {
    Row(modifier.fillMaxWidth().padding(top = 8.dp, bottom = 10.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(title.uppercase(), style = MaterialTheme.typography.labelSmall, color = Argus.TextDim, modifier = Modifier.weight(1f))
        trailing()
    }
}

@Composable
fun StatTile(label: String, value: String, icon: ImageVector, tint: Color, modifier: Modifier = Modifier, sub: String? = null) {
    Surface(
        modifier = modifier,
        shape = RoundedCornerShape(18.dp),
        color = Argus.Surface,
        border = BorderStroke(1.dp, Argus.Outline),
    ) {
        Column(Modifier.padding(14.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(28.dp).clip(RoundedCornerShape(9.dp)).background(tint.copy(alpha = 0.14f)), contentAlignment = Alignment.Center) {
                    Icon(icon, null, tint = tint, modifier = Modifier.size(16.dp))
                }
                Spacer(Modifier.width(10.dp))
                Text(label, style = MaterialTheme.typography.labelMedium, color = Argus.TextDim)
            }
            Spacer(Modifier.padding(top = 10.dp))
            Text(value, style = MaterialTheme.typography.headlineSmall.copy(fontFamily = Mono, fontWeight = FontWeight.Bold), color = Argus.Text)
            if (sub != null) Text(sub, style = MaterialTheme.typography.bodySmall, color = Argus.TextFaint)
        }
    }
}

@Composable
fun Metric(label: String, value: String, modifier: Modifier = Modifier, color: Color = Argus.Text) {
    Column(modifier, horizontalAlignment = Alignment.Start) {
        Text(label.uppercase(), style = MaterialTheme.typography.labelSmall, color = Argus.TextFaint)
        Text(value, style = MaterialTheme.typography.titleMedium.copy(fontFamily = Mono), color = color)
    }
}

@Composable
fun KeyValue(key: String, value: String, mono: Boolean = true) {
    Row(Modifier.fillMaxWidth().padding(vertical = 5.dp), horizontalArrangement = Arrangement.SpaceBetween) {
        Text(key, style = MaterialTheme.typography.bodyMedium, color = Argus.TextDim)
        Spacer(Modifier.width(12.dp))
        Text(value, style = MaterialTheme.typography.bodyMedium.copy(fontFamily = if (mono) Mono else null), color = Argus.Text)
    }
}

/** Soft radial glow for screen backgrounds. */
fun Modifier.argusBackdrop(glow: Color = Argus.Accent): Modifier = this
    .background(Argus.Void)
    .background(Brush.radialGradient(listOf(glow.copy(alpha = 0.16f), Color.Transparent), center = Offset(540f, -120f), radius = 1300f))
