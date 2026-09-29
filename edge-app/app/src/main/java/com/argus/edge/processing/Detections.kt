package com.argus.edge.processing

import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/** A detection in ORIGINAL frame pixels. */
data class Detection(
    val x1: Float, val y1: Float, val x2: Float, val y2: Float,
    val score: Float,
    val classId: Int,
) {
    val width get() = x2 - x1
    val height get() = y2 - y1
    val area get() = max(0f, width) * max(0f, height)
}

/** Aspect-preserving resize into a square model input, padded — what YOLO was trained on. */
data class Letterbox(val scale: Float, val padX: Float, val padY: Float, val newW: Int, val newH: Int, val size: Int) {
    fun toOriginal(x: Float, y: Float): Pair<Float, Float> = ((x - padX) / scale) to ((y - padY) / scale)

    companion object {
        fun of(srcW: Int, srcH: Int, size: Int): Letterbox {
            val scale = min(size.toFloat() / srcW, size.toFloat() / srcH)
            val newW = (srcW * scale).roundToInt()
            val newH = (srcH * scale).roundToInt()
            return Letterbox(scale, (size - newW) / 2f, (size - newH) / 2f, newW, newH, size)
        }
    }
}

object YoloDecoder {
    /**
     * Decodes a YOLOv8/11 detect head, output shape [4 + numClasses][anchors] (no NMS baked in):
     * rows 0..3 = cx, cy, w, h in model-input pixels; rows 4.. = per-class scores.
     */
    fun decode(
        out: Array<FloatArray>,
        numClasses: Int,
        keepClasses: Set<Int>,
        confThreshold: Float,
        lb: Letterbox,
        srcW: Int,
        srcH: Int,
    ): List<Detection> {
        val anchors = out[0].size
        val found = ArrayList<Detection>()
        for (a in 0 until anchors) {
            var best = -1
            var bestScore = 0f
            for (c in 0 until numClasses) {
                val s = out[4 + c][a]
                if (s > bestScore) { bestScore = s; best = c }
            }
            if (best !in keepClasses || bestScore < confThreshold) continue
            val cx = out[0][a]; val cy = out[1][a]; val w = out[2][a]; val h = out[3][a]
            val (x1, y1) = lb.toOriginal(cx - w / 2, cy - h / 2)
            val (x2, y2) = lb.toOriginal(cx + w / 2, cy + h / 2)
            found += Detection(
                x1.coerceIn(0f, srcW.toFloat()), y1.coerceIn(0f, srcH.toFloat()),
                x2.coerceIn(0f, srcW.toFloat()), y2.coerceIn(0f, srcH.toFloat()),
                bestScore, best,
            )
        }
        return nms(found, 0.45f)
    }

    fun iou(a: Detection, b: Detection): Float {
        val ix = max(0f, min(a.x2, b.x2) - max(a.x1, b.x1))
        val iy = max(0f, min(a.y2, b.y2) - max(a.y1, b.y1))
        val inter = ix * iy
        val union = a.area + b.area - inter
        return if (union <= 0f) 0f else inter / union
    }

    /** Class-aware greedy NMS. */
    fun nms(dets: List<Detection>, iouThreshold: Float): List<Detection> {
        val sorted = dets.sortedByDescending { it.score }.toMutableList()
        val kept = ArrayList<Detection>()
        while (sorted.isNotEmpty()) {
            val top = sorted.removeAt(0)
            kept += top
            sorted.removeAll { it.classId == top.classId && iou(it, top) > iouThreshold }
        }
        return kept
    }
}
