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
import com.argus.edge.core.GateMode
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
    val cracks: Long = 0,
    val vehicles: Long = 0,
    val pedestrians: Long = 0,
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
class TilePreview(val bitmap: Bitmap, val detections: List<Labeled>, val atMs: Long, val inferred: Boolean)

class Finding(
    val id: String,
    val cameraId: String,
    val kind: Kind,
    val confidence: Float,
    val atMs: Long,
    val thumb: Bitmap,
    /** Saved to processed/<category>/ (false = no GPS fix, logged only). */
    val saved: Boolean,
)

/**
 * The processing client. Owns the frame server (cameras link to it), discovery, the GNSS
 * tracker, the local API, and — while a session runs — the loop that runs three prototype
 * models over every camera:
 *
 *   road defects   pothole + crack models, every 5 m        → processed/pothole/, damaged_road/
 *   traffic        vehicle/pedestrian model, 4 fps, tracked → processed/traffic_counting/
 *   dataset        clean frames every 10 m (optional)       → dataset/
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

    private var potholeModel: YoloDetector? = null
    private var damageModel: YoloDetector? = null
    private var trafficModel: YoloDetector? = null

    private val roadGate = FrameGate()
    private var trafficGate = FrameGate(intervalMs = 1000L / TRAFFIC_FPS)
    private var captureGate = FrameGate(metres = prefs.captureSpacingM.toDouble(), intervalMs = 1000)
    val dataset = DatasetRecorder(storage)
    private val inferFps = FpsCounter()

    private val trackers = HashMap<String, IouTracker>()
    private val windows = HashMap<String, TrafficWindow>()
    private val windowStartOdo = HashMap<String, Double>()

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
        roadGate.reset(); trafficGate.reset()
        captureGate = FrameGate(metres = prefs.captureSpacingM.toDouble(), intervalMs = 1000)
        trackers.clear(); windows.clear(); windowStartOdo.clear()
        _state.update {
            it.copy(running = true, sessionId = id, sessionStartMs = now, inferences = 0, potholes = 0, cracks = 0,
                vehicles = 0, pedestrians = 0, recordsWritten = 0, notice = null)
        }
        sessionJob = scope.launch(inferenceDispatcher) {
            if (!loadModels()) { _state.update { it.copy(running = false) }; return@launch }
            storage.appendLine(storage.logFile(id), buildJsonObject { put("session", sessionJson(id, now)) }.toString())
            if (prefs.captureEnabled) dataset.begin(id, sessionJson(id, now), prefs.routeId)
            launch(Dispatchers.IO) { telemetryLoop() }
            try {
                inferenceLoop(id)
            } finally {
                flushTraffic(force = true)
            }
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

    private fun loadModels(): Boolean = try {
        potholeModel = potholeModel ?: YoloDetector(context, Models.POTHOLE)
        damageModel = damageModel ?: YoloDetector(context, Models.ROAD_DAMAGE)
        trafficModel = trafficModel ?: YoloDetector(context, Models.TRAFFIC)
        _state.update { it.copy(modelReady = true, modelError = null) }
        true
    } catch (e: Throwable) {
        Log.e(TAG, "model load failed", e)
        _state.update { it.copy(modelReady = false, modelError = e.message ?: e.javaClass.simpleName) }
        false
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
                val odo = location.odometerM
                // Three independent gates. Road defects and dataset frames sample by distance;
                // traffic samples by time, because tracking needs continuity.
                val capture = dataset.active && captureGate.shouldInfer(cam, prefs.gateMode, odo, now)
                val road = (prefs.detectPotholes || prefs.detectCracks) && roadGate.shouldInfer(cam, prefs.gateMode, odo, now)
                val traffic = prefs.detectTraffic && trafficGate.shouldInfer(cam, GateMode.TIME, odo, now)
                if (!capture && !road && !traffic) continue
                lastSeq[cam] = f.seq
                worked = true
                runCatching { process(f, log, capture, road || capture, traffic) }.onFailure { Log.e(TAG, "process $cam", it) }
            }
            flushTraffic(force = false)
            if (!worked) delay(15)
        }
    }

    private fun process(f: ReceivedFrame, log: File, capture: Boolean, road: Boolean, traffic: Boolean) {
        val bmp = BitmapFactory.decodeByteArray(f.jpeg, 0, f.jpeg.size) ?: return
        val t0 = System.nanoTime()
        val conf = prefs.confidenceThreshold

        val roadDets = ArrayList<Labeled>()
        if (road) {
            if (prefs.detectPotholes) potholeModel?.let { m -> m.detect(bmp, conf).forEach { roadDets += Labeled(it, m.spec.keep.getValue(it.classId)) } }
            if (prefs.detectCracks) damageModel?.let { m ->
                m.detect(bmp, maxOf(conf, m.spec.defaultConfidence)).forEach { roadDets += Labeled(it, m.spec.keep.getValue(it.classId)) }
            }
        }
        roadDets.retainAll { plausible(bmp, it.det) }

        var trafficDets: List<Labeled> = emptyList()
        if (traffic) trafficModel?.let { m ->
            trafficDets = m.detect(bmp, conf).map { Labeled(it, m.spec.keep.getValue(it.classId)) }
            countTraffic(f.cameraId, f.captureMs, trafficDets)
        }

        val inferMs = (System.nanoTime() - t0) / 1_000_000
        val now = System.currentTimeMillis()
        inferFps.tick(now)

        val fix = location.at(f.captureMs)
        val who = Messages.Identity(prefs.deviceId, prefs.busId, prefs.routeId)
        val stamp = Time.compact(f.captureMs)
        val saved = if (roadDets.isEmpty()) "none" else if (fix == null) "no_gnss_fix" else "saved"
        val ids = roadDets.map { UUID.randomUUID().toString() }
        val illum = Messages.illumination(meanLuma(bmp))

        if (capture) dataset.record(f, fix, location.odometerM, illum, roadDets, listOfNotNull(potholeModel?.info, damageModel?.info))

        if (roadDets.isNotEmpty()) {
            // A contract Observation needs a position: without a GPS fix the detection is
            // logged (below) but no record is written.
            if (fix != null) {
                // One annotated frame per category per frame, however many boxes are on it.
                roadDets.map { it.kind.category }.distinct().forEach { category ->
                    val annotated = annotate(bmp, roadDets.filter { it.kind.category == category })
                    storage.writeRecord(category, "$stamp-${f.cameraId}-frame.jpg", jpeg(annotated, 85))
                    annotated.recycle()
                }
            }
            roadDets.forEachIndexed { i, l ->
                val id = ids[i]
                val crop = crop(bmp, l.det)
                if (fix != null) {
                    val base = "$stamp-$id"
                    val cropJpeg = jpeg(crop, 90)
                    storage.writeRecord(l.kind.category, "$base.jpg", cropJpeg)
                    val model = if (l.kind == Kind.POTHOLE) potholeModel!!.info else damageModel!!.info
                    val obs = Messages.observation(
                        id, who, f.cameraId,
                        classId = l.kind.category,
                        subclass = if (l.kind == Kind.POTHOLE) null else l.kind.name.lowercase(),
                        capturedAtMs = f.captureMs, confidence = l.det.score, fix = fix, bbox = l.det,
                        illumination = illum,
                        evidenceUri = "edge://${who.deviceId}/processed/${l.kind.category}/$base.jpg",
                        evidenceSha256 = Messages.sha256Hex(cropJpeg),
                        evidenceBytes = cropJpeg.size,
                        model = model,
                    )
                    storage.writeRecord(l.kind.category, "$base.json", obs.toString().toByteArray())
                }
                addFinding(Finding(id, f.cameraId, l.kind, l.det.score, f.captureMs, thumb(crop), saved = fix != null))
                crop.recycle()
            }
        }
        if (roadDets.isNotEmpty() || trafficDets.isNotEmpty()) showInferred(f.cameraId, bmp, roadDets + trafficDets, holdMs = if (roadDets.isNotEmpty()) 1500 else 300)

        storage.appendLine(log, buildJsonObject {
            put("captured_at", Time.rfc3339(f.captureMs))
            put("camera_id", f.cameraId)
            put("seq", f.seq)
            put("stream_latency_ms", f.arrivedMs - f.captureMs)
            put("inference_ms", inferMs)
            put("frame_px", "${bmp.width}x${bmp.height}")
            putJsonArray("ran") { if (road) add("road"); if (traffic) add("traffic"); if (capture) add("dataset") }
            if (fix != null) putJsonObject("gnss") {
                put("lat", fix.lat); put("lon", fix.lon); put("accuracy_m", fix.accuracyM)
                fix.speedMs?.let { put("speed_ms", it) }
            }
            put("odometer_m", location.odometerM)
            putJsonArray("detections") {
                roadDets.forEachIndexed { i, l -> addJsonObject {
                    put("id", ids[i]); put("kind", l.kind.name.lowercase()); put("confidence", l.det.score)
                    putJsonArray("bbox_px") { add(l.det.x1); add(l.det.y1); add(l.det.x2); add(l.det.y2) }
                } }
            }
            if (traffic) putJsonObject("traffic_in_frame") {
                trafficDets.groupingBy { it.kind }.eachCount().forEach { (k, n) -> put(k.name.lowercase(), n) }
            }
            put("record", saved)
        }.toString())
        bmp.recycle()

        _state.update {
            it.copy(
                inferences = it.inferences + 1,
                potholes = it.potholes + roadDets.count { d -> d.kind == Kind.POTHOLE },
                cracks = it.cracks + roadDets.count { d -> d.kind.category == "damaged_road" },
                recordsWritten = it.recordsWritten + if (saved == "saved") roadDets.size else 0,
                inferenceFps = inferFps.fps(now),
                lastInferenceMs = inferMs,
                notice = when (saved) {
                    "no_gnss_fix" -> "No GPS fix — road defects are logged but not saved as records until there is a position"
                    "saved" -> null
                    else -> it.notice
                },
            )
        }
    }

    // ── traffic counting ────────────────────────────────────────────────────

    private fun countTraffic(cam: String, atMs: Long, dets: List<Labeled>) {
        val tracker = trackers.getOrPut(cam) { IouTracker() }
        val window = windows.getOrPut(cam) { windowStartOdo[cam] = location.odometerM; TrafficWindow(cam, atMs) }
        val confirmed = tracker.update(dets)
        window.add(confirmed, tracker.visible())
        if (confirmed.isNotEmpty()) _state.update {
            it.copy(
                vehicles = it.vehicles + confirmed.count { t -> t.kind != Kind.PEDESTRIAN },
                pedestrians = it.pedestrians + confirmed.count { t -> t.kind == Kind.PEDESTRIAN },
            )
        }
    }

    /** Writes every window older than [TRAFFIC_WINDOW_MS] (or all, when the session ends). */
    private fun flushTraffic(force: Boolean) {
        val now = System.currentTimeMillis()
        val model = trafficModel?.info ?: return
        val who = Messages.Identity(prefs.deviceId, prefs.busId, prefs.routeId)
        for ((cam, w) in windows.toMap()) {
            if (!force && now - w.startMs < TRAFFIC_WINDOW_MS) continue
            windows.remove(cam)
            if (w.frames == 0) continue
            val msg = Messages.trafficWindow(
                who, w, now, location.at(w.startMs), location.at(now),
                distanceM = location.odometerM - (windowStartOdo[cam] ?: location.odometerM), model = model,
            )
            runCatching { storage.writeRecord("traffic_counting", "${Time.compact(w.startMs)}-$cam.json", msg.toString().toByteArray()) }
        }
    }

    private suspend fun telemetryLoop() {
        while (currentCoroutineContext().isActive) {
            delay(30_000)
            val fix = location.latest.value ?: continue
            val now = System.currentTimeMillis()
            if (now - fix.wallMs > 10_000) continue
            val model = potholeModel?.info ?: continue
            val msg = Messages.telemetry(
                Messages.Identity(prefs.deviceId, prefs.busId, prefs.routeId), now, fix,
                uptimeS = (now - startedMs) / 1000,
                queueDepth = storage.counts.value.values.sum(),
                queueOldestS = 0,
                inferenceFps = inferFps.fps(now),
                camerasOnline = server.cameraIds().toList(),
                cameraQuality = emptyMap(),
                model = model,
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

    private fun showInferred(cam: String, frame: Bitmap, dets: List<Labeled>, holdMs: Long) {
        val s = 640f / maxOf(frame.width, frame.height)
        val small = Bitmap.createScaledBitmap(frame, (frame.width * s).toInt(), (frame.height * s).toInt(), true)
        val scaled = dets.map { l -> l.copy(det = l.det.copy(x1 = l.det.x1 * s, y1 = l.det.y1 * s, x2 = l.det.x2 * s, y2 = l.det.y2 * s)) }
        holdUntil[cam] = System.currentTimeMillis() + holdMs
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
        putJsonObject("this_session") {
            put("potholes", s.potholes); put("cracks", s.cracks)
            put("vehicles_counted", s.vehicles); put("pedestrians_counted", s.pedestrians)
        }
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
        putJsonObject("detectors") {
            put("potholes", prefs.detectPotholes); put("cracks", prefs.detectCracks); put("traffic", prefs.detectTraffic)
        }
        putJsonArray("models") {
            listOf(Models.POTHOLE, Models.ROAD_DAMAGE, Models.TRAFFIC).forEach { m -> addJsonObject {
                put("name", m.name); put("version", m.version); put("asset", m.asset)
            } }
        }
    }

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

    private fun annotate(src: Bitmap, dets: List<Labeled>): Bitmap {
        val out = src.copy(Bitmap.Config.ARGB_8888, true)
        val c = Canvas(out)
        val stroke = maxOf(3f, out.width / 320f)
        for (l in dets) {
            val color = if (l.kind == Kind.POTHOLE) Color.rgb(251, 191, 36) else Color.rgb(244, 114, 182)
            val box = Paint().apply { style = Paint.Style.STROKE; strokeWidth = stroke; this.color = color; isAntiAlias = true }
            val label = Paint().apply { this.color = color; textSize = stroke * 6; isAntiAlias = true; isFakeBoldText = true }
            c.drawRect(l.det.x1, l.det.y1, l.det.x2, l.det.y2, box)
            c.drawText("${l.kind.label} ${(l.det.score * 100).toInt()}%", l.det.x1, (l.det.y1 - stroke * 2).coerceAtLeast(label.textSize), label)
        }
        return out
    }

    /**
     * Drops boxes over flat regions — black letterbox bars, blown-out sky, a lens cap. Road damage
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

    private companion object {
        const val TAG = "ArgusEngine"
        const val TRAFFIC_FPS = 4L
        const val TRAFFIC_WINDOW_MS = 30_000L
    }
}
