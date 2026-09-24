package com.argus.edge.link

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import java.io.DataInputStream
import java.io.DataOutputStream
import java.io.EOFException
import java.io.IOException

/**
 * Camera phone ⇄ processing client wire format, over one TCP connection per camera.
 *
 *   message := type:u8  length:u32 (big-endian)  payload[length]
 *
 *   HELLO   camera → proc   JSON {camera_id, name, app_version}
 *   WELCOME proc → camera   JSON {device_id, name}
 *   FRAME   camera → proc   header_len:u16  header JSON {seq, capture_ms, offset_ms, w, h}  JPEG bytes
 *   PING    camera → proc   JSON {t0}
 *   PONG    proc → camera   JSON {t0, t1}
 *   ACK     proc → camera   JSON {seq}   — one per FRAME received
 *   CONFIG  proc → camera   JSON {mode, max_edge, jpeg_quality, fps} — sent on link and on change
 *
 * capture_ms is the camera phone's wall clock at capture. offset_ms is the camera's current
 * estimate of (processing clock − camera clock) from PING/PONG, so the processing client
 * places the frame at capture_ms + offset_ms on ITS clock — the clock its GNSS is on.
 *
 * Flow control: a camera keeps at most MAX_IN_FLIGHT unacknowledged frames and drops older
 * ones while it waits. Without this, a busy processing client lets frames queue in TCP
 * buffers and latency grows to seconds; with it, a slow receiver just gets fewer, fresh frames.
 *
 * MVP transport is JPEG frames rather than H.264/RTSP: simpler, no codec negotiation, and
 * ~10 fps of 720p JPEG is well inside local Wi-Fi capacity. See docs/03.
 */
object Protocol {
    const val HELLO: Int = 1
    const val WELCOME: Int = 2
    const val FRAME: Int = 3
    const val PING: Int = 4
    const val PONG: Int = 5
    const val ACK: Int = 6
    const val CONFIG: Int = 7

    /** Frames a camera may have in flight before it waits for an ACK. Bounds stream latency. */
    const val MAX_IN_FLIGHT = 2

    const val MAX_PAYLOAD = 8 * 1024 * 1024
    const val SERVICE_TYPE = "_argus._tcp."

    val json = Json { ignoreUnknownKeys = true }

    class Message(val type: Int, val payload: ByteArray)

    fun write(out: DataOutputStream, type: Int, payload: ByteArray) {
        synchronized(out) {
            out.writeByte(type)
            out.writeInt(payload.size)
            out.write(payload)
            out.flush()
        }
    }

    fun writeJson(out: DataOutputStream, type: Int, obj: JsonObject) =
        write(out, type, obj.toString().toByteArray(Charsets.UTF_8))

    fun read(input: DataInputStream): Message {
        val type = input.read()
        if (type < 0) throw EOFException()
        val len = input.readInt()
        if (len < 0 || len > MAX_PAYLOAD) throw IOException("bad payload length $len")
        val buf = ByteArray(len)
        input.readFully(buf)
        return Message(type, buf)
    }

    fun parseJson(payload: ByteArray): JsonObject =
        json.parseToJsonElement(payload.toString(Charsets.UTF_8)).jsonObject

    fun encodeFrame(header: JsonObject, jpeg: ByteArray): ByteArray {
        val h = header.toString().toByteArray(Charsets.UTF_8)
        require(h.size <= 0xFFFF)
        val out = ByteArray(2 + h.size + jpeg.size)
        out[0] = (h.size ushr 8).toByte()
        out[1] = h.size.toByte()
        System.arraycopy(h, 0, out, 2, h.size)
        System.arraycopy(jpeg, 0, out, 2 + h.size, jpeg.size)
        return out
    }

    /** Returns (header, jpeg). */
    fun decodeFrame(payload: ByteArray): Pair<JsonObject, ByteArray> {
        val hLen = ((payload[0].toInt() and 0xFF) shl 8) or (payload[1].toInt() and 0xFF)
        val header = json.parseToJsonElement(String(payload, 2, hLen, Charsets.UTF_8)).jsonObject
        val jpeg = payload.copyOfRange(2 + hLen, payload.size)
        return header to jpeg
    }
}

/** What the processing client asks each camera to send. */
data class StreamConfig(val mode: String, val maxEdge: Int, val jpegQuality: Int, val fps: Int) {
    fun toJson() = kotlinx.serialization.json.buildJsonObject {
        put("mode", kotlinx.serialization.json.JsonPrimitive(mode))
        put("max_edge", kotlinx.serialization.json.JsonPrimitive(maxEdge))
        put("jpeg_quality", kotlinx.serialization.json.JsonPrimitive(jpegQuality))
        put("fps", kotlinx.serialization.json.JsonPrimitive(fps))
    }

    companion object {
        /** Live detection: 720p-class, light on the Wi-Fi. */
        val DETECT = StreamConfig("detect", 1280, 80, 10)
        /** Dataset capture: full 1080p, light compression. Only ~2 frames/s are kept at 10 m spacing. */
        val CAPTURE = StreamConfig("capture", 1920, 92, 4)

        fun fromJson(o: JsonObject): StreamConfig {
            fun i(k: String, d: Int) = (o[k] as? kotlinx.serialization.json.JsonPrimitive)?.content?.toIntOrNull() ?: d
            return StreamConfig(
                (o["mode"] as? kotlinx.serialization.json.JsonPrimitive)?.content ?: "detect",
                i("max_edge", 1280).coerceIn(320, 3840), i("jpeg_quality", 80).coerceIn(40, 100), i("fps", 10).coerceIn(1, 30),
            )
        }
    }
}

/**
 * NTP-style offset estimate. Keeps the lowest-RTT samples, because the one with the least
 * queueing delay is the one whose midpoint assumption is closest to true.
 */
class ClockSync(private val window: Int = 8) {
    private data class Sample(val rtt: Long, val offset: Long)
    private val samples = ArrayDeque<Sample>()

    /** t0 = camera send, t1 = processing receive, t2 = camera receive. Returns proc − camera. */
    @Synchronized
    fun add(t0: Long, t1: Long, t2: Long) {
        val rtt = t2 - t0
        if (rtt < 0) return
        samples.addLast(Sample(rtt, t1 - (t0 + t2) / 2))
        while (samples.size > window) samples.removeFirst()
    }

    @get:Synchronized
    val offsetMs: Long get() = samples.minByOrNull { it.rtt }?.offset ?: 0L

    @get:Synchronized
    val rttMs: Long get() = samples.minByOrNull { it.rtt }?.rtt ?: -1L

    @get:Synchronized
    val synced: Boolean get() = samples.isNotEmpty()
}
