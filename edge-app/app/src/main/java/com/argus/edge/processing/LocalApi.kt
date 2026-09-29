package com.argus.edge.processing

import android.util.Log
import com.argus.edge.core.Time
import fi.iki.elonen.NanoHTTPD
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.addJsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.putJsonObject
import java.io.FileInputStream

/**
 * Read-only hand-off from the processing client to the backend (docs/04 "Consuming the
 * processing client"). Served on the local network; every /api call needs the token shown on
 * the phone, as `Authorization: Bearer <token>`. Nothing here deletes: the phone keeps its record,
 * and the backend remembers how far it has read with the `after` cursor.
 *
 *   GET /api/v1/status                                  device, session, cameras, counts
 *   GET /api/v1/processed                               categories and how many records each holds
 *   GET /api/v1/processed/{category}?after=&limit=      files, oldest first, after a cursor
 *   GET /api/v1/processed/{category}/{name}             one file (JSON or JPEG)
 */
class LocalApi(
    port: Int,
    private val storage: Storage,
    private val token: () -> String,
    private val status: () -> JsonObject,
) : NanoHTTPD(port) {

    fun startSafely(): Boolean = runCatching { start(SOCKET_READ_TIMEOUT, false); true }
        .onFailure { Log.e("ArgusLocalApi", "cannot start", it) }.getOrDefault(false)

    override fun serve(session: IHTTPSession): Response {
        val uri = session.uri.trimEnd('/')
        if (uri == "" || uri == "/") return text(Response.Status.OK, "ARGUS edge local API. See /api/v1/status (token required).")
        if (!uri.startsWith("/api/v1")) return error(Response.Status.NOT_FOUND, "not found")
        if (!authorised(session)) return error(Response.Status.UNAUTHORIZED, "missing or wrong token")
        if (session.method != Method.GET) return error(Response.Status.METHOD_NOT_ALLOWED, "read-only API: GET only")

        val parts = uri.removePrefix("/api/v1").trim('/').split('/')
        return when {
            parts == listOf("status") -> json(Response.Status.OK, status())
            parts == listOf("processed") -> json(Response.Status.OK, categories())
            parts.size == 2 && parts[0] == "processed" -> {
                if (parts[1] !in Storage.CATEGORIES) return error(Response.Status.NOT_FOUND, "no such category")
                val after = session.parameters["after"]?.firstOrNull()?.takeIf { it.isNotBlank() }
                val limit = session.parameters["limit"]?.firstOrNull()?.toIntOrNull()?.coerceIn(1, 5000) ?: 500
                json(Response.Status.OK, listing(parts[1], after, limit))
            }
            parts.size == 3 && parts[0] == "processed" -> {
                val f = storage.file(parts[1], parts[2]) ?: return error(Response.Status.NOT_FOUND, "no such file")
                val mime = if (f.name.endsWith(".jpg")) "image/jpeg" else "application/json"
                newFixedLengthResponse(Response.Status.OK, mime, FileInputStream(f), f.length())
            }
            else -> error(Response.Status.NOT_FOUND, "not found")
        }.also { it.addHeader("Cache-Control", "no-store") }
    }

    private fun authorised(s: IHTTPSession): Boolean {
        val header = s.headers["authorization"]?.removePrefix("Bearer ")?.trim()
        val query = s.parameters["token"]?.firstOrNull()
        return (header ?: query) == token()
    }

    private fun categories(): JsonObject = buildJsonObject {
        putJsonObject("categories") {
            storage.counts.value.forEach { (c, n) -> putJsonObject(c) { put("records", n); put("url", "/api/v1/processed/$c") } }
        }
    }

    private fun listing(category: String, after: String?, limit: Int): JsonObject = buildJsonObject {
        val files = storage.list(category, after, limit)
        put("category", category)
        put("count", files.size)
        files.lastOrNull()?.let { put("next_after", it.name) }
        putJsonArray("files") {
            for (f in files) addJsonObject {
                put("name", f.name)
                put("type", when {
                    f.name.endsWith("-frame.jpg") -> "frame"
                    f.name.endsWith(".jpg") -> "evidence"
                    category == "telemetry" -> "telemetry"
                    category == "traffic_counting" -> "traffic_window"
                    else -> "observation"
                })
                put("bytes", f.length())
                put("modified_at", Time.rfc3339(f.lastModified()))
                put("url", "/api/v1/processed/$category/${f.name}")
            }
        }
    }

    private fun json(status: Response.Status, body: JsonObject) =
        newFixedLengthResponse(status, "application/json", body.toString())

    private fun text(status: Response.Status, body: String) = newFixedLengthResponse(status, MIME_PLAINTEXT, body)

    private fun error(status: Response.Status, msg: String) =
        json(status, buildJsonObject { put("error", msg) })
}
