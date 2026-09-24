package com.argus.edge

import com.argus.edge.processing.Detection
import com.argus.edge.processing.Fix
import com.argus.edge.processing.Messages
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test
import java.io.File

/**
 * Writes sample messages to build/contract-samples/, which scripts/validate_contracts.py checks
 * against contracts/schemas/. Asserts the shape details here that the schema can't express.
 */
class MessagesTest {
    private val who = Messages.Identity("ARGUS-4F2A1C", "KA01FA4417", "500D")
    private val model = Messages.ModelInfo("pothole-yolo11n", "tahaUgan-2v", "onnxrt-cpu", "640x640")
    private val fix = Fix(1_790_000_000_000L, 12.9617, 77.6309, 4.2f, 4.8f, 118.4f)

    @Test fun observationMatchesContractShape() {
        val obs = Messages.observation(
            "0b9e7c7e-6a2f-4c7b-9a0e-3f1d2c4b5a69", who, "front", 1_790_000_000_123L, 0.8123f, fix,
            Detection(612f, 700f, 780f, 760f, 0.8123f, 0), "day",
            "edge://ARGUS-4F2A1C/helpful/x.jpg", "a".repeat(64), 46211, model,
        )
        assertEquals("2026-09-21T14:13:20.123Z", obs["captured_at"]!!.jsonPrimitive.content)
        assertEquals("gnss", obs["geo"]!!.jsonObject["source"]!!.jsonPrimitive.content)
        assertEquals("pothole", obs["class_id"]!!.jsonPrimitive.content)
        dump("observation.json", obs.toString())
    }

    @Test fun observationWithoutBusOmitsOptionalIds() {
        val obs = Messages.observation(
            "0b9e7c7e-6a2f-4c7b-9a0e-3f1d2c4b5a69", who.copy(busId = "", routeId = ""), "rear", 0, 0.5f,
            fix.copy(speedMs = null), Detection(0f, 0f, 1f, 1f, 0.5f, 0), "night", null, null, 0, model,
        )
        assertFalse("bus_id" in obs); assertFalse("ego" in obs); assertFalse("evidence" in obs)
        dump("observation-minimal.json", obs.toString())
    }

    @Test fun telemetryMatchesContractShape() {
        val t = Messages.telemetry(
            who, 1_790_000_000_000L, fix, 3600, 7, 12, 3.4f, listOf("front", "rear", "bogus"),
            mapOf("front" to 0.93f), model,
        )
        dump("telemetry.json", t.toString())
    }

    @Test fun illuminationBands() {
        assertEquals("day", Messages.illumination(140f))
        assertEquals("dusk", Messages.illumination(60f))
        assertEquals("day", Messages.illumination(80f))
        assertEquals("night", Messages.illumination(10f))
    }

    private fun dump(name: String, json: String) {
        val dir = File("build/contract-samples").apply { mkdirs() }
        File(dir, name).writeText(json)
    }
}
