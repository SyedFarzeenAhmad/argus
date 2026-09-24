package com.argus.edge.link

import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.int
import kotlinx.serialization.json.long
import kotlinx.serialization.json.put
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.ConcurrentHashMap

/** A frame as received, placed on the processing client's clock. */
class ReceivedFrame(
    val cameraId: String,
    val seq: Long,
    /** Capture instant on the processing client's wall clock (camera clock + sync offset). */
    val captureMs: Long,
    val arrivedMs: Long,
    val width: Int,
    val height: Int,
    val jpeg: ByteArray,
)

data class CameraLink(
    val cameraId: String,
    val name: String,
    val address: String,
    val connectedAtMs: Long,
    val frames: Long = 0,
    val fps: Float = 0f,
    val offsetMs: Long = 0,
    /** arrival − capture, on the processing clock. The stream's end-to-end latency. */
    val latencyMs: Long = 0,
    val width: Int = 0,
    val height: Int = 0,
    val isTestSource: Boolean = false,
)

/**
 * Accepts camera phones on [port]. Holds only the LATEST frame per camera: inference is
 * gated and never needs a backlog, and a backlog on a phone is how you run out of memory.
 */
class FrameServer(
    private val port: Int,
    private val deviceId: () -> String,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var server: ServerSocket? = null
    private val sockets = ConcurrentHashMap<String, Socket>()
    private val latest = ConcurrentHashMap<String, ReceivedFrame>()
    private val counters = ConcurrentHashMap<String, FpsCounter>()

    private val _links = MutableStateFlow<Map<String, CameraLink>>(emptyMap())
    val links: StateFlow<Map<String, CameraLink>> = _links.asStateFlow()

    val isRunning: Boolean get() = server != null

    fun start() {
        if (server != null) return
        val ss = runCatching { ServerSocket(port).apply { reuseAddress = true } }
            .onFailure { Log.e(TAG, "cannot bind $port", it) }.getOrNull() ?: return
        server = ss
        scope.launch {
            while (isActive) {
                val s = runCatching { ss.accept() }.getOrNull() ?: break
                launch { serve(s) }
            }
        }
        scope.launch { publishLoop() }
    }

    fun stop() {
        runCatching { server?.close() }
        server = null
        sockets.values.forEach { runCatching { it.close() } }
        sockets.clear()
        latest.clear()
        _links.value = emptyMap()
        scope.coroutineContext[Job]?.children?.forEach { it.cancel() }
    }

    fun shutdown() { stop(); scope.cancel() }

    fun latest(cameraId: String): ReceivedFrame? = latest[cameraId]

    fun cameraIds(): Set<String> = _links.value.keys

    /** Test sources (a video file on this phone) feed frames through the same path as a camera. */
    fun registerTestSource(cameraId: String, name: String) {
        updateLink(cameraId) { CameraLink(cameraId, name, "this phone", System.currentTimeMillis(), isTestSource = true) }
    }

    fun unregisterTestSource(cameraId: String) {
        latest.remove(cameraId)
        _links.value = _links.value - cameraId
    }

    fun injectFrame(frame: ReceivedFrame) = accept(frame, offsetMs = 0)

    private fun serve(socket: Socket) {
        var cameraId: String? = null
        try {
            socket.tcpNoDelay = true
            val input = DataInputStream(BufferedInputStream(socket.getInputStream(), 256 * 1024))
            val out = DataOutputStream(BufferedOutputStream(socket.getOutputStream()))

            val hello = Protocol.read(input)
            if (hello.type != Protocol.HELLO) return
            val h = Protocol.parseJson(hello.payload)
            val id = h["camera_id"]?.jsonPrimitive?.content ?: return
            val name = h["name"]?.jsonPrimitive?.content ?: id
            cameraId = id

            // One phone per camera position. A re-link replaces the old connection.
            sockets.put(id, socket)?.let { runCatching { it.close() } }
            updateLink(id) { CameraLink(id, name, socket.inetAddress.hostAddress ?: "?", System.currentTimeMillis()) }

            Protocol.writeJson(out, Protocol.WELCOME, buildJsonObject {
                put("device_id", deviceId())
                put("name", deviceId())
            })

            while (true) {
                val m = Protocol.read(input)
                when (m.type) {
                    Protocol.PING -> {
                        val t1 = System.currentTimeMillis()
                        val t0 = Protocol.parseJson(m.payload)["t0"]!!.jsonPrimitive.long
                        Protocol.writeJson(out, Protocol.PONG, buildJsonObject { put("t0", t0); put("t1", t1) })
                    }
                    Protocol.FRAME -> {
                        val (hdr, jpeg) = Protocol.decodeFrame(m.payload)
                        Protocol.writeJson(out, Protocol.ACK, buildJsonObject { put("seq", hdr["seq"]!!.jsonPrimitive.long) })
                        val offset = hdr["offset_ms"]?.jsonPrimitive?.long ?: 0L
                        accept(
                            ReceivedFrame(
                                cameraId = id,
                                seq = hdr["seq"]!!.jsonPrimitive.long,
                                captureMs = hdr["capture_ms"]!!.jsonPrimitive.long + offset,
                                arrivedMs = System.currentTimeMillis(),
                                width = hdr["w"]?.jsonPrimitive?.int ?: 0,
                                height = hdr["h"]?.jsonPrimitive?.int ?: 0,
                                jpeg = jpeg,
                            ),
                            offset,
                        )
                    }
                }
            }
        } catch (e: Exception) {
            Log.i(TAG, "camera ${cameraId ?: "?"} disconnected: ${e.message}")
        } finally {
            runCatching { socket.close() }
            cameraId?.let { id ->
                // Only drop the link if this socket is still the current one for that camera.
                if (sockets.remove(id, socket)) {
                    latest.remove(id)
                    _links.value = _links.value - id
                }
            }
        }
    }

    private fun accept(frame: ReceivedFrame, offsetMs: Long) {
        latest[frame.cameraId] = frame
        val c = counters.getOrPut(frame.cameraId) { FpsCounter() }
        c.tick(frame.arrivedMs)
        updateLink(frame.cameraId) {
            it?.copy(
                frames = it.frames + 1,
                offsetMs = offsetMs,
                latencyMs = frame.arrivedMs - frame.captureMs,
                width = frame.width,
                height = frame.height,
            )
        }
    }

    // Per-frame updates are cheap map writes; the UI-facing flow gets fps folded in twice a second.
    private val pending = ConcurrentHashMap<String, CameraLink>()

    private fun updateLink(id: String, f: (CameraLink?) -> CameraLink?) {
        synchronized(pending) {
            val cur = pending[id] ?: _links.value[id]
            val next = f(cur) ?: return
            pending[id] = next
            if (cur == null) _links.value = _links.value + (id to next)
        }
    }

    private suspend fun publishLoop() {
        while (scope.isActive) {
            delay(500)
            synchronized(pending) {
                if (pending.isEmpty()) return@synchronized
                val now = System.currentTimeMillis()
                val merged = _links.value.toMutableMap()
                for ((id, link) in pending) {
                    if (id in merged) merged[id] = link.copy(fps = counters[id]?.fps(now) ?: 0f)
                }
                pending.clear()
                _links.value = merged
            }
        }
    }

    private companion object { const val TAG = "ArgusFrameServer" }
}

/** Frames per second over a sliding two-second window. */
class FpsCounter {
    private val stamps = ArrayDeque<Long>()
    @Synchronized fun tick(now: Long) { stamps.addLast(now); trim(now) }
    @Synchronized fun fps(now: Long): Float { trim(now); return stamps.size / 2f }
    private fun trim(now: Long) { while (stamps.isNotEmpty() && now - stamps.first() > 2000) stamps.removeFirst() }
}
