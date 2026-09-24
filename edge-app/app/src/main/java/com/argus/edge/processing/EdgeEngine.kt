package com.argus.edge.processing

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.net.Uri
import android.util.Log
import com.argus.edge.BuildConfig
import com.argus.edge.core.Net
import com.argus.edge.core.Prefs
import com.argus.edge.core.Time
import com.argus.edge.link.CameraLink
import com.argus.edge.link.Discovery
import com.argus.edge.link.FpsCounter
import com.argus.edge.link.FrameServer
import com.argus.edge.link.ReceivedFrame
import com.argus.edge.link.StreamConfig
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.add
import kotlinx.serialization.json.addJsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.putJsonObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.UUID
import java.util.concurrent.Executors

data class EngineState(
    val active: Boolean = false,
    val running: Boolean = false,
    val sessionId: String? = null,
    val sessionStartMs: Long = 0,
    val inferences: Long = 0,
    val potholes: Long = 0,
    val recordsWritten: Long = 0,
    val inferenceFps: Float = 0f,
    val lastInferenceMs: Long = 0,
    val modelReady: Boolean = false,
    val modelError: String? = null,
    val locationOn: Boolean = false,
    val apiOn: Boolean = false,
    /** Why the last finding was not saved as a record, if it wasn't. Shown on screen. */
    val notice: String? = null,
)

/** What a camera tile shows: a downscaled frame and, just after an inference, its boxes. */
class TilePreview(val bitmap: Bitmap, val detections: List<Detection>, val atMs: Long, val inferred: Boolean)

class Finding(
    val id: String,
    val cameraId: String,
    val confidence: Float,
    val atMs: Long,
    val thumb: Bitmap,
    /** Saved to processed/pothole/ (false = no GPS fix, logged only). */
    val saved: Boolean,
)

/**
 * The processing client. Owns the frame server (cameras link to it), discovery, the GNSS
 * tracker, the local API, and — while a session runs — the inference loop that turns frames
 * into processed/ records and, when dataset capture is on, dataset/ training frames.
 */
class EdgeEngine(private val context: Context, private val prefs: Prefs) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val inferenceDispatcher = Executors.newSingleThreadExecutor { r -> Thread(r, "argus-infer") }.asCoroutineDispatcher()

    val storage = Storage(context)
    val server = FrameServer(Net.FRAME_PORT) { prefs.deviceId }
    val location = LocationTracker(context)
    private val discovery = Discovery(context)
    private val videoSource = VideoFileSource(context, server, scope)
    private var api: LocalApi? = null

    private var detector: PotholeDetector? = null
    private val gate = FrameGate()
    private var captureGate = FrameGate(metres = prefs.captureSpacingM.toDouble(), intervalMs = 1000)
    val dataset = DatasetRecorder(storage)
    private val inferFps = FpsCounter()

    private var sessionJob: Job? = null
    private var previewJob: Job? = null
    private var sessionId: String? = null
    private val startedMs = System.currentTimeMillis()

    private val _state = MutableStateFlow(EngineState())
    val state: StateFlow<EngineState> = _state.asStateFlow()

    private val _previews = MutableStateFlow<Map<String, TilePreview>>(emptyMap())
    val previews: StateFlow<Map<String, TilePreview>> = _previews.asStateFlow()

    private val _findings = MutableStateFlow<List<Finding>>(emptyList())
    val findings: StateFlow<List<Finding>> = _findings.asStateFlow()

    val links: StateFlow<Map<String, CameraLink>> get() = server.links
    val testVideoActive: Boolean get() = videoSource.active

    // ── role lifecycle ──────────────────────────────────────────────────────

    /** Processing role shown: cameras can link, the API serves, GNSS runs. No inference yet. */
    fun activate() {
        if (_state.value.active) return
        server.setStreamConfig(if (prefs.captureEnabled) StreamConfig.CAPTURE else StreamConfig.DETECT)
        server.start()
        discovery.advertise(prefs.deviceId, Net.FRAME_PORT)
        if (api == null) {
            api = LocalApi(Net.API_PORT, storage, { prefs.apiToken }, ::statusJson).also {
                _state.update { s -> s.copy(apiOn = it.startSafely()) }
            }
        }
        ensureLocation()
        previewJob = scope.launch { previewLoop() }
        _state.update { it.copy(active = true) }
    }

    fun deactivate() {
        stopSession()
        videoSource.stop()
        previewJob?.cancel()
        discovery.stopAdvertising()
        server.stop()
        api?.stop(); api = null
        location.stop()
        _previews.value = emptyMap()
        _state.update { it.copy(active = false, apiOn = false, locationOn = false) }
    }

    /** Call again after the location permission is granted. */
    fun ensureLocation() {
        val on = runCatching { location.start() }.getOrDefault(false)
        _state.update { it.copy(locationOn = on) }
    }

    // ── session ─────────────────────────────────────────────────────────────

    fun startSession() {
        if (sessionJob?.isActive == true) return
        val now = System.currentTimeMillis()
        val id = Time.sessionId(now)
        sessionId = id
        gate.reset()
        captureGate = FrameGate(metres = prefs.captureSpacingM.toDouble(), intervalMs = 1000)
        _state.update {
            it.copy(running = true, sessionId = id, sessionStartMs = now, inferences = 0, potholes = 0,
                recordsWritten = 0, notice = null)
        }
        sessionJob = scope.launch(inferenceDispatcher) {
            if (!loadModels()) { _state.update { it.copy(running = false) }; return@launch }
            storage.appendLine(storage.logFile(id), buildJsonObject { put("session", sessionJson(id, now)) }.toString())
            if (prefs.captureEnabled) dataset.begin(id, sessionJson(id, now), prefs.routeId)
            launch(Dispatchers.IO) { telemetryLoop() }
            inferenceLoop(id)
        }
    }

    fun stopSession() {
        sessionJob?.cancel()
        sessionJob = null
        dataset.end()
        _state.update { it.copy(running = false) }
    }

    /**
     * Dataset capture on/off. Also tells every camera phone to switch between detection
     * quality (1280, q80) and capture quality (1920, q92). Takes effect mid-session.
     */
    fun setCapture(on: Boolean) {
        prefs.captureEnabled = on
        server.setStreamConfig(if (on) StreamConfig.CAPTURE else StreamConfig.DETECT)
        val id = sessionId
        if (on && _state.value.running && id != null && !dataset.active) {
            captureGate = FrameGate(metres = prefs.captureSpacingM.toDouble(), intervalMs = 1000)
            dataset.begin(id, sessionJson(id, System.currentTimeMillis()), prefs.routeId)
        }
        if (!on) dataset.end()
    }

    fun startTestVideo(uri: Uri) {
        val free = Messages.CAMERA_IDS.firstOrNull { it !in server.cameraIds() } ?: return
        videoSource.start(uri, free)
    }

    fun stopTestVideo() = videoSource.stop()

    private fun loadModels(): Boolean {
        if (detector != null) return true
        return try {
            detector = PotholeDetector(context)
            _state.update { it.copy(modelReady = true, modelError = null) }
            true
        } catch (e: Throwable) {
            Log.e(TAG, "model load failed", e)
            _state.update { it.copy(modelReady = false, modelError = e.message ?: e.javaClass.simpleName) }
            false
        }
    }

    // ── the loop ────────────────────────────────────────────────────────────

    private val lastSeq = HashMap<String, Long>()

    private suspend fun inferenceLoop(sessionId: String) {
        val log = storage.logFile(sessionId)
        while (currentCoroutineContext().isActive) {
            var worked = false
            for (cam in server.cameraIds().sorted()) {
                val f = server.latest(cam) ?: continue
                if (f.seq == lastSeq[cam]) continue
                val now = System.currentTimeMillis()
                // Capture (every 10 m) and detection (every 5 m) are gated independently; a
                // captured frame is always also run through the model, for its pre-labels.
                val capture = dataset.active && captureGate.shouldInfer(cam, prefs.gateMode, location.odometerM, now)
                val infer = gate.shouldInfer(cam, prefs.gateMode, location.odometerM, now)
                if (!capture && !infer) continue
                lastSeq[cam] = f.seq
                worked = true
                runCatching { process(f, log, capture) }.onFailure { Log.e(TAG, "process $cam", it) }
            }
            if (!worked) delay(15)
        }
    }

    private fun process(f: ReceivedFrame, log: File, capture: Boolean) {
        val bmp = BitmapFactory.decodeByteArray(f.jpeg, 0, f.jpeg.size) ?: return
        val det = detector ?: return
        val t0 = System.nanoTime()
        val dets = det.detect(bmp, prefs.confidenceThreshold).filter { plausible(bmp, it) }
        val inferMs = (System.nanoTime() - t0) / 1_000_000
        val now = System.currentTimeMillis()
        inferFps.tick(now)

        val fix = location.at(f.captureMs)
        val who = Messages.Identity(prefs.deviceId, prefs.busId, prefs.routeId)
        val model = modelInfo(det)
        val stamp = Time.compact(f.captureMs)
        val saved = if (dets.isEmpty()) "none" else if (fix == null) "no_gnss_fix" else "saved"
        val obsIds = dets.map { UUID.randomUUID().toString() }
        val illum = Messages.illumination(meanLuma(bmp))

        if (capture) dataset.record(f, fix, location.odometerM, illum, dets, model)

        if (dets.isNotEmpty()) {
            // processed/pothole/: a contract Observation needs a position, so without a GPS fix
            // the detection is logged (below) but no record is written.
            if (fix != null) {
                val annotated = annotate(bmp, dets)
                val frameJpeg = jpeg(annotated, 85)
                annotated.recycle()
                dets.forEachIndexed { i, d ->
                    val id = obsIds[i]
                    val base = "$stamp-$id"
                    val crop = crop(bmp, d)
                    val cropJpeg = jpeg(crop, 90)
                    storage.writeRecord("pothole", "$base.jpg", cropJpeg)
                    storage.writeRecord("pothole", "$base-frame.jpg", frameJpeg)
                    val obs = Messages.observation(
                        id, who, f.cameraId, f.captureMs, d.score, fix, d, illum,
                        evidenceUri = "edge://${who.deviceId}/processed/pothole/$base.jpg",
                        evidenceSha256 = Messages.sha256Hex(cropJpeg),
                        evidenceBytes = cropJpeg.size,
                        model = model,
                    )
                    storage.writeRecord("pothole", "$base.json", obs.toString().toByteArray())
                    addFinding(Finding(id, f.cameraId, d.score, f.captureMs, thumb(crop), saved = true))
                    crop.recycle()
                }
            } else {
                dets.forEachIndexed { i, d ->
                    val crop = crop(bmp, d)
                    addFinding(Finding(obsIds[i], f.cameraId, d.score, f.captureMs, thumb(crop), saved = false))
                    crop.recycle()
                }
            }
            showInferred(f.cameraId, bmp, dets)
        }

        storage.appendLine(log, buildJsonObject {
            put("captured_at", Time.rfc3339(f.captureMs))
            put("camera_id", f.cameraId)
            put("seq", f.seq)
            put("stream_latency_ms", f.arrivedMs - f.captureMs)
            put("inference_ms", inferMs)
            put("frame_px", "${bmp.width}x${bmp.height}")
            if (fix != null) putJsonObject("gnss") {
                put("lat", fix.lat); put("lon", fix.lon); put("accuracy_m", fix.accuracyM)
                fix.speedMs?.let { put("speed_ms", it) }
            }
            put("odometer_m", location.odometerM)
            put("dataset_frame", capture)
            putJsonArray("detections") {
                dets.forEachIndexed { i, d -> addJsonObject {
                    put("observation_id", obsIds[i])
                    put("confidence", d.score)
                    putJsonArray("bbox_px") { add(d.x1); add(d.y1); add(d.x2); add(d.y2) }
                } }
            }
            put("record", saved)
        }.toString())
        bmp.recycle()

        _state.update {
            it.copy(
                inferences = it.inferences + 1,
                potholes = it.potholes + dets.size,
                recordsWritten = it.recordsWritten + if (saved == "saved") dets.size else 0,
                inferenceFps = inferFps.fps(now),
                lastInferenceMs = inferMs,
                notice = when (saved) {
                    "no_gnss_fix" -> "No GPS fix — potholes are logged but not saved as records until there is a position"
                    "saved" -> null
                    else -> it.notice
                },
            )
        }
    }

    private suspend fun telemetryLoop() {
        while (currentCoroutineContext().isActive) {
            delay(30_000)
            val fix = location.latest.value ?: continue
            val now = System.currentTimeMillis()
            if (now - fix.wallMs > 10_000) continue
            val det = detector ?: continue
            val msg = Messages.telemetry(
                Messages.Identity(prefs.deviceId, prefs.busId, prefs.routeId), now, fix,
                uptimeS = (now - startedMs) / 1000,
                queueDepth = storage.counts.value["pothole"] ?: 0,
                queueOldestS = 0,
                inferenceFps = inferFps.fps(now),
                camerasOnline = server.cameraIds().toList(),
                cameraQuality = emptyMap(),
                model = modelInfo(det),
            )
            runCatching { storage.writeRecord("telemetry", "${Time.compact(now)}.json", msg.toString().toByteArray()) }
        }
    }

    // ── previews for the UI ─────────────────────────────────────────────────

    private val holdUntil = HashMap<String, Long>()
    private val previewSeq = HashMap<String, Long>()

    private suspend fun previewLoop() {
        while (scope.isActive) {
            val now = System.currentTimeMillis()
            val ids = server.cameraIds()
            val next = _previews.value.filterKeys { it in ids }.toMutableMap()
            for (cam in ids) {
                if ((holdUntil[cam] ?: 0) > now) continue
                val f = server.latest(cam) ?: continue
                if (previewSeq[cam] == f.seq) continue
                previewSeq[cam] = f.seq
                val opts = BitmapFactory.Options().apply { inSampleSize = if (f.width > 960) 2 else 1 }
                val b = BitmapFactory.decodeByteArray(f.jpeg, 0, f.jpeg.size, opts) ?: continue
                next[cam] = TilePreview(b, emptyList(), now, inferred = false)
            }
            _previews.value = next
            delay(350)
        }
    }

    private fun showInferred(cam: String, frame: Bitmap, dets: List<Detection>) {
        val s = 640f / maxOf(frame.width, frame.height)
        val small = Bitmap.createScaledBitmap(frame, (frame.width * s).toInt(), (frame.height * s).toInt(), true)
        val scaled = dets.map { it.copy(x1 = it.x1 * s, y1 = it.y1 * s, x2 = it.x2 * s, y2 = it.y2 * s) }
        holdUntil[cam] = System.currentTimeMillis() + 1500
        _previews.update { it + (cam to TilePreview(small, scaled, System.currentTimeMillis(), inferred = true)) }
    }

    private fun addFinding(f: Finding) = _findings.update { (listOf(f) + it).take(24) }

    // ── status for the API + session file ───────────────────────────────────

    fun statusJson(): JsonObject = buildJsonObject {
        val s = _state.value
        put("device_id", prefs.deviceId)
        if (prefs.busId.isNotBlank()) put("bus_id", prefs.busId)
        if (prefs.routeId.isNotBlank()) put("route_id", prefs.routeId)
        put("app_version", BuildConfig.VERSION_NAME)
        put("session_running", s.running)
        s.sessionId?.let { put("session_id", it) }
        putJsonObject("records") { storage.counts.value.forEach { (c, n) -> put(c, n) } }
        put("dataset_capture", dataset.active)
        put("potholes_this_session", s.potholes)
        putJsonArray("cameras") {
            server.links.value.values.forEach { l -> addJsonObject {
                put("camera_id", l.cameraId); put("name", l.name); put("fps", l.fps)
                put("latency_ms", l.latencyMs); put("clock_offset_ms", l.offsetMs); put("test_source", l.isTestSource)
            } }
        }
        location.latest.value?.let { fix -> putJsonObject("gnss") {
            put("lat", fix.lat); put("lon", fix.lon); put("accuracy_m", fix.accuracyM)
            put("age_s", (System.currentTimeMillis() - fix.wallMs) / 1000)
        } }
    }

    private fun sessionJson(id: String, now: Long) = buildJsonObject {
        put("session_id", id)
        put("started_at", Time.rfc3339(now))
        put("device_id", prefs.deviceId)
        put("bus_id", prefs.busId)
        put("route_id", prefs.routeId)
        put("app_version", BuildConfig.VERSION_NAME)
        put("gate_mode", prefs.gateMode.name.lowercase())
        put("confidence_threshold", prefs.confidenceThreshold)
        put("dataset_capture", prefs.captureEnabled)
        put("dataset_spacing_m", prefs.captureSpacingM)
        detector?.let { d -> putJsonObject("model") { put("name", d.modelName); put("version", d.modelVersion); put("runtime", d.runtime) } }
    }

    private fun modelInfo(d: PotholeDetector) =
        Messages.ModelInfo(d.modelName, d.modelVersion, d.runtime, "${d.inputSize}x${d.inputSize}")

    // ── bitmap helpers ──────────────────────────────────────────────────────

    private fun jpeg(b: Bitmap, q: Int): ByteArray =
        ByteArrayOutputStream().also { b.compress(Bitmap.CompressFormat.JPEG, q, it) }.toByteArray()

    private fun crop(src: Bitmap, d: Detection): Bitmap {
        val padX = maxOf(d.width * 0.25f, 16f)
        val padY = maxOf(d.height * 0.25f, 16f)
        val l = (d.x1 - padX).toInt().coerceIn(0, src.width - 1)
        val t = (d.y1 - padY).toInt().coerceIn(0, src.height - 1)
        val r = (d.x2 + padX).toInt().coerceIn(l + 1, src.width)
        val b = (d.y2 + padY).toInt().coerceIn(t + 1, src.height)
        return Bitmap.createBitmap(src, l, t, r - l, b - t)
    }

    private fun thumb(b: Bitmap): Bitmap {
        val s = 240f / maxOf(b.width, b.height)
        return if (s >= 1f) b.copy(Bitmap.Config.ARGB_8888, false)
        else Bitmap.createScaledBitmap(b, (b.width * s).toInt().coerceAtLeast(1), (b.height * s).toInt().coerceAtLeast(1), true)
    }

    private fun annotate(src: Bitmap, dets: List<Detection>): Bitmap {
        val out = src.copy(Bitmap.Config.ARGB_8888, true)
        val c = Canvas(out)
        val stroke = maxOf(3f, out.width / 320f)
        val box = Paint().apply { style = Paint.Style.STROKE; strokeWidth = stroke; color = Color.rgb(251, 191, 36); isAntiAlias = true }
        val label = Paint().apply { color = Color.rgb(251, 191, 36); textSize = stroke * 6; isAntiAlias = true; isFakeBoldText = true }
        for (d in dets) {
            c.drawRect(d.x1, d.y1, d.x2, d.y2, box)
            c.drawText("pothole ${(d.score * 100).toInt()}%", d.x1, (d.y1 - stroke * 2).coerceAtLeast(label.textSize), label)
        }
        return out
    }

    /**
     * Drops boxes over flat regions — black letterbox bars, blown-out sky, a lens cap. A pothole
     * has texture; a detector firing on a uniform patch is firing on nothing.
     */
    private fun plausible(b: Bitmap, d: Detection): Boolean {
        val n = 16
        var sum = 0.0; var sq = 0.0
        for (i in 0 until n) for (j in 0 until n) {
            val x = (d.x1 + (d.width * (i + 0.5f) / n)).toInt().coerceIn(0, b.width - 1)
            val y = (d.y1 + (d.height * (j + 0.5f) / n)).toInt().coerceIn(0, b.height - 1)
            val p = b.getPixel(x, y)
            val l = 0.299 * ((p shr 16) and 0xFF) + 0.587 * ((p shr 8) and 0xFF) + 0.114 * (p and 0xFF)
            sum += l; sq += l * l
        }
        val mean = sum / (n * n)
        val std = kotlin.math.sqrt((sq / (n * n) - mean * mean).coerceAtLeast(0.0))
        return mean > 12 && std > 6
    }

    private fun meanLuma(b: Bitmap): Float {
        val s = Bitmap.createScaledBitmap(b, 32, 32, true)
        val px = IntArray(32 * 32)
        s.getPixels(px, 0, 32, 0, 0, 32, 32)
        s.recycle()
        return px.map { p -> 0.299f * ((p shr 16) and 0xFF) + 0.587f * ((p shr 8) and 0xFF) + 0.114f * (p and 0xFF) }.average().toFloat()
    }

    private companion object { const val TAG = "ArgusEngine" }
}
