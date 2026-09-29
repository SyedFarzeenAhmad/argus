package com.argus.edge.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.slideInVertically
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.ArrowForward
import androidx.compose.material.icons.rounded.Hub
import androidx.compose.material.icons.rounded.Videocam
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.argus.edge.BuildConfig
import com.argus.edge.core.Role
import com.argus.edge.ui.theme.Argus
import com.argus.edge.ui.theme.Mono
import kotlinx.coroutines.delay

@Composable
fun RoleScreen(deviceId: String, onChoose: (Role) -> Unit) {
    var shown by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { delay(80); shown = true }

    Box(Modifier.fillMaxSize().argusBackdrop()) {
        Column(
            Modifier
                .fillMaxSize()
                .statusBarsPadding()
                .navigationBarsPadding()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 22.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Spacer(Modifier.height(44.dp))
            ArgusEye(96.dp)
            Spacer(Modifier.height(18.dp))
            Text("ARGUS", style = MaterialTheme.typography.displaySmall, color = Argus.Text)
            Text(
                "EDGE UNIT",
                style = MaterialTheme.typography.labelSmall,
                color = Argus.Accent,
            )
            Spacer(Modifier.height(34.dp))
            Text(
                "What is this phone on the bus?",
                style = MaterialTheme.typography.headlineSmall,
                color = Argus.Text,
                textAlign = TextAlign.Center,
            )
            Spacer(Modifier.height(6.dp))
            Text(
                "Every bus runs one processing client and any number of cameras.",
                style = MaterialTheme.typography.bodyMedium,
                color = Argus.TextDim,
                textAlign = TextAlign.Center,
            )
            Spacer(Modifier.height(26.dp))

            AnimatedVisibility(shown, enter = fadeIn(tween(420)) + slideInVertically(tween(420)) { it / 5 }) {
                RoleCard(
                    icon = Icons.Rounded.Videocam,
                    title = "Camera",
                    body = "Mount on the windscreen or rear window. Streams live video to the processing client over the bus Wi-Fi. Stores nothing.",
                    tags = listOf("Front", "Rear", "Any position"),
                    tint = Argus.Accent,
                    onClick = { onChoose(Role.CAMERA) },
                )
            }
            Spacer(Modifier.height(14.dp))
            AnimatedVisibility(shown, enter = fadeIn(tween(420, 120)) + slideInVertically(tween(420, 120)) { it / 5 }) {
                RoleCard(
                    icon = Icons.Rounded.Hub,
                    title = "Processing client",
                    body = "Links 1, 2 or N cameras, detects potholes on-device, keeps the full record locally and prepares what the ARGUS servers need.",
                    tags = listOf("Pothole AI", "GPS", "Server hand-off"),
                    tint = Argus.Good,
                    onClick = { onChoose(Role.PROCESSOR) },
                )
            }

            Spacer(Modifier.height(36.dp))
            Text(
                "$deviceId  ·  v${BuildConfig.VERSION_NAME}",
                style = MaterialTheme.typography.labelMedium.copy(fontFamily = Mono),
                color = Argus.TextFaint,
            )
            Text(
                "You can change this later in settings.",
                style = MaterialTheme.typography.bodySmall,
                color = Argus.TextFaint,
                modifier = Modifier.padding(top = 4.dp, bottom = 20.dp),
            )
        }
    }
}

@Composable
private fun RoleCard(
    icon: ImageVector,
    title: String,
    body: String,
    tags: List<String>,
    tint: Color,
    onClick: () -> Unit,
) {
    var pressed by remember { mutableStateOf(false) }
    val scale by animateFloatAsState(if (pressed) 0.97f else 1f, label = "press")
    Surface(
        onClick = { pressed = true; onClick() },
        modifier = Modifier.fillMaxWidth().scale(scale),
        shape = RoundedCornerShape(26.dp),
        color = Argus.Surface,
        border = BorderStroke(1.dp, Brush.linearGradient(listOf(tint.copy(alpha = 0.6f), Argus.Outline))),
    ) {
        Box(Modifier.background(Brush.linearGradient(listOf(tint.copy(alpha = 0.10f), Color.Transparent)))) {
            Column(Modifier.padding(20.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(
                        Modifier.size(52.dp).clip(RoundedCornerShape(16.dp)).background(tint.copy(alpha = 0.16f)),
                        contentAlignment = Alignment.Center,
                    ) { Icon(icon, null, tint = tint, modifier = Modifier.size(28.dp)) }
                    Spacer(Modifier.width(16.dp))
                    Text(title, style = MaterialTheme.typography.titleLarge, color = Argus.Text, modifier = Modifier.weight(1f))
                    Box(
                        Modifier.size(36.dp).clip(RoundedCornerShape(50)).background(tint.copy(alpha = 0.14f)),
                        contentAlignment = Alignment.Center,
                    ) { Icon(Icons.AutoMirrored.Rounded.ArrowForward, null, tint = tint, modifier = Modifier.size(18.dp)) }
                }
                Spacer(Modifier.height(14.dp))
                Text(body, style = MaterialTheme.typography.bodyMedium, color = Argus.TextDim)
                Spacer(Modifier.height(14.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    tags.forEach { t ->
                        Text(
                            t,
                            style = MaterialTheme.typography.labelMedium,
                            color = tint,
                            modifier = Modifier
                                .clip(RoundedCornerShape(50))
                                .background(tint.copy(alpha = 0.10f))
                                .padding(horizontal = 10.dp, vertical = 5.dp),
                        )
                    }
                }
            }
        }
    }
}
