package com.argus.edge.processing

import android.annotation.SuppressLint
import android.content.Context
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Bundle
import android.os.Looper
import android.os.SystemClock
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.sin
import kotlin.math.sqrt

/** One GNSS fix, stamped on this phone's wall clock. */
data class Fix(
    val wallMs: Long,
    val lat: Double,
    val lon: Double,
    val accuracyM: Float,
    val speedMs: Float?,
    val bearingDeg: Float?,
)

/**
 * The processing client's own GNSS is the bus's position (docs/03). GPS provider only:
 * a network fix is off by tens to hundreds of metres, and a pothole placed on the wrong road
 * is worse than one not reported.
 */
class LocationTracker(context: Context) {
    private val lm = context.getSystemService(Context.LOCATION_SERVICE) as LocationManager
    private val history = ArrayDeque<Fix>()
    private var lastForOdo: Fix? = null

    private val _latest = MutableStateFlow<Fix?>(null)
    val latest: StateFlow<Fix?> = _latest.asStateFlow()

    /** Metres travelled since start. Drives the distance gate. */
    @Volatile var odometerM: Double = 0.0
        private set

    private val listener = object : LocationListener {
        override fun onLocationChanged(loc: Location) = onFix(loc)
        @Deprecated("Deprecated in Java") override fun onStatusChanged(p: String?, s: Int, e: Bundle?) {}
        override fun onProviderEnabled(provider: String) {}
        override fun onProviderDisabled(provider: String) {}
    }

    var running = false
        private set

    @SuppressLint("MissingPermission")
    fun start(): Boolean {
        if (running) return true
        if (!lm.isProviderEnabled(LocationManager.GPS_PROVIDER)) return false
        return runCatching {
            lm.requestLocationUpdates(LocationManager.GPS_PROVIDER, 1000L, 0f, listener, Looper.getMainLooper())
            running = true
        }.isSuccess
    }

    fun stop() {
        runCatching { lm.removeUpdates(listener) }
        running = false
    }

    private fun onFix(loc: Location) {
        val ageMs = (SystemClock.elapsedRealtimeNanos() - loc.elapsedRealtimeNanos) / 1_000_000L
        val fix0 = Fix(
            wallMs = System.currentTimeMillis() - ageMs,
            lat = loc.latitude, lon = loc.longitude,
            accuracyM = if (loc.hasAccuracy()) loc.accuracy else 50f,
            speedMs = if (loc.hasSpeed()) loc.speed else null,
            bearingDeg = if (loc.hasBearing()) loc.bearing else null,
        )
        var fix = fix0
        val prevFix = synchronized(history) { history.lastOrNull() }
        if (prevFix != null) {
            val dt = (fix.wallMs - prevFix.wallMs) / 1000.0
            val d = haversineM(prevFix.lat, prevFix.lon, fix.lat, fix.lon)
            // A jump no bus can make (> ~150 km/h) is a multipath glitch, not travel. Drop it.
            if (dt > 0 && d / dt > MAX_SPEED_MS && d > 3 * fix.accuracyM) return
            if (dt in 0.2..5.0) {
                val derived = (d / dt).toFloat()
                val reported = fix.speedMs
                fix = fix.copy(
                    // Some chips (and emulators) report 0 while clearly moving; trust displacement then.
                    speedMs = if (reported == null || (reported < 0.5f && derived > 2f)) derived else reported,
                    bearingDeg = fix.bearingDeg ?: if (d > 0.5) bearingDeg(prevFix.lat, prevFix.lon, fix.lat, fix.lon) else prevFix.bearingDeg,
                )
            }
        }
        synchronized(history) {
            history.addLast(fix)
            while (history.size > 30) history.removeFirst()
        }
        // Ignore stationary jitter: a parked bus drifting 3 m in GNSS noise has not surveyed 3 m of road.
        val prev = lastForOdo
        if (prev == null) lastForOdo = fix
        else {
            val d = haversineM(prev.lat, prev.lon, fix.lat, fix.lon)
            val moving = (fix.speedMs ?: 0f) > 0.8f || d > 2 * fix.accuracyM
            if (moving && fix.accuracyM < 50f) { odometerM += d; lastForOdo = fix }
        }
        _latest.value = fix
    }

    /**
     * Bus position at [wallMs], linearly interpolated between the bracketing fixes. Falls back
     * to the newest fix if it is under 3 s old; null if the position is stale or unknown.
     */
    fun at(wallMs: Long): Fix? = synchronized(history) {
        if (history.isEmpty()) return null
        val after = history.firstOrNull { it.wallMs >= wallMs }
        val before = history.lastOrNull { it.wallMs <= wallMs }
        when {
            before != null && after != null && after.wallMs > before.wallMs -> {
                val t = (wallMs - before.wallMs).toDouble() / (after.wallMs - before.wallMs)
                before.copy(
                    wallMs = wallMs,
                    lat = before.lat + (after.lat - before.lat) * t,
                    lon = before.lon + (after.lon - before.lon) * t,
                    accuracyM = maxOf(before.accuracyM, after.accuracyM),
                )
            }
            before != null && wallMs - before.wallMs < 3000 -> before
            after != null && after.wallMs - wallMs < 1000 -> after
            else -> null
        }
    }

    companion object {
        const val MAX_SPEED_MS = 42.0

        fun bearingDeg(lat1: Double, lon1: Double, lat2: Double, lon2: Double): Float {
            val p1 = Math.toRadians(lat1); val p2 = Math.toRadians(lat2); val dl = Math.toRadians(lon2 - lon1)
            val y = sin(dl) * cos(p2)
            val x = cos(p1) * sin(p2) - sin(p1) * cos(p2) * cos(dl)
            return ((Math.toDegrees(atan2(y, x)) + 360.0) % 360.0).toFloat()
        }

        fun haversineM(lat1: Double, lon1: Double, lat2: Double, lon2: Double): Double {
            val r = 6_371_000.0
            val dLat = Math.toRadians(lat2 - lat1)
            val dLon = Math.toRadians(lon2 - lon1)
            val a = sin(dLat / 2) * sin(dLat / 2) +
                cos(Math.toRadians(lat1)) * cos(Math.toRadians(lat2)) * sin(dLon / 2) * sin(dLon / 2)
            return 2 * r * atan2(sqrt(a), sqrt(1 - a))
        }
    }
}
