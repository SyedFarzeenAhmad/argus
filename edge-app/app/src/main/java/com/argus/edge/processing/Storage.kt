package com.argus.edge.processing

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.io.File
import java.io.FileOutputStream

/**
 * The two folders on the processing client (docs/03, docs/04):
 *
 *   argus/processed/<session>/   the full local record — every inference, every camera,
 *                                annotated frames and crops. Stays on the phone.
 *   argus/helpful/               ONLY what the servers need, in contract format. The backend
 *                                consumer reads it over the local API and DELETEs what it took.
 *
 * Root is app-specific external storage (/sdcard/Android/data/com.argus.edge/files/argus), so
 * it needs no storage permission and `adb pull` works during development.
 */
class Storage(context: Context) {
    val root: File = File(context.getExternalFilesDir(null) ?: context.filesDir, "argus")
    val processedRoot = File(root, "processed")
    val helpful = File(root, "helpful")
    private val tmp = File(root, ".tmp")

    private val _helpfulPending = MutableStateFlow(0)
    val helpfulPending: StateFlow<Int> = _helpfulPending.asStateFlow()

    init {
        processedRoot.mkdirs(); helpful.mkdirs(); tmp.mkdirs()
        tmp.listFiles()?.forEach { it.delete() }
        refreshCount()
    }

    fun sessionDir(sessionId: String): File = File(processedRoot, sessionId).apply {
        File(this, "frames").mkdirs()
        File(this, "crops").mkdirs()
    }

    fun appendLine(file: File, line: String) {
        FileOutputStream(file, true).use { it.write((line + "\n").toByteArray(Charsets.UTF_8)) }
    }

    fun write(file: File, bytes: ByteArray) {
        file.parentFile?.mkdirs()
        file.writeBytes(bytes)
    }

    /**
     * Written to .tmp then renamed, so the API can never list a half-written file. For an
     * observation, write the evidence JPEG FIRST: a consumer that sees the JSON can rely on
     * its evidence already being there.
     */
    fun writeHelpful(name: String, bytes: ByteArray) {
        val t = File(tmp, "$name.part")
        t.writeBytes(bytes)
        if (!t.renameTo(File(helpful, name))) { t.delete(); error("rename failed for $name") }
        refreshCount()
    }

    /** Files the backend may consume, oldest first (names start with a sortable UTC timestamp). */
    fun listHelpful(): List<File> =
        helpful.listFiles { f -> f.isFile && NAME.matches(f.name) }?.sortedBy { it.name } ?: emptyList()

    fun helpfulFile(name: String): File? {
        if (!NAME.matches(name)) return null
        val f = File(helpful, name)
        return if (f.isFile && f.parentFile?.canonicalPath == helpful.canonicalPath) f else null
    }

    /** Deleting an observation JSON also deletes its evidence JPEG: consuming one consumes both. */
    fun deleteHelpful(name: String): Boolean {
        val f = helpfulFile(name) ?: return false
        val ok = f.delete()
        if (ok && name.endsWith(".json") && name.contains("-obs-")) {
            helpfulFile(name.removeSuffix(".json") + ".jpg")?.delete()
        }
        refreshCount()
        return ok
    }

    /** Pending = records awaiting the backend (JSON files; JPEGs ride with their observation). */
    fun refreshCount() {
        _helpfulPending.value = helpful.listFiles { f -> f.name.endsWith(".json") }?.size ?: 0
    }

    fun oldestHelpfulAgeS(now: Long): Long =
        listHelpful().firstOrNull { it.name.endsWith(".json") }?.let { ((now - it.lastModified()) / 1000).coerceAtLeast(0) } ?: 0

    companion object {
        val NAME = Regex("^[A-Za-z0-9._-]{1,128}$")
    }
}
