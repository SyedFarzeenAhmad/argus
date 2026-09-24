package com.argus.edge.processing

import com.argus.edge.core.Time
import com.argus.edge.link.ReceivedFrame
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.add
import kotlinx.serialization.json.addJsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.putJsonObject
import java.io.File

data class DatasetStats(
    val active: Boolean = false,
    val sessionDir: String? = null,
    val frames: Int = 0,
    val bytes: Long = 0,
    val perCamera: Map<String, Int> = emptyMap(),
    val freeBytes: Long = 0,
    val stoppedReason: String? = null,
)

/**
 * Training-data capture (docs/03 "Our own capture"): a clean frame from every camera every
 * N metres, WHETHER OR NOT a pothole was seen — a model trained only on frames where the
 * prototype fired never learns what an ordinary road, a tar patch or a shadow looks like.
 *
 *   dataset/<session>_<route>/
 *     session.json
 *     manifest.jsonl            one line per frame: time, camera, GNSS, speed, heading, conditions
 *     frames/<camera>/<utc>_<seq>.jpg      exactly as streamed (1080p), nothing drawn on it
 *     prelabels/<camera>/<utc>_<seq>.txt   the prototype's boxes, YOLO format, for CVAT import
 *
 * One folder per drive, tagged with the route, so the dataset splits BY ROUTE, never by frame.
 */
class DatasetRecorder(private val storage: Storage) {
    private var dir: File? = null
    private val _stats = MutableStateFlow(DatasetStats(freeBytes = storage.freeBytes()))
    val stats: StateFlow<DatasetStats> = _stats.asStateFlow()

    val active: Boolean get() = dir != null

    fun begin(sessionId: String, meta: JsonObject, routeId: String) {
        val tag = routeId.filter { it.isLetterOrDigit() || it == '-' }.take(16)
        val d = File(storage.datasetRoot, if (tag.isEmpty()) sessionId else "${sessionId}_$tag").apply { mkdirs() }
        File(d, "session.json").writeText(meta.toString())
        dir = d
        _stats.value = DatasetStats(active = true, sessionDir = d.absolutePath, freeBytes = storage.freeBytes())
    }

    fun end() {
        dir = null
        _stats.update { it.copy(active = false) }
    }

    /** Returns false (and stops) when the phone is nearly full, rather than filling it. */
    fun record(
        f: ReceivedFrame, fix: Fix?, odometerM: Double, illumination: String,
        suggestions: List<Detection>, model: Messages.ModelInfo,
    ): Boolean {
        val d = dir ?: return false
        val free = storage.freeBytes()
        if (free < MIN_FREE_BYTES) {
            end()
            _stats.update { it.copy(freeBytes = free, stoppedReason = "Stopped: less than 1 GB free on this phone") }
            return false
        }
        val stem = "${Time.compact(f.captureMs)}_${f.seq}"
        val rel = "frames/${f.cameraId}/$stem.jpg"
        storage.write(File(d, rel), f.jpeg)

        if (f.width > 0 && f.height > 0) {
            val yolo = suggestions.joinToString("\n") { s ->
                val cx = (s.x1 + s.x2) / 2 / f.width; val cy = (s.y1 + s.y2) / 2 / f.height
                "0 %.6f %.6f %.6f %.6f".format(cx, cy, s.width / f.width, s.height / f.height)
            }
            storage.write(File(d, "prelabels/${f.cameraId}/$stem.txt"), yolo.toByteArray())
        }

        storage.appendLine(File(d, "manifest.jsonl"), buildJsonObject {
            put("frame", rel)
            put("camera_id", f.cameraId)
            put("seq", f.seq)
            put("captured_at", Time.rfc3339(f.captureMs))
            put("width", f.width); put("height", f.height)
            put("bytes", f.jpeg.size)
            put("odometer_m", odometerM)
            if (fix != null) putJsonObject("gnss") {
                put("lat", fix.lat); put("lon", fix.lon); put("accuracy_m", fix.accuracyM)
                fix.speedMs?.let { put("speed_ms", it) }
                fix.bearingDeg?.let { put("heading_deg", it) }
            }
            put("illumination", illumination)
            putJsonArray("prelabels") {
                suggestions.forEach { s -> addJsonObject {
                    put("class", "pothole"); put("confidence", s.score)
                    putJsonArray("bbox_px") { add(s.x1); add(s.y1); add(s.x2); add(s.y2) }
                } }
            }
            putJsonObject("prelabel_model") { put("name", model.name); put("version", model.version) }
        }.toString())

        _stats.update {
            it.copy(
                frames = it.frames + 1,
                bytes = it.bytes + f.jpeg.size,
                perCamera = it.perCamera + (f.cameraId to ((it.perCamera[f.cameraId] ?: 0) + 1)),
                freeBytes = free,
            )
        }
        return true
    }

    private companion object { const val MIN_FREE_BYTES = 1L shl 30 }
}
