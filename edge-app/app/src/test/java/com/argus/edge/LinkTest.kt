package com.argus.edge

import com.argus.edge.core.GateMode
import com.argus.edge.link.ClockSync
import com.argus.edge.link.Protocol
import com.argus.edge.link.StreamConfig
import com.argus.edge.processing.FrameGate
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import kotlinx.serialization.json.put
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream

class LinkTest {
    @Test fun frameRoundTripsThroughTheWire() {
        val jpeg = ByteArray(5000) { (it % 251).toByte() }
        val header = buildJsonObject { put("seq", 42); put("capture_ms", 1_700_000_000_123L); put("offset_ms", -17); put("w", 1280); put("h", 720) }
        val buf = ByteArrayOutputStream()
        Protocol.write(DataOutputStream(buf), Protocol.FRAME, Protocol.encodeFrame(header, jpeg))

        val m = Protocol.read(DataInputStream(ByteArrayInputStream(buf.toByteArray())))
        assertEquals(Protocol.FRAME, m.type)
        val (h, j) = Protocol.decodeFrame(m.payload)
        assertEquals(42L, h["seq"]!!.jsonPrimitive.long)
        assertEquals(-17L, h["offset_ms"]!!.jsonPrimitive.long)
        assertArrayEquals(jpeg, j)
    }

    @Test fun clockSyncPrefersLowestRttSample() {
        val c = ClockSync()
        // true offset +1000 ms; symmetric 10 ms path
        c.add(t0 = 0, t1 = 1005, t2 = 10)
        // congested sample, asymmetric: would suggest +1090
        c.add(t0 = 100, t1 = 1290, t2 = 300)
        assertEquals(1000L, c.offsetMs)
        assertEquals(10L, c.rttMs)
    }

    @Test fun distanceGateFiresEveryFiveMetresPerCamera() {
        val g = FrameGate()
        assertTrue(g.shouldInfer("front", GateMode.DISTANCE, 0.0, 0))
        assertFalse(g.shouldInfer("front", GateMode.DISTANCE, 3.0, 1000))
        assertTrue(g.shouldInfer("rear", GateMode.DISTANCE, 3.0, 1000))   // cameras are independent
        assertTrue(g.shouldInfer("front", GateMode.DISTANCE, 5.5, 2000))
        assertFalse(g.shouldInfer("front", GateMode.DISTANCE, 50.0, 2050)) // 10 Hz cap
    }

    @Test fun stoppedBusInfersNothingInDistanceMode() {
        val g = FrameGate()
        g.shouldInfer("front", GateMode.DISTANCE, 100.0, 0)
        repeat(20) { assertFalse(g.shouldInfer("front", GateMode.DISTANCE, 100.0, 1000L * (it + 1))) }
    }

    @Test fun timeGateIsForBenchTesting() {
        val g = FrameGate()
        assertTrue(g.shouldInfer("front", GateMode.TIME, 0.0, 0))
        assertFalse(g.shouldInfer("front", GateMode.TIME, 0.0, 300))
        assertTrue(g.shouldInfer("front", GateMode.TIME, 0.0, 600))
    }

    @Test fun streamConfigRoundTripsAndClampsGarbage() {
        assertEquals(StreamConfig.CAPTURE, StreamConfig.fromJson(StreamConfig.CAPTURE.toJson()))
        val bad = buildJsonObject { put("mode", "capture"); put("max_edge", 99999); put("jpeg_quality", 5); put("fps", 0) }
        val c = StreamConfig.fromJson(bad)
        assertEquals(3840, c.maxEdge); assertEquals(40, c.jpegQuality); assertEquals(1, c.fps)
    }
}
