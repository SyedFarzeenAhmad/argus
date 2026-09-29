package com.argus.edge.processing

import android.content.Context
import android.graphics.Bitmap
import android.media.MediaMetadataRetriever
import android.net.Uri
import com.argus.edge.link.FrameServer
import com.argus.edge.link.ReceivedFrame
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.io.ByteArrayOutputStream

/**
 * Plays a video file on the processing client AS IF it were a linked camera — same frame
 * server, same gate, same models (docs/03: "input is a URI, never a device"). For demos and
 * bench tests without a second phone. Loops, in real time.
 */
class VideoFileSource(
    private val context: Context,
    private val server: FrameServer,
    private val scope: CoroutineScope,
) {
    var cameraId: String? = null
        private set
    private var job: Job? = null

    val active: Boolean get() = job?.isActive == true

    /** [asCamera] is the camera_id the video stands in for — one not already linked. */
    fun start(uri: Uri, asCamera: String, fps: Int = 5) {
        stop()
        cameraId = asCamera
        server.registerTestSource(asCamera, "Test video")
        job = scope.launch(Dispatchers.IO) {
            val r = MediaMetadataRetriever()
            try {
                r.setDataSource(context, uri)
                val durationMs = r.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull() ?: 0L
                if (durationMs <= 0) return@launch
                val started = System.currentTimeMillis()
                var seq = 0L
                val out = ByteArrayOutputStream()
                while (isActive) {
                    val t0 = System.currentTimeMillis()
                    val posMs = (t0 - started) % durationMs
                    val frame = r.getFrameAtTime(posMs * 1000, MediaMetadataRetriever.OPTION_CLOSEST_SYNC)
                    if (frame != null) {
                        val scaled = downscale(frame, 1280)
                        out.reset()
                        scaled.compress(Bitmap.CompressFormat.JPEG, 85, out)
                        server.injectFrame(ReceivedFrame(asCamera, ++seq, t0, System.currentTimeMillis(), scaled.width, scaled.height, out.toByteArray()))
                        if (scaled !== frame) scaled.recycle()
                        frame.recycle()
                    }
                    delay((1000L / fps - (System.currentTimeMillis() - t0)).coerceAtLeast(10))
                }
            } finally {
                r.release()
            }
        }
    }

    fun stop() {
        job?.cancel()
        job = null
        cameraId?.let { server.unregisterTestSource(it) }
        cameraId = null
    }

    private fun downscale(b: Bitmap, maxEdge: Int): Bitmap {
        val s = maxEdge.toFloat() / maxOf(b.width, b.height)
        return if (s >= 1f) b else Bitmap.createScaledBitmap(b, (b.width * s).toInt(), (b.height * s).toInt(), true)
    }
}
