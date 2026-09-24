package com.argus.edge.core

import java.time.Instant
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter

object Time {
    private val rfc3339 = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'").withZone(ZoneOffset.UTC)
    private val compact = DateTimeFormatter.ofPattern("yyyyMMdd'T'HHmmssSSS'Z'").withZone(ZoneOffset.UTC)
    private val session = DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss").withZone(ZoneOffset.UTC)

    /** Contract Timestamp: RFC3339 UTC, millisecond precision. */
    fun rfc3339(epochMs: Long): String = rfc3339.format(Instant.ofEpochMilli(epochMs))

    /** Sortable, filename-safe. Helpful files are named with it so listing order = capture order. */
    fun compact(epochMs: Long): String = compact.format(Instant.ofEpochMilli(epochMs))

    fun sessionId(epochMs: Long): String = session.format(Instant.ofEpochMilli(epochMs))
}
