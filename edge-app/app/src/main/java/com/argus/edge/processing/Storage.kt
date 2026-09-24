package com.argus.edge.processing

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.io.File
import java.io.FileOutputStream

/**
 * What the processing client keeps (docs/03, docs/04). Root is app-specific external storage —
 * /sdcard/Android/data/com.argus.edge/files/argus — so no storage permission, and `adb pull` works.
 *
 *   processed/                 findings, one subfolder per category
 *     pothole/                   <utc>-<id>.json   contract Observation (class_id pothole)
 *                                <utc>-<id>.jpg    its evidence crop
 *                                <utc>-<camera>-frame.jpg  the full frame, boxes drawn, for review
 *     damaged_road/              same, class_id damaged_road + subclass (crack type)
 *     traffic_counting/          <utc>-<camera>.json  unique vehicle/pedestrian counts per 30 s
 *     telemetry/                 <utc>.json        contract Telemetry, every 30 s
 *     log/                       <session>.jsonl   one line per inference, every camera
 *   dataset/<session>/         clean frames for training (DatasetRecorder)
 *
 * The backend reads processed/<category>/ over the local API and does NOT delete: the phone
 * keeps its record. Categories are added as their models land (incidents, …).
 */
class Storage(context: Context) {
    val root: File = File(context.getExternalFilesDir(null) ?: context.filesDir, "argus")
    val processed = File(root, "processed")
    val datasetRoot = File(root, "dataset")
    private val tmp = File(root, ".tmp")

    private val _counts = MutableStateFlow<Map<String, Int>>(emptyMap())
    /** JSON records per category. */
    val counts: StateFlow<Map<String, Int>> = _counts.asStateFlow()

    init {
        CATEGORIES.forEach { category(it) }
        File(processed, "log").mkdirs()
        datasetRoot.mkdirs(); tmp.mkdirs()
        tmp.listFiles()?.forEach { it.delete() }
        refreshCounts()
    }

    fun category(name: String): File = File(processed, name).apply { mkdirs() }

    fun logFile(sessionId: String) = File(processed, "log/$sessionId.jsonl")

    fun appendLine(file: File, line: String) {
        file.parentFile?.mkdirs()
        FileOutputStream(file, true).use { it.write((line + "\n").toByteArray(Charsets.UTF_8)) }
    }

    fun write(file: File, bytes: ByteArray) {
        file.parentFile?.mkdirs()
        file.writeBytes(bytes)
    }

    /**
     * Written to .tmp then renamed, so the API never lists a half-written file. For a pothole,
     * write the JPEGs FIRST: a reader that sees the JSON can rely on its evidence being there.
     */
    fun writeRecord(category: String, name: String, bytes: ByteArray) {
        val t = File(tmp, "$name.part")
        t.writeBytes(bytes)
        if (!t.renameTo(File(category(category), name))) { t.delete(); error("rename failed for $name") }
        if (name.endsWith(".json")) refreshCounts()
    }

    /** Files of a category in name order (= capture order), optionally only those after a cursor. */
    fun list(category: String, after: String? = null, limit: Int = Int.MAX_VALUE): List<File> {
        if (category !in CATEGORIES) return emptyList()
        return (File(processed, category).listFiles { f -> f.isFile && NAME.matches(f.name) } ?: emptyArray())
            .sortedBy { it.name }
            .let { all -> if (after == null) all else all.filter { it.name > after } }
            .take(limit)
    }

    fun file(category: String, name: String): File? {
        if (category !in CATEGORIES || !NAME.matches(name)) return null
        val dir = File(processed, category)
        val f = File(dir, name)
        return if (f.isFile && f.parentFile?.canonicalPath == dir.canonicalPath) f else null
    }

    fun refreshCounts() {
        _counts.value = CATEGORIES.associateWith { c -> File(processed, c).listFiles { f -> f.name.endsWith(".json") }?.size ?: 0 }
    }

    fun freeBytes(): Long = root.usableSpace

    companion object {
        /** Only categories with a model behind them today. */
        val CATEGORIES = listOf("pothole", "damaged_road", "traffic_counting", "telemetry")
        val NAME = Regex("^[A-Za-z0-9._-]{1,128}$")
    }
}
