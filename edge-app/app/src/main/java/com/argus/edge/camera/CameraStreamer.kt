package com.argus.edge.camera

import android.graphics.Bitmap
import android.graphics.Matrix
import android.os.SystemClock
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import com.argus.edge.link.FrameClient
import java.io.ByteArrayOutputStream
import kotlin.math.abs
import kotlin.math.max

/**
 * CameraX analyzer for the camera role: throttle → upright → downscale → JPEG → FrameClient.
 * Encodes only while linked, so an idle camera phone stays cool.
 */
class CameraStreamer(
    private val client: FrameClient,
    private val targetFps: Int = 10,
    private val maxEdge: Int = 1280,
    private val jpegQuality: Int = 80,
) : ImageAnalysis.Analyzer {

    private var lastSentMs = 0L
    private val out = ByteArrayOutputStream(256 * 1024)

    override fun analyze(image: ImageProxy) {
        try {
            val nowMs = System.currentTimeMillis()
            if (!client.isStreaming || nowMs - lastSentMs < 1000L / targetFps) return
            lastSentMs = nowMs

            val captureMs = captureWallMs(image.imageInfo.timestamp, nowMs)
            val upright = upright(image.toBitmap(), image.imageInfo.rotationDegrees)

            out.reset()
            upright.compress(Bitmap.CompressFormat.JPEG, jpegQuality, out)
            client.offer(captureMs, out.toByteArray(), upright.width, upright.height)
            upright.recycle()
        } finally {
            image.close()
        }
    }

    private fun upright(src: Bitmap, rotation: Int): Bitmap {
        val scale = maxEdge.toFloat() / max(src.width, src.height)
        if (rotation == 0 && scale >= 1f) return src
        val m = Matrix().apply {
            if (scale < 1f) postScale(scale, scale)
            if (rotation != 0) postRotate(rotation.toFloat())
        }
        return Bitmap.createBitmap(src, 0, 0, src.width, src.height, m, true).also { if (it !== src) src.recycle() }
    }

    /**
     * The sensor timestamp is usually on the elapsedRealtime base; if so, convert it to wall
     * time. If the device uses another base, fall back to "now" (a few ms after capture).
     */
    private fun captureWallMs(sensorNs: Long, nowMs: Long): Long {
        val ageNs = SystemClock.elapsedRealtimeNanos() - sensorNs
        return if (abs(ageNs) < 1_000_000_000L) nowMs - ageNs / 1_000_000L else nowMs
    }
}
