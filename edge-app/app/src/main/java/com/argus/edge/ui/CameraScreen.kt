package com.argus.edge.ui

import android.Manifest
import android.app.Application
import android.content.pm.PackageManager
import android.os.Build
import android.util.Size
import android.view.Surface as ViewSurface
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.Preview
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
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
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Hub
import androidx.compose.material.icons.rounded.LinkOff
import androidx.compose.material.icons.rounded.Settings
import androidx.compose.material.icons.rounded.Videocam
import androidx.compose.material.icons.rounded.WifiFind
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewmodel.compose.viewModel
import com.argus.edge.ArgusApp
import com.argus.edge.BuildConfig
import com.argus.edge.camera.CameraStreamer
import com.argus.edge.core.Net
import com.argus.edge.link.Discovery
import com.argus.edge.link.FrameClient
import com.argus.edge.link.LinkState
import com.argus.edge.link.Peer
import com.argus.edge.processing.Messages
import com.argus.edge.ui.theme.Argus
import com.argus.edge.ui.theme.Mono
import java.util.concurrent.Executors

class CameraViewModel(app: Application) : AndroidViewModel(app) {
    val prefs = (app as ArgusApp).prefs
    val client = FrameClient(BuildConfig.VERSION_NAME)
    val discovery = Discovery(app)
    val streamer = CameraStreamer(client)
    val analysisExecutor = Executors.newSingleThreadExecutor { r -> Thread(r, "argus-camera") }

    var cameraId by mutableStateOf(prefs.cameraId)
        private set
    private var linkedHost: Pair<String, Int>? = null

    init {
        // A camera phone that reboots on the bus should resume streaming with nobody touching it.
        prefs.lastProcessorHost.takeIf { it.isNotBlank() }?.let { last ->
            link(last.substringBefore(':'), last.substringAfter(':', "").toIntOrNull() ?: Net.FRAME_PORT)
        }
    }

    fun link(host: String, port: Int) {
        linkedHost = host to port
        prefs.lastProcessorHost = if (port == Net.FRAME_PORT) host else "$host:$port"
        client.link(host, port, cameraId, "${Build.MANUFACTURER} ${Build.MODEL}".trim())
    }

    /** An explicit unlink also forgets the host, so the next launch doesn't auto-relink. */
    fun unlink() { linkedHost = null; prefs.lastProcessorHost = ""; client.unlink() }

    fun choosePosition(id: String) {
        if (id == cameraId) return
        cameraId = id
        prefs.cameraId = id
        linkedHost?.let { (h, p) -> link(h, p) } // re-announce under the new position
    }

    override fun onCleared() {
        client.unlink()
        discovery.stopBrowsing()
        analysisExecutor.shutdown()
    }
}

@Composable
fun CameraScreen(onSettings: () -> Unit, vm: CameraViewModel = viewModel()) {
    val context = LocalContext.current
    var granted by remember {
        mutableStateOf(ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED)
    }
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted = it }
    LaunchedEffect(Unit) { if (!granted) launcher.launch(Manifest.permission.CAMERA) }

    DisposableEffect(Unit) {
        vm.discovery.startBrowsing()
        onDispose { vm.discovery.stopBrowsing() }
    }

    val state by vm.client.state.collectAsState()
    val streamCfg by vm.client.config.collectAsState()
    val peers by vm.discovery.peers.collectAsState()

    Box(Modifier.fillMaxSize().background(Argus.Void)) {
        if (granted) CameraPreview(vm) else PermissionNeeded { launcher.launch(Manifest.permission.CAMERA) }

        // top scrim + bar
        Column(
            Modifier
                .fillMaxWidth()
                .background(Brush.verticalGradient(listOf(Argus.Void.copy(alpha = 0.92f), Color.Transparent)))
                .statusBarsPadding()
                .padding(start = 18.dp, end = 8.dp, top = 8.dp, bottom = 26.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                ArgusEye(30.dp)
                Spacer(Modifier.width(10.dp))
                Column(Modifier.weight(1f)) {
                    Text("Camera", style = MaterialTheme.typography.titleMedium, color = Argus.Text)
                    Text(vm.cameraId.uppercase(), style = MaterialTheme.typography.labelSmall, color = Argus.Accent)
                }
                LinkPill(state)
                IconButton(onClick = onSettings) { Icon(Icons.Rounded.Settings, "Settings", tint = Argus.TextDim) }
            }
            Spacer(Modifier.height(12.dp))
            Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Messages.CAMERA_IDS.forEach { id ->
                    FilterChip(
                        selected = vm.cameraId == id,
                        onClick = { vm.choosePosition(id) },
                        label = { Text(id.replaceFirstChar { it.uppercase() }) },
                        colors = FilterChipDefaults.filterChipColors(
                            containerColor = Argus.Surface.copy(alpha = 0.7f),
                            labelColor = Argus.TextDim,
                            selectedContainerColor = Argus.Accent.copy(alpha = 0.22f),
                            selectedLabelColor = Argus.Accent,
                        ),
                        border = FilterChipDefaults.filterChipBorder(
                            enabled = true, selected = vm.cameraId == id,
                            borderColor = Argus.Outline, selectedBorderColor = Argus.Accent.copy(alpha = 0.6f),
                        ),
                    )
                }
            }
        }

        // bottom panel
        Surface(
            modifier = Modifier.align(Alignment.BottomCenter).fillMaxWidth().widthIn(max = 640.dp),
            shape = RoundedCornerShape(topStart = 30.dp, topEnd = 30.dp),
            color = Argus.Surface.copy(alpha = 0.94f),
            border = BorderStroke(1.dp, Argus.Outline),
        ) {
            Column(Modifier.navigationBarsPadding().padding(20.dp)) {
                AnimatedContent(
                    targetState = state is LinkState.Streaming,
                    transitionSpec = { fadeIn(tween(250)) togetherWith fadeOut(tween(200)) },
                    label = "panel",
                ) { streaming ->
                    if (streaming) StreamingPanel(state as? LinkState.Streaming, streamCfg.mode == "capture", vm::unlink)
                    else LinkPanel(state, peers, vm.prefs.lastProcessorHost, vm::link, vm::unlink)
                }
            }
        }
    }
}

@Composable
private fun CameraPreview(vm: CameraViewModel) {
    val context = LocalContext.current
    val owner = LocalLifecycleOwner.current
    val configuration = LocalConfiguration.current
    val previewView = remember { PreviewView(context).apply { scaleType = PreviewView.ScaleType.FILL_CENTER; keepScreenOn = true } }
    val useCases = remember {
        val selector = ResolutionSelector.Builder()
            // 1080p sensor frames; detection mode downscales to 1280, dataset capture sends them whole.
            .setResolutionStrategy(ResolutionStrategy(Size(1920, 1080), ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER))
            .build()
        val preview = Preview.Builder().build()
        val analysis = ImageAnalysis.Builder()
            .setResolutionSelector(selector)
            .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
            .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_RGBA_8888)
            .build()
            .also { it.setAnalyzer(vm.analysisExecutor, vm.streamer) }
        preview to analysis
    }

    DisposableEffect(owner) {
        val future = ProcessCameraProvider.getInstance(context)
        future.addListener({
            val provider = future.get()
            val (preview, analysis) = useCases
            preview.surfaceProvider = previewView.surfaceProvider
            provider.unbindAll()
            runCatching { provider.bindToLifecycle(owner, CameraSelector.DEFAULT_BACK_CAMERA, preview, analysis) }
        }, ContextCompat.getMainExecutor(context))
        onDispose { runCatching { future.get().unbindAll() } }
    }

    // Bus phones get mounted in landscape; keep the stream upright whichever way it's held.
    LaunchedEffect(configuration.orientation) {
        val rotation = previewView.display?.rotation ?: ViewSurface.ROTATION_0
        useCases.first.targetRotation = rotation
        useCases.second.targetRotation = rotation
    }

    AndroidView({ previewView }, Modifier.fillMaxSize())
}

@Composable
private fun LinkPill(state: LinkState) = when (state) {
    is LinkState.Streaming -> StatusPill("Streaming", Argus.Good)
    is LinkState.Connecting -> StatusPill("Linking…", Argus.Warn)
    is LinkState.Failed -> StatusPill("Retrying", Argus.Bad)
    LinkState.Idle -> StatusPill("Not linked", Argus.TextDim, pulsing = false)
}

@Composable
private fun LinkPanel(
    state: LinkState,
    peers: List<Peer>,
    lastHost: String,
    onLink: (String, Int) -> Unit,
    onCancel: () -> Unit,
) {
    var manual by remember { mutableStateOf(lastHost) }
    Column {
        Text("Link to a processing client", style = MaterialTheme.typography.titleMedium, color = Argus.Text)
        Text(
            "Join the same Wi-Fi or hotspot as the processing phone, then pick it below.",
            style = MaterialTheme.typography.bodySmall, color = Argus.TextDim,
        )
        Spacer(Modifier.height(14.dp))

        when (state) {
            is LinkState.Connecting -> Busy("Linking to ${state.host}…  attempt ${state.attempt}", onCancel)
            is LinkState.Failed -> Busy("Lost ${state.host}: ${state.reason}. Retrying…", onCancel)
            else -> {
                if (peers.isEmpty()) Searching()
                peers.forEach { p ->
                    PeerRow(p) { onLink(p.host, p.port) }
                    Spacer(Modifier.height(8.dp))
                }
            }
        }

        Spacer(Modifier.height(12.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(
                value = manual,
                onValueChange = { manual = it.trim() },
                modifier = Modifier.weight(1f),
                singleLine = true,
                placeholder = { Text("or type its address, e.g. 192.168.43.1") },
                textStyle = MaterialTheme.typography.bodyMedium.copy(fontFamily = Mono),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                shape = RoundedCornerShape(14.dp),
                colors = OutlinedTextFieldDefaults.colors(unfocusedBorderColor = Argus.Outline, focusedBorderColor = Argus.Accent),
            )
            Spacer(Modifier.width(10.dp))
            Button(
                onClick = {
                    val host = manual.substringBefore(':')
                    val port = manual.substringAfter(':', "").toIntOrNull() ?: Net.FRAME_PORT
                    if (host.isNotBlank()) onLink(host, port)
                },
                enabled = manual.isNotBlank(),
                shape = RoundedCornerShape(14.dp),
                modifier = Modifier.height(56.dp),
            ) { Text("Link") }
        }
    }
}

@Composable
private fun PeerRow(p: Peer, onLink: () -> Unit) {
    Surface(
        onClick = onLink,
        shape = RoundedCornerShape(16.dp),
        color = Argus.SurfaceHigh,
        border = BorderStroke(1.dp, Argus.Good.copy(alpha = 0.35f)),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(Modifier.padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(40.dp).clip(RoundedCornerShape(12.dp)).background(Argus.Good.copy(alpha = 0.14f)), contentAlignment = Alignment.Center) {
                Icon(Icons.Rounded.Hub, null, tint = Argus.Good)
            }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(p.name, style = MaterialTheme.typography.titleSmall, color = Argus.Text)
                Text("${p.host}:${p.port}", style = MaterialTheme.typography.bodySmall.copy(fontFamily = Mono), color = Argus.TextDim)
            }
            Text("LINK", style = MaterialTheme.typography.labelLarge, color = Argus.Good)
        }
    }
}

@Composable
private fun Searching() {
    val t = rememberInfiniteTransition(label = "radar")
    val r by t.animateFloat(0f, 1f, infiniteRepeatable(tween(1800, easing = LinearEasing)), label = "r")
    Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(44.dp), contentAlignment = Alignment.Center) {
            Canvas(Modifier.size(44.dp)) {
                val c = Offset(size.width / 2, size.height / 2)
                for (k in 0..1) {
                    val p = (r + k * 0.5f) % 1f
                    drawCircle(Argus.Accent.copy(alpha = (1 - p) * 0.6f), radius = size.width / 2 * p, center = c, style = Stroke(2f))
                }
            }
            Icon(Icons.Rounded.WifiFind, null, tint = Argus.Accent, modifier = Modifier.size(20.dp))
        }
        Spacer(Modifier.width(12.dp))
        Column {
            Text("Searching this Wi-Fi…", style = MaterialTheme.typography.titleSmall, color = Argus.Text)
            Text("Processing clients appear here automatically.", style = MaterialTheme.typography.bodySmall, color = Argus.TextDim)
        }
    }
}

@Composable
private fun Busy(text: String, onCancel: () -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        PulseDot(Argus.Warn)
        Spacer(Modifier.width(10.dp))
        Text(text, style = MaterialTheme.typography.bodyMedium, color = Argus.Text, modifier = Modifier.weight(1f))
        OutlinedButton(onClick = onCancel, shape = RoundedCornerShape(12.dp)) { Text("Cancel") }
    }
}

@Composable
private fun StreamingPanel(s: LinkState.Streaming?, capture: Boolean, onUnlink: () -> Unit) {
    s ?: return
    Column {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(46.dp).clip(RoundedCornerShape(14.dp)).background(Argus.Good.copy(alpha = 0.15f)), contentAlignment = Alignment.Center) {
                Icon(Icons.Rounded.Videocam, null, tint = Argus.Good)
            }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text("Streaming to", style = MaterialTheme.typography.bodySmall, color = Argus.TextDim)
                Text(s.processorName, style = MaterialTheme.typography.titleMedium, color = Argus.Text)
                Text(s.host, style = MaterialTheme.typography.bodySmall.copy(fontFamily = Mono), color = Argus.TextFaint)
            }
            if (capture) StatusPill("Dataset · 1080p", Argus.Accent)
        }
        Spacer(Modifier.height(18.dp))
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Metric("fps", "%.1f".format(s.fps))
            Metric("kbit/s", "%.0f".format(s.kbps))
            Metric("rtt", if (s.rttMs < 0) "—" else "${s.rttMs} ms")
            Metric("clock", "${if (s.offsetMs >= 0) "+" else ""}${s.offsetMs} ms", color = Argus.Accent)
        }
        Spacer(Modifier.height(6.dp))
        Text(
            "${s.framesSent} frames sent · capture times synced to the processing client",
            style = MaterialTheme.typography.bodySmall, color = Argus.TextFaint,
        )
        Spacer(Modifier.height(16.dp))
        OutlinedButton(
            onClick = onUnlink,
            modifier = Modifier.fillMaxWidth().height(50.dp),
            shape = RoundedCornerShape(14.dp),
            colors = ButtonDefaults.outlinedButtonColors(contentColor = Argus.Bad),
            border = BorderStroke(1.dp, Argus.Bad.copy(alpha = 0.5f)),
        ) {
            Icon(Icons.Rounded.LinkOff, null)
            Spacer(Modifier.width(8.dp))
            Text("Unlink")
        }
    }
}

@Composable
private fun PermissionNeeded(onAsk: () -> Unit) {
    Column(Modifier.fillMaxSize().padding(32.dp), verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally) {
        Icon(Icons.Rounded.Videocam, null, tint = Argus.TextDim, modifier = Modifier.size(48.dp))
        Spacer(Modifier.height(12.dp))
        Text("Camera access is needed to stream.", style = MaterialTheme.typography.titleMedium, color = Argus.Text)
        Spacer(Modifier.height(12.dp))
        Button(onClick = onAsk) { Text("Allow camera") }
    }
}
