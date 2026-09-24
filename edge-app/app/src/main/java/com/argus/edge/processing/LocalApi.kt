package com.argus.edge.processing

import android.util.Log
import com.argus.edge.core.Time
import fi.iki.elonen.NanoHTTPD
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.addJsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import java.io.FileInputStream

/**
 * The hand-off from the processing client to the backend (docs/04 "Consuming the edge
 * phone's helpful folder"). Served on the local network; every /api call needs the token
 * shown on the phone, as `Authorization: Bearer <token>`.
 *
 *   GET    /api/v1/status              device, session, cameras, pending count
 *   GET    /api/v1/helpful             list, oldest first
 *   GET    /api/v1/helpful/{name}      one file (JSON or JPEG)
 *   DELETE /api/v1/helpful/{name}      consumed → delete (an observation also deletes its JPEG)
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

        return when {
            uri == "/api/v1/status" && session.method == Method.GET -> json(Response.Status.OK, status())
            uri == "/api/v1/helpful" && session.method == Method.GET -> json(Response.Status.OK, listing())
            uri.startsWith("/api/v1/helpful/") -> {
                val name = uri.removePrefix("/api/v1/helpful/")
                when (session.method) {
                    Method.GET -> {
                        val f = storage.helpfulFile(name) ?: return error(Response.Status.NOT_FOUND, "no such file")
                        val mime = if (name.endsWith(".jpg")) "image/jpeg" else "application/json"
                        newFixedLengthResponse(Response.Status.OK, mime, FileInputStream(f), f.length())
                    }
                    Method.DELETE ->
                        if (storage.deleteHelpful(name)) newFixedLengthResponse(Response.Status.NO_CONTENT, MIME_PLAINTEXT, "")
                        else error(Response.Status.NOT_FOUND, "no such file")
                    else -> error(Response.Status.METHOD_NOT_ALLOWED, "GET or DELETE")
                }
            }
            else -> error(Response.Status.NOT_FOUND, "not found")
        }.also { it.addHeader("Cache-Control", "no-store") }
    }

    private fun authorised(s: IHTTPSession): Boolean {
        val header = s.headers["authorization"]?.removePrefix("Bearer ")?.trim()
        val query = s.parameters["token"]?.firstOrNull()
        return (header ?: query) == token()
    }

    private fun listing(): JsonObject = buildJsonObject {
        val files = storage.listHelpful()
        put("count", files.size)
        putJsonArray("files") {
            for (f in files) addJsonObject {
                put("name", f.name)
                put("type", when {
                    f.name.contains("-obs-") && f.name.endsWith(".json") -> "observation"
                    f.name.contains("-obs-") -> "evidence"
                    f.name.contains("-tlm") -> "telemetry"
                    else -> "other"
                })
                put("bytes", f.length())
                put("modified_at", Time.rfc3339(f.lastModified()))
                put("url", "/api/v1/helpful/${f.name}")
            }
        }
    }

    private fun json(status: Response.Status, body: JsonObject) =
        newFixedLengthResponse(status, "application/json", body.toString())

    private fun text(status: Response.Status, body: String) = newFixedLengthResponse(status, MIME_PLAINTEXT, body)

    private fun error(status: Response.Status, msg: String) =
        json(status, buildJsonObject { put("error", msg) })
}
