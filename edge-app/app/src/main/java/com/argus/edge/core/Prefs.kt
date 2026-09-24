package com.argus.edge.core

import android.content.Context
import java.security.SecureRandom

enum class Role { CAMERA, PROCESSOR }

enum class GateMode { DISTANCE, TIME }

/** Everything the phone remembers between launches. Small enough for SharedPreferences. */
class Prefs(context: Context) {
    private val sp = context.getSharedPreferences("argus", Context.MODE_PRIVATE)

    var role: Role?
        get() = sp.getString("role", null)?.let { runCatching { Role.valueOf(it) }.getOrNull() }
        set(v) = sp.edit().putString("role", v?.name).apply()

    /** Contract DeviceId: ^[A-Z0-9-]{4,32}$. One processing client = one device. */
    val deviceId: String
        get() = sp.getString("device_id", null) ?: ("ARGUS-" + randomToken(6, HEX_UPPER)).also {
            sp.edit().putString("device_id", it).apply()
        }

    fun setDeviceId(v: String) {
        val clean = v.uppercase().filter { it.isLetterOrDigit() || it == '-' }.take(32)
        if (clean.length >= 4) sp.edit().putString("device_id", clean).apply()
    }

    var busId: String
        get() = sp.getString("bus_id", "") ?: ""
        set(v) = sp.edit().putString("bus_id", v.trim()).apply()

    var routeId: String
        get() = sp.getString("route_id", "") ?: ""
        set(v) = sp.edit().putString("route_id", v.trim()).apply()

    /** Camera role: which camera_id this phone streams as. */
    var cameraId: String
        get() = sp.getString("camera_id", "front") ?: "front"
        set(v) = sp.edit().putString("camera_id", v).apply()

    /** Camera role: the last processing client it linked to, for one-tap relink. */
    var lastProcessorHost: String
        get() = sp.getString("last_host", "") ?: ""
        set(v) = sp.edit().putString("last_host", v).apply()

    var confidenceThreshold: Float
        get() = sp.getFloat("conf", 0.35f)
        set(v) = sp.edit().putFloat("conf", v.coerceIn(0.05f, 0.95f)).apply()

    var gateMode: GateMode
        get() = sp.getString("gate", GateMode.DISTANCE.name)?.let { runCatching { GateMode.valueOf(it) }.getOrNull() }
            ?: GateMode.DISTANCE
        set(v) = sp.edit().putString("gate", v.name).apply()

    /** Dataset capture: clean frames for training, every [captureSpacingM] metres. */
    var captureEnabled: Boolean
        get() = sp.getBoolean("capture", false)
        set(v) = sp.edit().putBoolean("capture", v).apply()

    var captureSpacingM: Int
        get() = sp.getInt("capture_spacing", 10)
        set(v) = sp.edit().putInt("capture_spacing", v.coerceIn(2, 100)).apply()

    /** Bearer token for the local processed-data API. Shown on screen; the backend consumer is configured with it. */
    val apiToken: String
        get() = sp.getString("api_token", null) ?: randomToken(20, ALNUM).also {
            sp.edit().putString("api_token", it).apply()
        }

    private companion object {
        const val HEX_UPPER = "0123456789ABCDEF"
        const val ALNUM = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        val rng = SecureRandom()
        fun randomToken(n: Int, alphabet: String) = buildString { repeat(n) { append(alphabet[rng.nextInt(alphabet.length)]) } }
    }
}
