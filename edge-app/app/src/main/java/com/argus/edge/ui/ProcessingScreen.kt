package com.argus.edge.ui

import android.Manifest
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.pm.PackageManager
import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.CameraAlt
import androidx.compose.material.icons.rounded.CloudUpload
import androidx.compose.material.icons.rounded.ContentCopy
import androidx.compose.material.icons.rounded.GpsFixed
import androidx.compose.material.icons.rounded.GpsOff
import androidx.compose.material.icons.rounded.Memory
import androidx.compose.material.icons.rounded.Movie
import androidx.compose.material.icons.rounded.PhotoLibrary
import androidx.compose.material.icons.rounded.PlayArrow
import androidx.compose.material.icons.rounded.Settings
import androidx.compose.material.icons.rounded.Stop
import androidx.compose.material.icons.rounded.Straighten
import androidx.compose.material.icons.rounded.Timer
import androidx.compose.material.icons.rounded.Warning
import androidx.compose.material.icons.rounded.Wifi
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.argus.edge.core.GateMode
import com.argus.edge.core.Net
import com.argus.edge.core.Prefs
import com.argus.edge.link.CameraLink
import com.argus.edge.processing.DatasetStats
import com.argus.edge.processing.EdgeEngine
import com.argus.edge.processing.EngineState
import com.argus.edge.processing.Finding
import com.argus.edge.processing.Fix
import com.argus.edge.processing.TilePreview
import com.argus.edge.ui.theme.Argus
import com.argus.edge.ui.theme.Mono
import kotlinx.coroutines.delay
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

@Composable
fun ProcessingScreen(engine: EdgeEngine, prefs: Prefs, onSettings: () -> Unit) {
    val context = LocalContext.current
    val view = LocalView.current

    val locationLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        engine.ensureLocation()
    }
    val videoPicker = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) engine.startTestVideo(uri)
    }

    DisposableEffect(Unit) {
        engine.activate()
        view.keepScreenOn = true
        if (ContextCompat_hasLocation(context).not()) {
            locationLauncher.launch(arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION))
        }
        onDispose { view.keepScreenOn = false }
    }

    val state by engine.state.collectAsState()
    val links by engine.links.collectAsState()
    val previews by engine.previews.collectAsState()
    val findings by engine.findings.collectAsState()
    val fix by engine.location.latest.collectAsState()
    val counts by engine.storage.counts.collectAsState()
    val ds by engine.dataset.stats.collectAsState()
    var capture by remember { mutableStateOf(prefs.captureEnabled) }
    var gate by remember { mutableStateOf(prefs.gateMode) }
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(Unit) { while (true) { now = System.currentTimeMillis(); delay(1000) } }

    Box(Modifier.fillMaxSize().argusBackdrop(if (state.running) Argus.Good else Argus.Accent)) {
        LazyColumn(
            Modifier.fillMaxSize().statusBarsPadding(),
            contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 8.dp, bottom = 120.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            item { Header(prefs, onSettings) }
            item { SessionHero(state, links.size, now) { if (state.running) engine.stopSession() else engine.startSession() } }
            state.modelError?.let { err -> item { Banner("Model failed to load: $err", Argus.Bad) } }
            state.notice?.let { n -> item { Banner(n, Argus.Warn) } }
            if (!state.locationOn) item {
                Banner("GPS is off or not permitted. Potholes will be kept in processed/ only until there is a fix.", Argus.Warn)
            }
            item { StatsGrid(state, links.size, counts["pothole"] ?: 0) }

            item {
                SectionHeader("Cameras · ${links.size}") {
                    if (engine.testVideoActive) TextButton(onClick = engine::stopTestVideo) { Text("Stop test video", color = Argus.Warn) }
                    else TextButton(onClick = { videoPicker.launch("video/*") }) {
                        Icon(Icons.Rounded.Movie, null, Modifier.size(16.dp)); Spacer(Modifier.width(6.dp)); Text("Test video")
                    }
                }
            }
            if (links.isEmpty()) item { NoCameras() }
            else items(links.values.sortedBy { it.cameraId }, key = { it.cameraId }) { l ->
                CameraTile(l, previews[l.cameraId], now)
            }

            item { SectionHeader("Dataset capture") }
            item {
                DatasetPanel(capture, prefs.captureSpacingM, ds, state.running) {
                    capture = it; engine.setCapture(it)
                }
            }

            item { SectionHeader("Location & sampling") }
            item {
                LocationPanel(fix, now, engine.location.odometerM, gate) {
                    gate = it; prefs.gateMode = it
                }
            }

            item { SectionHeader("Recent potholes · ${findings.size}") }
            item { FindingsRow(findings) }

            item { SectionHeader("Server hand-off") }
            item { HandoffPanel(context, prefs, state, counts, engine.storage.root.absolutePath) }
        }
    }
}

private fun ContextCompat_hasLocation(context: Context) =
    context.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED

@Composable
private fun Header(prefs: Prefs, onSettings: () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
        ArgusEye(38.dp)
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text("Processing client", style = MaterialTheme.typography.titleMedium, color = Argus.Text)
            val sub = listOf(prefs.deviceId, prefs.busId.ifBlank { null }?.let { "bus $it" }, prefs.routeId.ifBlank { null }?.let { "route $it" })
                .filterNotNull().joinToString("  ·  ")
            Text(sub, style = MaterialTheme.typography.labelMedium.copy(fontFamily = Mono), color = Argus.TextDim, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        IconButton(onClick = onSettings) { Icon(Icons.Rounded.Settings, "Settings", tint = Argus.TextDim) }
    }
}

@Composable
private fun SessionHero(state: EngineState, cameras: Int, now: Long, onToggle: () -> Unit) {
    val tint by animateColorAsState(if (state.running) Argus.Good else Argus.Accent, label = "tint")
    val scale by animateFloatAsState(if (state.running) 1f else 0.96f, label = "scale")
    Panel(highlight = tint) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                StatusPill(if (state.running) "Detecting" else "Idle", tint, pulsing = state.running)
                Spacer(Modifier.height(12.dp))
                Text(
                    if (state.running) "Watching the road on $cameras camera${if (cameras == 1) "" else "s"}"
                    else "Ready. Cameras can link now.",
                    style = MaterialTheme.typography.titleLarge, color = Argus.Text,
                )
                Spacer(Modifier.height(4.dp))
                Text(
                    if (state.running) "Session ${state.sessionId} · ${elapsed(now - state.sessionStartMs)}"
                    else "Start a session to run pothole detection and write findings.",
                    style = MaterialTheme.typography.bodySmall, color = Argus.TextDim,
                )
            }
            Spacer(Modifier.width(12.dp))
            Box(
                Modifier
                    .size(76.dp)
                    .scale(scale)
                    .clip(CircleShape)
                    .background(Brush.radialGradient(listOf(tint.copy(alpha = 0.55f), tint.copy(alpha = 0.18f))))
                    .border(2.dp, tint, CircleShape)
                    .clickable(onClick = onToggle),
                contentAlignment = Alignment.Center,
            ) {
                Icon(if (state.running) Icons.Rounded.Stop else Icons.Rounded.PlayArrow, if (state.running) "Stop" else "Start",
                    tint = Argus.Text, modifier = Modifier.size(36.dp))
            }
        }
    }
}

@Composable
private fun StatsGrid(state: EngineState, cameras: Int, records: Int) {
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            StatTile("Cameras", "$cameras", Icons.Rounded.CameraAlt, Argus.Accent, Modifier.weight(1f), sub = "linked now")
            StatTile("Potholes", "${state.potholes}", Icons.Rounded.Warning, Argus.Pothole, Modifier.weight(1f), sub = "this session")
        }
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            StatTile("Records", "$records", Icons.Rounded.CloudUpload, Argus.Good, Modifier.weight(1f), sub = "in processed/pothole")
            StatTile(
                "Inference", "%.1f/s".format(state.inferenceFps), Icons.Rounded.Memory, Argus.Accent, Modifier.weight(1f),
                sub = if (state.lastInferenceMs > 0) "${state.lastInferenceMs} ms · ${state.inferences} runs" else "not running",
            )
        }
    }
}

@Composable
private fun CameraTile(link: CameraLink, preview: TilePreview?, now: Long) {
    Surface(shape = RoundedCornerShape(22.dp), color = Argus.Surface, border = BorderStroke(1.dp, Argus.Outline)) {
        Column {
            Box(Modifier.fillMaxWidth().aspectRatio(16f / 9f).background(Argus.SurfaceHigh)) {
                if (preview != null) {
                    Image(preview.bitmap.asImageBitmap(), null, Modifier.fillMaxSize(), contentScale = ContentScale.Fit)
                    if (preview.detections.isNotEmpty()) DetectionOverlay(preview)
                } else {
                    Text("Waiting for frames…", Modifier.align(Alignment.Center), color = Argus.TextFaint, style = MaterialTheme.typography.bodySmall)
                }
                Row(Modifier.padding(10.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        link.cameraId.uppercase(),
                        style = MaterialTheme.typography.labelSmall,
                        color = Argus.Void,
                        modifier = Modifier.clip(RoundedCornerShape(6.dp)).background(Argus.Accent).padding(horizontal = 8.dp, vertical = 4.dp),
                    )
                    if (link.isTestSource) {
                        Spacer(Modifier.width(6.dp))
                        Text("TEST VIDEO", style = MaterialTheme.typography.labelSmall, color = Argus.Void,
                            modifier = Modifier.clip(RoundedCornerShape(6.dp)).background(Argus.Warn).padding(horizontal = 8.dp, vertical = 4.dp))
                    }
                }
                if (preview?.inferred == true && preview.detections.isNotEmpty()) {
                    Text(
                        "POTHOLE ×${preview.detections.size}",
                        style = MaterialTheme.typography.labelSmall, color = Argus.Void,
                        modifier = Modifier.align(Alignment.TopEnd).padding(10.dp).clip(RoundedCornerShape(6.dp))
                            .background(Argus.Pothole).padding(horizontal = 8.dp, vertical = 4.dp),
                    )
                }
            }
            Row(Modifier.fillMaxWidth().padding(14.dp), horizontalArrangement = Arrangement.SpaceBetween) {
                Metric("fps", "%.1f".format(link.fps))
                Metric("latency", "${link.latencyMs.coerceAtLeast(0)} ms")
                Metric("clock", "${if (link.offsetMs >= 0) "+" else ""}${link.offsetMs}", color = Argus.Accent)
                Metric("res", if (link.width > 0) "${link.width}×${link.height}" else "—")
            }
            Text(
                "${link.name} · ${link.address} · linked ${elapsed(now - link.connectedAtMs)} ago",
                style = MaterialTheme.typography.bodySmall, color = Argus.TextFaint,
                modifier = Modifier.padding(start = 14.dp, end = 14.dp, bottom = 12.dp),
                maxLines = 1, overflow = TextOverflow.Ellipsis,
            )
        }
    }
}

@Composable
private fun DetectionOverlay(p: TilePreview) {
    Canvas(Modifier.fillMaxSize()) {
        val bw = p.bitmap.width.toFloat(); val bh = p.bitmap.height.toFloat()
        val s = minOf(size.width / bw, size.height / bh)
        val ox = (size.width - bw * s) / 2; val oy = (size.height - bh * s) / 2
        for (d in p.detections) {
            val tl = Offset(ox + d.x1 * s, oy + d.y1 * s)
            val sz = Size(d.width * s, d.height * s)
            drawRect(Argus.Pothole.copy(alpha = 0.18f), tl, sz)
            drawRect(Argus.Pothole, tl, sz, style = Stroke(3.dp.toPx()))
        }
    }
}

@Composable
private fun NoCameras() {
    val ips = remember { Net.localIpv4() }
    Panel {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(44.dp).clip(RoundedCornerShape(14.dp)).background(Argus.Accent.copy(alpha = 0.12f)), contentAlignment = Alignment.Center) {
                Icon(Icons.Rounded.Wifi, null, tint = Argus.Accent)
            }
            Spacer(Modifier.width(12.dp))
            Column {
                Text("No cameras linked yet", style = MaterialTheme.typography.titleSmall, color = Argus.Text)
                Text("On each camera phone: open ARGUS → Camera → pick this phone.", style = MaterialTheme.typography.bodySmall, color = Argus.TextDim)
            }
        }
        Spacer(Modifier.height(12.dp))
        Text("If it doesn't appear, type one of these addresses on the camera phone:", style = MaterialTheme.typography.bodySmall, color = Argus.TextFaint)
        Spacer(Modifier.height(6.dp))
        if (ips.isEmpty()) Text("Not on any Wi-Fi. Turn on this phone's hotspot or join the bus Wi-Fi.", style = MaterialTheme.typography.bodySmall, color = Argus.Warn)
        ips.forEach { KeyValue("address", it) }
    }
}

@Composable
private fun LocationPanel(fix: Fix?, now: Long, odometer: Double, gate: GateMode, onGate: (GateMode) -> Unit) {
    Panel {
        val fresh = fix != null && now - fix.wallMs < 5000
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(if (fresh) Icons.Rounded.GpsFixed else Icons.Rounded.GpsOff, null, tint = if (fresh) Argus.Good else Argus.Warn)
            Spacer(Modifier.width(10.dp))
            Text(
                when { fix == null -> "Waiting for GPS fix"; fresh -> "GPS fix · ±%.0f m".format(fix.accuracyM); else -> "GPS fix is stale" },
                style = MaterialTheme.typography.titleSmall, color = Argus.Text, modifier = Modifier.weight(1f),
            )
            Text("%.1f km/h".format((fix?.speedMs ?: 0f) * 3.6f), style = MaterialTheme.typography.titleSmall.copy(fontFamily = Mono), color = Argus.Text)
        }
        if (fix != null) {
            Spacer(Modifier.height(4.dp))
            Text("%.6f, %.6f".format(fix.lat, fix.lon), style = MaterialTheme.typography.bodySmall.copy(fontFamily = Mono), color = Argus.TextDim)
        }
        Spacer(Modifier.height(14.dp))
        val segColors = SegmentedButtonDefaults.colors(
            activeContainerColor = Argus.Accent.copy(alpha = 0.18f), activeContentColor = Argus.Accent,
            activeBorderColor = Argus.Accent.copy(alpha = 0.6f),
            inactiveContainerColor = Argus.SurfaceHigh, inactiveContentColor = Argus.TextDim, inactiveBorderColor = Argus.Outline,
        )
        SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
            SegmentedButton(
                selected = gate == GateMode.DISTANCE, onClick = { onGate(GateMode.DISTANCE) },
                shape = SegmentedButtonDefaults.itemShape(0, 2), colors = segColors,
                icon = { Icon(Icons.Rounded.Straighten, null, Modifier.size(16.dp)) },
            ) { Text("Every 5 m") }
            SegmentedButton(
                selected = gate == GateMode.TIME, onClick = { onGate(GateMode.TIME) },
                shape = SegmentedButtonDefaults.itemShape(1, 2), colors = segColors,
                icon = { Icon(Icons.Rounded.Timer, null, Modifier.size(16.dp)) },
            ) { Text("Every 0.5 s (test)") }
        }
        Spacer(Modifier.height(8.dp))
        Text(
            if (gate == GateMode.DISTANCE) "Samples by distance travelled (%.0f m so far). A stopped bus runs no inference.".format(odometer)
            else "Test mode: samples by time, even when stopped. Use for bench tests only.",
            style = MaterialTheme.typography.bodySmall, color = if (gate == GateMode.TIME) Argus.Warn else Argus.TextFaint,
        )
    }
}

@Composable
private fun FindingsRow(findings: List<Finding>) {
    if (findings.isEmpty()) {
        Panel { Text("No potholes yet this run. They appear here the moment one is detected.", style = MaterialTheme.typography.bodySmall, color = Argus.TextDim) }
        return
    }
    val fmt = remember { SimpleDateFormat("HH:mm:ss", Locale.US) }
    LazyRow(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        items(findings, key = { it.id }) { f ->
            Surface(shape = RoundedCornerShape(18.dp), color = Argus.Surface, border = BorderStroke(1.dp, Argus.Outline), modifier = Modifier.width(150.dp)) {
                Column {
                    Image(f.thumb.asImageBitmap(), null, Modifier.fillMaxWidth().height(110.dp), contentScale = ContentScale.Crop)
                    Column(Modifier.padding(10.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text("${(f.confidence * 100).toInt()}%", style = MaterialTheme.typography.titleSmall.copy(fontFamily = Mono), color = Argus.Pothole)
                            Spacer(Modifier.weight(1f))
                            Text(f.cameraId.uppercase(), style = MaterialTheme.typography.labelSmall, color = Argus.TextDim)
                        }
                        Text(fmt.format(Date(f.atMs)), style = MaterialTheme.typography.bodySmall.copy(fontFamily = Mono), color = Argus.TextFaint)
                        Text(
                            if (f.saved) "saved" else "no GPS · logged only",
                            style = MaterialTheme.typography.labelMedium,
                            color = if (f.saved) Argus.Good else Argus.Warn,
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun HandoffPanel(context: Context, prefs: Prefs, state: EngineState, counts: Map<String, Int>, root: String) {
    val ip = remember { Net.localIpv4().firstOrNull() ?: "<this-phone-ip>" }
    val base = "http://$ip:${Net.API_PORT}/api/v1"
    Panel {
        Row(verticalAlignment = Alignment.CenterVertically) {
            StatusPill(if (state.apiOn) "API serving" else "API off", if (state.apiOn) Argus.Good else Argus.Bad, pulsing = state.apiOn)
            Spacer(Modifier.weight(1f))
            Text("read-only", style = MaterialTheme.typography.labelLarge, color = Argus.TextDim)
        }
        Spacer(Modifier.height(12.dp))
        Text(
            "The ARGUS backend reads processed/ over this API. Nothing is deleted from this phone.",
            style = MaterialTheme.typography.bodySmall, color = Argus.TextDim,
        )
        Spacer(Modifier.height(8.dp))
        CopyRow(context, "endpoint", "$base/processed")
        CopyRow(context, "token", prefs.apiToken)
        Spacer(Modifier.height(8.dp))
        counts.forEach { (c, n) -> KeyValue("processed/$c/", "$n records") }
        Text(root, style = MaterialTheme.typography.bodySmall.copy(fontFamily = Mono), color = Argus.TextFaint)
    }
}

@Composable
private fun DatasetPanel(on: Boolean, spacingM: Int, ds: DatasetStats, running: Boolean, onToggle: (Boolean) -> Unit) {
    Panel(highlight = if (ds.active) Argus.Accent else null) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(40.dp).clip(RoundedCornerShape(12.dp)).background(Argus.Accent.copy(alpha = 0.12f)), contentAlignment = Alignment.Center) {
                Icon(Icons.Rounded.PhotoLibrary, null, tint = Argus.Accent)
            }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text("Record training frames", style = MaterialTheme.typography.titleSmall, color = Argus.Text)
                Text("Clean 1080p frame from every camera every $spacingM m, pothole or not.",
                    style = MaterialTheme.typography.bodySmall, color = Argus.TextDim)
            }
            Switch(
                checked = on, onCheckedChange = onToggle,
                colors = SwitchDefaults.colors(checkedThumbColor = Argus.Void, checkedTrackColor = Argus.Accent,
                    uncheckedTrackColor = Argus.SurfaceTop, uncheckedBorderColor = Argus.Outline),
            )
        }
        if (on) {
            Spacer(Modifier.height(14.dp))
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Metric("frames", "${ds.frames}")
                Metric("size", "%.0f MB".format(ds.bytes / 1e6))
                Metric("free", "%.1f GB".format(ds.freeBytes / 1e9), color = if (ds.freeBytes < 2e9) Argus.Warn else Argus.Text)
            }
            if (ds.perCamera.isNotEmpty()) {
                Spacer(Modifier.height(6.dp))
                Text(ds.perCamera.entries.sortedBy { it.key }.joinToString("  ·  ") { "${it.key} ${it.value}" },
                    style = MaterialTheme.typography.bodySmall.copy(fontFamily = Mono), color = Argus.TextDim)
            }
            Spacer(Modifier.height(8.dp))
            Text(
                when {
                    ds.stoppedReason != null -> ds.stoppedReason
                    !running -> "Starts with the next session. Cameras switch to 1080p now."
                    else -> "Saving to ${ds.sessionDir?.substringAfterLast("/argus/") ?: "dataset/"} — frames, manifest, YOLO pre-labels."
                },
                style = MaterialTheme.typography.bodySmall,
                color = if (ds.stoppedReason != null) Argus.Warn else Argus.TextFaint,
            )
        }
    }
}

@Composable
private fun CopyRow(context: Context, label: String, value: String) {
    Row(
        Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp)
            .clip(RoundedCornerShape(12.dp))
            .background(Argus.SurfaceHigh)
            .clickable {
                (context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager).setPrimaryClip(ClipData.newPlainText(label, value))
                Toast.makeText(context, "Copied $label", Toast.LENGTH_SHORT).show()
            }
            .padding(horizontal = 12.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(label.uppercase(), style = MaterialTheme.typography.labelSmall, color = Argus.TextFaint, modifier = Modifier.width(78.dp))
        Text(value, style = MaterialTheme.typography.bodySmall.copy(fontFamily = Mono, fontWeight = FontWeight.Medium), color = Argus.Text,
            modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
        Icon(Icons.Rounded.ContentCopy, "Copy", tint = Argus.TextDim, modifier = Modifier.size(16.dp))
    }
}

@Composable
private fun Banner(text: String, color: Color) {
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(color.copy(alpha = 0.12f))
            .border(1.dp, color.copy(alpha = 0.4f), RoundedCornerShape(16.dp)).padding(14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(Icons.Rounded.Warning, null, tint = color, modifier = Modifier.size(18.dp))
        Spacer(Modifier.width(10.dp))
        Text(text, style = MaterialTheme.typography.bodySmall, color = Argus.Text)
    }
}

private fun elapsed(ms: Long): String {
    val s = (ms / 1000).coerceAtLeast(0)
    return when {
        s < 60 -> "${s}s"
        s < 3600 -> "${s / 60}m ${s % 60}s"
        else -> "${s / 3600}h ${(s % 3600) / 60}m"
    }
}
