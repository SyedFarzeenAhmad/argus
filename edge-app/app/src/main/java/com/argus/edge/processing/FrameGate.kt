package com.argus.edge.processing

import com.argus.edge.core.GateMode

/**
 * Sample by distance, not time (docs/03 frame gating): one inference per camera every 5 m of
 * travel, capped at 10 Hz. A stopped bus infers nothing. TIME mode (every 0.5 s) exists only
 * for bench testing without movement, and says so on screen.
 */
class FrameGate(private val metres: Double = 5.0, private val intervalMs: Long = 500, private val minGapMs: Long = 100) {
    private val lastOdo = HashMap<String, Double>()
    private val lastMs = HashMap<String, Long>()

    @Synchronized
    fun shouldInfer(cameraId: String, mode: GateMode, odometerM: Double, nowMs: Long): Boolean {
        val prevMs = lastMs[cameraId]
        if (prevMs != null && nowMs - prevMs < minGapMs) return false
        val fire = when (mode) {
            GateMode.TIME -> prevMs == null || nowMs - prevMs >= intervalMs
            GateMode.DISTANCE -> {
                val prev = lastOdo[cameraId]
                prev == null || odometerM - prev >= metres
            }
        }
        if (fire) { lastOdo[cameraId] = odometerM; lastMs[cameraId] = nowMs }
        return fire
    }

    @Synchronized
    fun reset() { lastOdo.clear(); lastMs.clear() }
}
