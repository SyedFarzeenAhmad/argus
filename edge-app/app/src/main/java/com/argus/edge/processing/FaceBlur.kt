package com.argus.edge.processing

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Rect
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.face.FaceDetection
import com.google.mlkit.vision.face.FaceDetectorOptions
import kotlinx.coroutines.tasks.await
import java.io.Closeable

/**
 * The privacy gate (docs/08): every frame is face-blurred BEFORE it is written anywhere.
 * Fails closed — if detection errors, the caller gets null and must not store the frame.
 */
class FaceBlur : Closeable {
    private val detector = FaceDetection.getClient(
        FaceDetectorOptions.Builder()
            .setPerformanceMode(FaceDetectorOptions.PERFORMANCE_MODE_FAST)
            .setMinFaceSize(0.03f)
            .build()
    )

    class Result(val bitmap: Bitmap, val faces: Int)

    suspend fun blur(src: Bitmap): Result? = runCatching {
        val faces = detector.process(InputImage.fromBitmap(src, 0)).await()
        val out = src.copy(Bitmap.Config.ARGB_8888, true)
        if (faces.isNotEmpty()) {
            val canvas = Canvas(out)
            val paint = Paint(Paint.FILTER_BITMAP_FLAG)
            for (f in faces) {
                val b = f.boundingBox
                val padX = (b.width() * 0.25f).toInt()
                val padY = (b.height() * 0.25f).toInt()
                val r = Rect(b.left - padX, b.top - padY, b.right + padX, b.bottom + padY)
                if (!r.intersect(0, 0, out.width, out.height) || r.width() < 2 || r.height() < 2) continue
                // Pixelate: down to a handful of blocks and back. Irreversible, unlike a light blur.
                val region = Bitmap.createBitmap(out, r.left, r.top, r.width(), r.height())
                val tiny = Bitmap.createScaledBitmap(region, (r.width() / 16).coerceAtLeast(1), (r.height() / 16).coerceAtLeast(1), false)
                val blocky = Bitmap.createScaledBitmap(tiny, r.width(), r.height(), false)
                canvas.drawBitmap(blocky, r.left.toFloat(), r.top.toFloat(), paint)
                region.recycle(); tiny.recycle(); blocky.recycle()
            }
        }
        Result(out, faces.size)
    }.getOrNull()

    override fun close() = detector.close()
}
