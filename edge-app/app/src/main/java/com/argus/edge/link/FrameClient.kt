package com.argus.edge.link

import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import kotlinx.serialization.json.put
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.net.InetSocketAddress
import java.net.Socket
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicLong

sealed interface LinkState {
    data object Idle : LinkState
    data class Connecting(val host: String, val attempt: Int) : LinkState
    data class Streaming(
        val host: String,
        val processorName: String,
        val fps: Float,
        val rttMs: Long,
        val offsetMs: Long,
        val framesSent: Long,
        val kbps: Float,
    ) : LinkState
    data class Failed(val host: String, val reason: String) : LinkState
}

/**
 * Camera-role link to one processing client. Reconnects on its own until [unlink]: a camera
 * phone on a bus should heal from a Wi-Fi blip without anyone touching it.
 */
class FrameClient(private val appVersion: String) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var job: Job? = null
    private val frames = Channel<Pair<Long, ByteArrayWithSize>>(Channel.CONFLATED)
    private val clock = ClockSync()
    private val seq = AtomicLong(0)

    private val _state = MutableStateFlow<LinkState>(LinkState.Idle)
    val state: StateFlow<LinkState> = _state.asStateFlow()

    class ByteArrayWithSize(val jpeg: ByteArray, val w: Int, val h: Int)

    val isStreaming: Boolean get() = _state.value is LinkState.Streaming

    fun link(host: String, port: Int, cameraId: String, deviceName: String) {
        unlink()
        job = scope.launch {
            var attempt = 0
            while (isActive) {
                attempt++
                _state.value = LinkState.Connecting(host, attempt)
                val reason = runCatching { session(host, port, cameraId, deviceName) }
                    .exceptionOrNull()?.message ?: "closed"
                if (!isActive) break
                _state.value = LinkState.Failed(host, reason)
                delay(2000)
            }
        }
    }

    fun unlink() {
        job?.cancel()
        job = null
        _state.value = LinkState.Idle
    }

    /** Offer the newest encoded frame. Older unsent frames are dropped — latency beats completeness. */
    fun offer(captureMs: Long, jpeg: ByteArray, w: Int, h: Int) {
        if (isStreaming) frames.trySend(captureMs to ByteArrayWithSize(jpeg, w, h))
    }

    private suspend fun session(host: String, port: Int, cameraId: String, deviceName: String) {
        Socket().use { socket ->
            socket.tcpNoDelay = true
            socket.connect(InetSocketAddress(host, port), 4000)
            val input = DataInputStream(BufferedInputStream(socket.getInputStream()))
            val out = DataOutputStream(BufferedOutputStream(socket.getOutputStream(), 256 * 1024))

            Protocol.writeJson(out, Protocol.HELLO, buildJsonObject {
                put("camera_id", cameraId)
                put("name", deviceName)
                put("app_version", appVersion)
            })
            val welcome = withTimeout(5000) { Protocol.read(input) }
            if (welcome.type != Protocol.WELCOME) error("unexpected handshake")
            val processorName = Protocol.parseJson(welcome.payload)["name"]?.jsonPrimitive?.content ?: host

            val fps = FpsCounter()
            val inFlight = AtomicInteger(0)
            val lastAck = AtomicLong(System.currentTimeMillis())
            var sent = 0L
            var bytes = 0L
            val started = System.currentTimeMillis()

            // Reader: PONGs feed the clock estimate.
            val reader = scope.launch {
                runCatching {
                    while (isActive) {
                        val m = Protocol.read(input)
                        if (m.type == Protocol.ACK) {
                            inFlight.updateAndGet { (it - 1).coerceAtLeast(0) }
                            lastAck.set(System.currentTimeMillis())
                        } else if (m.type == Protocol.PONG) {
                            val t2 = System.currentTimeMillis()
                            val p = Protocol.parseJson(m.payload)
                            clock.add(p["t0"]!!.jsonPrimitive.long, p["t1"]!!.jsonPrimitive.long, t2)
                        }
                    }
                }
                runCatching { socket.close() }
            }
            // Pinger: fast at first to converge, then every 5 s to track drift.
            val pinger = scope.launch {
                var n = 0
                while (isActive) {
                    runCatching {
                        Protocol.writeJson(out, Protocol.PING, buildJsonObject { put("t0", System.currentTimeMillis()) })
                    }.onFailure { runCatching { socket.close() }; return@launch }
                    delay(if (n++ < 8) 250 else 5000)
                }
            }

            _state.value = LinkState.Streaming(host, processorName, 0f, -1, 0, 0, 0f)
            try {
                while (!socket.isClosed) {
                    // Wait for the receiver before taking a frame: the conflated channel keeps
                    // replacing it with the newest, so what we send next is always fresh.
                    while (inFlight.get() >= Protocol.MAX_IN_FLIGHT && !socket.isClosed) {
                        if (System.currentTimeMillis() - lastAck.get() > 10_000) { socket.close(); error("processing client stopped acknowledging") }
                        delay(4)
                    }
                    val (captureMs, f) = frames.receive()
                    val header = buildJsonObject {
                        put("seq", seq.incrementAndGet())
                        put("capture_ms", captureMs)
                        put("offset_ms", clock.offsetMs)
                        put("w", f.w)
                        put("h", f.h)
                    }
                    Protocol.write(out, Protocol.FRAME, Protocol.encodeFrame(header, f.jpeg))
                    inFlight.incrementAndGet()
                    val now = System.currentTimeMillis()
                    fps.tick(now)
                    sent++
                    bytes += f.jpeg.size
                    val secs = ((now - started) / 1000f).coerceAtLeast(1f)
                    _state.value = LinkState.Streaming(
                        host, processorName, fps.fps(now), clock.rttMs, clock.offsetMs, sent, bytes * 8f / 1000f / secs,
                    )
                }
            } finally {
                reader.cancel()
                pinger.cancel()
            }
            Log.i(TAG, "link to $host closed")
        }
    }

    private companion object { const val TAG = "ArgusFrameClient" }
}
