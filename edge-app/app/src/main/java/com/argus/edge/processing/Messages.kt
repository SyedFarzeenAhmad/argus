package com.argus.edge.processing

import com.argus.edge.core.Time
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.putJsonObject
import java.security.MessageDigest

/** Builds messages in the exact shape of contracts/schemas/. Pure Kotlin, so it is unit-tested. */
object Messages {
    const val SCHEMA_VERSION = "1.1.0"
    val CAMERA_IDS = listOf("front", "rear", "left", "right", "cabin")

    data class ModelInfo(val name: String, val version: String, val runtime: String, val inputRes: String)

    data class Identity(val deviceId: String, val busId: String, val routeId: String)

    fun observation(
        id: String,
        who: Identity,
        cameraId: String,
        classId: String,
        subclass: String?,
        capturedAtMs: Long,
        confidence: Float,
        fix: Fix,
        bbox: Detection,
        illumination: String,
        evidenceUri: String?,
        evidenceSha256: String?,
        evidenceBytes: Int,
        model: ModelInfo,
    ): JsonObject = buildJsonObject {
        put("schema_version", SCHEMA_VERSION)
        put("observation_id", id)
        put("device_id", who.deviceId)
        if (who.busId.isNotBlank()) put("bus_id", who.busId)
        if (who.routeId.isNotBlank()) put("route_id", who.routeId)
        put("camera_id", cameraId)
        put("class_id", classId)
        if (subclass != null) put("subclass", subclass)
        put("captured_at", Time.rfc3339(capturedAtMs))
        put("confidence", round(confidence.toDouble(), 4))
        // MVP: this is the BUS's position (source "gnss"). IPM, which places the pothole itself,
        // arrives with camera calibration — the source then becomes "gnss+ipm".
        putGeo(fix)
        putEgo(fix)
        putJsonObject("geometry") {
            putJsonArray("bbox_px") {
                add(round(bbox.x1.toDouble(), 1)); add(round(bbox.y1.toDouble(), 1))
                add(round(bbox.x2.toDouble(), 1)); add(round(bbox.y2.toDouble(), 1))
            }
        }
        putJsonObject("conditions") {
            put("illumination", illumination)
            put("weather", "unknown")
        }
        if (evidenceUri != null && evidenceSha256 != null) {
            putJsonObject("evidence") {
                put("uri", evidenceUri)
                put("sha256", evidenceSha256)
                put("bytes", evidenceBytes)
            }
        }
        putModel(model)
    }

    fun telemetry(
        who: Identity,
        atMs: Long,
        fix: Fix,
        uptimeS: Long,
        queueDepth: Int,
        queueOldestS: Long,
        inferenceFps: Float,
        camerasOnline: List<String>,
        cameraQuality: Map<String, Float>,
        model: ModelInfo,
    ): JsonObject = buildJsonObject {
        put("schema_version", SCHEMA_VERSION)
        put("device_id", who.deviceId)
        if (who.busId.isNotBlank()) put("bus_id", who.busId)
        if (who.routeId.isNotBlank()) put("route_id", who.routeId)
        put("at", Time.rfc3339(atMs))
        putGeo(fix)
        putEgo(fix)
        putJsonObject("health") {
            put("uptime_s", uptimeS)
            put("queue_depth", queueDepth)
            put("queue_oldest_s", queueOldestS)
            put("inference_fps", round(inferenceFps.toDouble(), 2))
            putJsonArray("cameras_online") { camerasOnline.filter { it in CAMERA_IDS }.forEach { add(it) } }
            putJsonObject("camera_quality") {
                cameraQuality.filterKeys { it in CAMERA_IDS }.forEach { (k, v) -> put(k, round(v.toDouble().coerceIn(0.0, 1.0), 3)) }
            }
            put("gnss_fix", if (fix.accuracyM < 20f) "3d" else "2d")
        }
        putModel(model)
    }

    /**
     * Vehicle and pedestrian counts for one camera over one window. INTERIM format, not a contract
     * message: the contract home for these counts is SegmentPass, which needs a road segment id —
     * i.e. map matching, not built yet. Keys match SegmentPass.traffic.vehicle_counts so the
     * backend's mapping is one-to-one when it lands.
     */
    fun trafficWindow(
        who: Identity, w: TrafficWindow, endMs: Long, start: Fix?, end: Fix?, distanceM: Double, model: ModelInfo,
    ): JsonObject = buildJsonObject {
        put("format", "argus.edge.traffic_window/0.1")
        put("device_id", who.deviceId)
        if (who.busId.isNotBlank()) put("bus_id", who.busId)
        if (who.routeId.isNotBlank()) put("route_id", who.routeId)
        put("camera_id", w.cameraId)
        put("started_at", Time.rfc3339(w.startMs))
        put("ended_at", Time.rfc3339(endMs))
        start?.let { putJsonObject("gnss_start") { put("lat", round(it.lat, 7)); put("lon", round(it.lon, 7)); put("accuracy_m", round(it.accuracyM.toDouble(), 1)) } }
        end?.let { putJsonObject("gnss_end") { put("lat", round(it.lat, 7)); put("lon", round(it.lon, 7)); put("accuracy_m", round(it.accuracyM.toDouble(), 1)) } }
        put("distance_m", round(distanceM, 1))
        val secs = (endMs - w.startMs) / 1000.0
        if (secs > 0) put("mean_speed_kmh", round(distanceM / secs * 3.6, 1))
        putJsonObject("vehicle_counts") {
            put("car", w.unique[Kind.CAR] ?: 0)
            put("two_wheeler", w.unique[Kind.TWO_WHEELER] ?: 0)
            put("auto_rickshaw", 0)  // not detectable with COCO weights — see Models.TRAFFIC
            put("bus", w.unique[Kind.BUS] ?: 0)
            put("truck", w.unique[Kind.TRUCK] ?: 0)
            put("bicycle", w.unique[Kind.BICYCLE] ?: 0)
        }
        putJsonObject("pedestrians") {
            put("unique_tracks", w.unique[Kind.PEDESTRIAN] ?: 0)
            put("max_simultaneous", w.maxPedestriansInFrame)
        }
        put("mean_vehicles_in_frame", round(w.meanVehiclesInFrame.toDouble(), 2))
        put("frames_processed", w.frames)
        putModel(model)
    }

    /** Mean luma → the contract's illumination enum. Crude, and honest about being crude. */
    fun illumination(meanLuma: Float): String = when {
        meanLuma >= 75f -> "day"
        meanLuma >= 45f -> "dusk"
        else -> "night"
    }

    fun sha256Hex(bytes: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }

    private fun kotlinx.serialization.json.JsonObjectBuilder.putGeo(fix: Fix) = putJsonObject("geo") {
        put("lat", round(fix.lat, 7))
        put("lon", round(fix.lon, 7))
        put("accuracy_m", round(fix.accuracyM.toDouble(), 1))
        put("source", "gnss")
    }

    private fun kotlinx.serialization.json.JsonObjectBuilder.putEgo(fix: Fix) {
        val speed = fix.speedMs ?: return
        val bearing = fix.bearingDeg ?: 0f
        putJsonObject("ego") {
            put("speed_kmh", round(speed * 3.6, 1).coerceAtLeast(0.0))
            put("heading_deg", round(bearing.toDouble(), 1).let { if (it >= 360.0) 0.0 else it })
        }
    }

    private fun kotlinx.serialization.json.JsonObjectBuilder.putModel(m: ModelInfo) = putJsonObject("model") {
        put("name", m.name)
        put("version", m.version)
        put("runtime", m.runtime)
        put("input_res", m.inputRes)
    }

    private fun round(v: Double, places: Int): Double {
        val f = Math.pow(10.0, places.toDouble())
        return Math.round(v * f) / f
    }
}
